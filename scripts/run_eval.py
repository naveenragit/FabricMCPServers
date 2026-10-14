"""Run the evaluation dataset through the Foundry agents and record every attempt (plan section 7).

Run: .\.venv\Scripts\python.exe scripts\run_eval.py --arms dax fabric --split dev --repeats 1 [--workers dax=4,fabric=8]
    [--judge-workers 16] [--no-evaluate] [--no-traces]
Each arm gets its own run under artifacts/runs/ and its own worker pool, so a slow arm does not hold back a fast one; the
arms run at the same time. Within an arm the questions that took longest in earlier runs start first. Each finished
attempt is scored right away (deterministic checks, the judge only where needed), and Foundry traces are joined at the
end. Re-running scripts/evaluate_runs.py later fills any gaps.
Arm 3 needs the proxy and the dev tunnel up (fabric-dataagent-mcp/scripts/serve-local.ps1, then devtunnel host).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential
from openai import APIError

from foundry_eval import RunStore
from foundry_eval.agents import ARMS, POLICY_V1, STUB_ROWS
from foundry_eval.dataset import Row, dataset_sha256, load_dataset
from foundry_eval.evaluators import all_evaluators, config_from_env
from foundry_eval.evaluators.judges import FoundryJudge
from foundry_eval.evaluators.runner import evaluate_attempt
from foundry_eval.net import install_dns_retry
from foundry_eval.records import AttemptRecord, StepRecord

install_dns_retry()

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "artifacts" / "runs"
HARNESS_VERSION = "0.2"
FULL_OUTPUT = 400_000  # keep whole tool outputs so evaluators can trace figures
RETRY_BACKOFF_S = (20, 60, 120)  # infrastructure errors (rate limit, tunnel, connection) are retried; every try is recorded
# Arm 1 is bound by agent-model tokens and hit deployment server errors at 8 workers. Arm 3 waits on the Data Agent,
# which rejected every call after ~25 answers at 10 at once (full run 2026-10-14) though a single burst of 10 was fine.
DEFAULT_WORKERS = {"dax": 4, "sql": 4, "fabric": 4}
TRACE_WAITS_S = (60, 60, 120)  # spans reach Application Insights a few minutes after the response


def load_env() -> None:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()


def parse_workers(text: str) -> dict[str, int]:
    workers = dict(DEFAULT_WORKERS)
    for part in filter(None, (p.strip() for p in text.split(","))):
        arm, _, count = part.partition("=")
        if arm not in ARMS or not count.isdigit() or int(count) < 1:
            raise argparse.ArgumentTypeError(f"bad --workers entry {part!r}; use arm=N, for example dax=4,fabric=8")
        workers[arm] = int(count)
    return workers


def prior_durations(arm: str) -> dict[str, float]:
    """Mean completed duration per question in earlier runs of this arm, so the slowest questions start first."""
    seen: dict[str, list[float]] = defaultdict(list)
    for path in RUNS.glob(f"{arm}-*/attempts.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            attempt = json.loads(line)
            if attempt.get("status") == "completed" and attempt.get("duration_s"):
                seen[attempt["question_id"]].append(attempt["duration_s"])
    return {qid: sum(v) / len(v) for qid, v in seen.items()}


def join_traces(stores: list[RunStore]) -> None:
    app_id = os.environ.get("FOUNDRY_APPINSIGHTS_APP_ID")
    if not app_id:
        print("traces: FOUNDRY_APPINSIGHTS_APP_ID is not set, skipped", flush=True)
        return
    from foundry_eval.traces import enrich_run

    missing = 0
    for pause in (*TRACE_WAITS_S, None):
        missing = sum(enrich_run(store, app_id)[1] for store in stores)
        if not missing:
            print("traces: joined for every attempt", flush=True)
            return
        if pause is None:
            break
        print(f"traces: {missing} attempts have no spans yet, retrying in {pause}s", flush=True)
        time.sleep(pause)
    ids = " ".join(s.run.run_id for s in stores)
    print(f"traces: {missing} attempts still without spans; re-run later with python -m foundry_eval traces artifacts/runs <run_id> ({ids})", flush=True)


def preflight_proxy() -> None:
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    async def probe() -> list[str]:
        async with Client(streamable_http_client(os.environ["FABRIC_PROXY_MCP_URL"]), read_timeout_seconds=40, cache=None) as c:
            return [t.name for t in (await c.list_tools()).tools]

    try:
        tools = asyncio.run(asyncio.wait_for(probe(), 60))
    except Exception as exc:
        sys.exit(f"arm 3 proxy is not reachable through the tunnel ({type(exc).__name__}). Start serve-local.ps1 and devtunnel host.")
    print(f"arm 3 proxy reachable, tools: {tools}")


def preflight_stub() -> None:
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    async def probe() -> list[str]:
        async with Client(streamable_http_client(os.environ["INJECTION_STUB_URL"]), read_timeout_seconds=40, cache=None) as c:
            return [t.name for t in (await c.list_tools()).tools]

    try:
        tools = asyncio.run(asyncio.wait_for(probe(), 60))
    except Exception as exc:
        sys.exit(f"the R12 stub is not reachable through the tunnel ({type(exc).__name__}). Start scripts\\serve_stubs.py.")
    print(f"R12 stub reachable, tools: {tools}")


def stub_environment(project: AIProjectClient, arm: str) -> dict[str, str]:
    stub = ARMS[arm].stub_agent_name
    version = project.agents.get(stub).as_dict()["versions"]["latest"]
    return {"r12_agent": stub, "r12_agent_version": str(version["version"])}


def make_store(project: AIProjectClient, arm_key: str, args: argparse.Namespace, dataset: Path, with_stub: bool) -> RunStore:
    arm = ARMS[arm_key]
    latest = project.agents.get(arm.agent_name).as_dict()["versions"]["latest"]
    instructions = (latest.get("definition") or {}).get("instructions") or ""
    environment = (
        {"arm3_hosting": "local proxy behind a dev tunnel", "arm3_identity": "signed-in user (azure cli)"}
        if arm_key == "fabric"
        else {}
    )
    if with_stub:
        environment |= stub_environment(project, arm_key)
    return RunStore.create(
        RUNS,
        arm=arm_key,
        batch_id=args.batch,
        agent_name=arm.agent_name,
        agent_version=str(latest["version"]),
        agent_model=os.environ.get("FOUNDRY_AGENT_MODEL"),
        reasoning_effort=os.environ.get("FOUNDRY_AGENT_REASONING_EFFORT") or None,
        judge_model=os.environ.get("FOUNDRY_JUDGE_MODEL") or None,
        instructions_sha256=hashlib.sha256(instructions.encode()).hexdigest(),
        policy_version=f"v1:{hashlib.sha256(POLICY_V1.encode()).hexdigest()[:8]}",
        tools=[f"{arm.server_label}:{t}" for t in arm.allowed_tools],
        dataset_name=dataset.name,
        dataset_sha256=dataset_sha256(dataset),
        dataset_split=args.split,
        repeats=args.repeats,
        concurrency=args.workers[arm_key],
        git_sha=git("rev-parse", "HEAD") or None,
        git_dirty=bool(git("status", "--porcelain")),
        harness_version=HARNESS_VERSION,
        environment=environment,
    )


def run_attempt(client, store: RunStore, row: Row, repeat: int) -> tuple[AttemptRecord, list[StepRecord]]:
    arm = store.run.arm
    agent = ARMS[arm].stub_agent_name if row.id in STUB_ROWS else ARMS[arm].agent_name
    version = store.run.environment.get("r12_agent_version") if row.id in STUB_ROWS else store.run.agent_version
    retry_of = None
    for tries in range(len(RETRY_BACKOFF_S) + 1):
        recorder = store.recorder(
            question_id=row.id,
            question=row.query,
            repeat_index=repeat,
            retry_of=retry_of,
            agent_name=agent,
            agent_version=version,
            preview_chars=FULL_OUTPUT,
        )
        try:
            stream = client.responses.create(
                input=row.query,
                stream=True,
                extra_body={"agent_reference": {"name": agent, "type": "agent_reference"}},
            )
            for event in stream:
                recorder.on_event(event.model_dump(mode="json"))
        except APIError as exc:
            recorder.fail(exc)
        except Exception as exc:
            recorder.fail(exc)
        attempt, steps = recorder.build()
        store.add_attempt(attempt, steps)
        if attempt.infra_error and tries < len(RETRY_BACKOFF_S):
            retry_of = attempt.attempt_id
            time.sleep(RETRY_BACKOFF_S[tries])
            continue
        break
    return attempt, steps


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", nargs="+", default=["dax", "fabric"], choices=sorted(ARMS))
    parser.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    parser.add_argument("--ids", help="comma-separated row ids")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--workers", type=parse_workers, default=dict(DEFAULT_WORKERS), help="per-arm agent workers, e.g. dax=4,fabric=8")
    parser.add_argument("--judge-workers", type=int, default=16)
    parser.add_argument("--no-evaluate", action="store_true", help="record attempts only; score later with evaluate_runs.py")
    parser.add_argument("--no-traces", action="store_true")
    parser.add_argument("--batch", default=time.strftime("%Y%m%dT%H%M%S"))
    args = parser.parse_args()

    load_env()
    args.repeats = args.repeats or int(os.environ.get("EVAL_REPEATS", "3"))
    dataset = ROOT / "evals" / "eval-dataset.jsonl"
    rows = load_dataset(dataset)
    if args.ids:
        wanted = {i.strip().upper() for i in args.ids.split(",")}
        rows = [r for r in rows if r.id in wanted]
    elif args.split != "all":
        rows = [r for r in rows if r.split == args.split]
    if args.limit:
        rows = rows[: args.limit]

    if "fabric" in args.arms:
        preflight_proxy()
    with_stub = any(r.id in STUB_ROWS and set(r.applies_to) & set(args.arms) for r in rows)
    if with_stub:
        preflight_stub()
    project = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=AzureCliCredential())
    client = project.get_openai_client()
    stores = {arm: make_store(project, arm, args, dataset, with_stub) for arm in args.arms}

    work = {
        arm: [(row, repeat) for row in rows for repeat in range(args.repeats) if arm in row.applies_to] for arm in args.arms
    }
    for arm, items in work.items():
        durations = prior_durations(arm)
        items.sort(key=lambda item, d=durations: -d.get(item[0].id, float("inf")))  # unseen questions first, then slowest
    total = sum(len(items) for items in work.values())
    shape = ", ".join(f"{arm}={args.workers[arm]}" for arm in args.arms)
    print(f"{total} attempts: {len(rows)} rows x {args.repeats} repeats x arms {args.arms}; workers {shape}", flush=True)
    for store in stores.values():
        print(f"  run {store.run.run_id}", flush=True)

    evaluators = config = judge_model = None
    if not args.no_evaluate:
        judge_model = os.environ["FOUNDRY_JUDGE_MODEL"]
        evaluators = all_evaluators(FoundryJudge(client, judge_model, "medium"))
        config = config_from_env(os.environ)
    judge_pool = ThreadPoolExecutor(max_workers=args.judge_workers)
    lock, counts = threading.Lock(), {"done": 0, "scored": 0, "score_errors": 0}

    def score(store: RunStore, attempt: AttemptRecord, steps: list[StepRecord], row: Row) -> None:
        try:
            store.add_evaluations(evaluate_attempt(attempt, steps, row, evaluators, config, judge_model=judge_model))
            key = "scored"
        except Exception as exc:  # a scoring failure must not stop the run; evaluate_runs.py fills the gap
            print(f"  scoring failed for {attempt.arm} {attempt.question_id}: {type(exc).__name__}: {exc}", flush=True)
            key = "score_errors"
        with lock:
            counts[key] += 1

    def job(arm: str, row: Row, repeat: int) -> None:
        attempt, steps = run_attempt(client, stores[arm], row, repeat)
        if evaluators is not None and not attempt.infra_error:  # infrastructure errors are not scored
            judge_pool.submit(score, stores[arm], attempt, steps, row)
        flag = "TOOL-ERROR" if attempt.tool_error else attempt.status
        with lock:
            counts["done"] += 1
            print(f"[{counts['done']}/{total}] {arm} {row.id} r{repeat}: {flag} {attempt.duration_s:.0f}s", flush=True)

    started = time.monotonic()
    pools = {arm: ThreadPoolExecutor(max_workers=args.workers[arm]) for arm in args.arms}
    futures = [pools[arm].submit(job, arm, row, repeat) for arm, items in work.items() for row, repeat in items]
    wait(futures)
    for pool in pools.values():
        pool.shutdown()
    for future in futures:
        future.result()
    agents_s = time.monotonic() - started
    judge_pool.shutdown(wait=True)
    print(
        f"agents finished in {agents_s / 60:.1f} min; scoring finished {(time.monotonic() - started - agents_s) / 60:.1f} min later"
        f" ({counts['scored']} attempts scored, {counts['score_errors']} scoring failures)",
        flush=True,
    )

    for store in stores.values():
        run = store.finish()
        arm = store.run.arm
        print(f"{arm}: {run.status} attempts={run.n_attempts} completed={run.n_completed} failed={run.n_failed} infra={run.n_infra_errors} tool_errors={run.n_tool_errors}")
    if not args.no_traces:
        join_traces(list(stores.values()))
    ids = " ".join(s.run.run_id for s in stores.values())
    print(f"total {(time.monotonic() - started) / 60:.1f} min. Scorecard: scripts/scorecard.py {ids} --out artifacts/scorecards/{args.batch}.md", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
