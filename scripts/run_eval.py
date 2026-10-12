"""Run the evaluation dataset through the Foundry agents and record every attempt (plan section 7).

Run: .\.venv\Scripts\python.exe scripts\run_eval.py --arms dax fabric --split dev --repeats 1
Each arm gets its own run under artifacts/runs/. Arms are interleaved so none is favored by time of day or load.
Arm 3 needs the proxy and the dev tunnel up (fabric-dataagent-mcp/scripts/serve-local.ps1, then devtunnel host).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential
from foundry_eval import RunStore
from foundry_eval.agents import ARMS, POLICY_V1
from foundry_eval.dataset import Row, dataset_sha256, load_dataset
from foundry_eval.net import install_dns_retry
from openai import APIError

install_dns_retry()

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "artifacts" / "runs"
HARNESS_VERSION = "0.1"
FULL_OUTPUT = 400_000  # keep whole tool outputs so evaluators can trace figures


def load_env() -> None:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()


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


def make_store(project: AIProjectClient, arm_key: str, args: argparse.Namespace, dataset: Path) -> RunStore:
    arm = ARMS[arm_key]
    latest = project.agents.get(arm.agent_name).as_dict()["versions"]["latest"]
    instructions = (latest.get("definition") or {}).get("instructions") or ""
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
        concurrency=args.concurrency,
        git_sha=git("rev-parse", "HEAD") or None,
        git_dirty=bool(git("status", "--porcelain")),
        harness_version=HARNESS_VERSION,
        environment={"arm3_hosting": "local proxy behind a dev tunnel", "arm3_identity": "signed-in user (azure cli)"}
        if arm_key == "fabric"
        else {},
    )


def run_attempt(client, store: RunStore, row: Row, repeat: int) -> tuple[str, str, int, str]:
    arm = store.run.arm
    retry_of = None
    for tries in range(2):
        recorder = store.recorder(
            question_id=row.id, question=row.query, repeat_index=repeat, retry_of=retry_of, preview_chars=FULL_OUTPUT
        )
        try:
            stream = client.responses.create(
                input=row.query,
                stream=True,
                extra_body={"agent_reference": {"name": ARMS[arm].agent_name, "type": "agent_reference"}},
            )
            for event in stream:
                recorder.on_event(event.model_dump(mode="json"))
        except APIError as exc:
            recorder.fail(exc)
        except Exception as exc:
            recorder.fail(exc)
        attempt = store.add(recorder)
        if attempt.infra_error and tries == 0:
            retry_of = attempt.attempt_id
            time.sleep(8)
            continue
        break
    flag = "TOOL-ERROR" if attempt.tool_error else attempt.status
    return arm, row.id, repeat, f"{flag} {attempt.duration_s:.0f}s"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", nargs="+", default=["dax", "fabric"], choices=sorted(ARMS))
    parser.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    parser.add_argument("--ids", help="comma-separated row ids")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--concurrency", type=int, default=4)
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
    project = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=AzureCliCredential())
    client = project.get_openai_client()
    stores = {arm: make_store(project, arm, args, dataset) for arm in args.arms}

    work = [
        (arm, row, repeat)
        for row in rows
        for repeat in range(args.repeats)
        for arm in args.arms
        if arm in row.applies_to
    ]
    print(f"{len(work)} attempts: {len(rows)} rows x {args.repeats} repeats x arms {args.arms}; concurrency {args.concurrency}", flush=True)
    for store in stores.values():
        print(f"  run {store.run.run_id}", flush=True)

    lock, done = threading.Lock(), 0

    def job(item):
        nonlocal done
        arm, row, repeat = item
        result = run_attempt(client, stores[arm], row, repeat)
        with lock:
            done += 1
            print(f"[{done}/{len(work)}] {result[0]} {result[1]} r{result[2]}: {result[3]}", flush=True)

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        list(pool.map(job, work))

    for store in stores.values():
        run = store.finish()
        arm = store.run.arm
        print(f"{arm}: {run.status} attempts={run.n_attempts} completed={run.n_completed} failed={run.n_failed} infra={run.n_infra_errors} tool_errors={run.n_tool_errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
