"""Concurrency test: how many simultaneous questions each arm's Foundry agent handles, from 1 to 10 at a time.

Separate from the evaluation runs: nothing is written under artifacts/runs, nothing is retried, and nothing is judged.
Each arm is tested on its own, one arm after another. At each level N, N workers keep N questions in flight until
N x --rounds questions have been asked, so the load is sustained, not a single burst. A cooldown between levels lets
any throttling clear. A call counts as a success when it completes with an answer and no tool or infrastructure error;
the numeric check (no LLM) also reports whether the expected figures are in the answer.

Run: .\.venv\Scripts\python.exe scripts\concurrency_test.py --arms dax sql fabric [--levels 1-10] [--rounds 3]
    [--ids Q01,Q07,Q12,Q19,Q31] [--cooldown 30] [--keep-going]
Arm 3 needs the proxy and the dev tunnel up. Results: artifacts/concurrency/<batch>/<arm>.jsonl and summary.md.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_eval  # noqa: E402  (preflights, env loading, DNS retry)

from foundry_eval.agents import ARMS  # noqa: E402
from foundry_eval.dataset import load_dataset  # noqa: E402
from foundry_eval.evaluators import SCHEMA_TOOLS, config_from_env  # noqa: E402
from foundry_eval.evaluators.base import Case  # noqa: E402
from foundry_eval.evaluators.deterministic import numeric_audit  # noqa: E402
from foundry_eval.recorder import AttemptRecorder  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "concurrency"


def parse_levels(text: str) -> list[int]:
    if "-" in text:
        low, high = (int(x) for x in text.split("-", 1))
        levels = list(range(low, high + 1))
    else:
        levels = [int(x) for x in text.split(",") if x.strip()]
    if not levels or min(levels) < 1 or max(levels) > 10:
        raise argparse.ArgumentTypeError("levels must be between 1 and 10, e.g. 1-10 or 1,2,4,8")
    return levels


def ask(client, arm: str, agent: str, version: str, row, index: int, config) -> dict:
    recorder = AttemptRecorder(
        run_id="concurrency-test", arm=arm, question_id=row.id, question=row.query, repeat_index=index,
        agent_name=agent, agent_version=version, schema_tools=SCHEMA_TOOLS.get(arm, []), preview_chars=400_000,
    )
    try:
        stream = client.responses.create(
            input=row.query, stream=True, extra_body={"agent_reference": {"name": agent, "type": "agent_reference"}}
        )
        for event in stream:
            recorder.on_event(event.model_dump(mode="json"))
    except Exception as exc:
        recorder.fail(exc)
    attempt, steps = recorder.build()
    ok = attempt.status == "completed" and not attempt.tool_error and not attempt.infra_error
    audit = numeric_audit(Case(row=row, attempt=attempt, steps=steps, config=config))
    return {
        "question_id": row.id,
        "status": attempt.status,
        "ok": ok,
        "duration_s": round(attempt.duration_s, 2),
        "tool_error": attempt.tool_error,
        "infra_error": attempt.infra_error,
        "error": (attempt.tool_error_message or attempt.error_message or "")[:200] or None,
        "figures_found": audit.passed if audit.status == "ok" else None,
        "input_tokens": attempt.input_tokens,
    }


def run_level(client, arm: str, agent: str, version: str, rows: list, level: int, rounds: int, config) -> dict:
    jobs = [(rows[i % len(rows)], i) for i in range(level * rounds)]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=level) as pool:
        results = list(pool.map(lambda job: ask(client, arm, agent, version, job[0], job[1], config), jobs))
    wall = time.monotonic() - started
    durations = [r["duration_s"] for r in results if r["ok"]]
    errors: dict[str, int] = {}
    for r in results:
        if not r["ok"]:
            key = (r["error"] or r["status"])[:80]
            errors[key] = errors.get(key, 0) + 1
    audited = [r["figures_found"] for r in results if r["ok"] and r["figures_found"] is not None]
    return {
        "arm": arm,
        "level": level,
        "calls": len(results),
        "ok": sum(r["ok"] for r in results),
        "success_rate": sum(r["ok"] for r in results) / len(results),
        "p50_s": round(statistics.median(durations), 1) if durations else None,
        "p95_s": round(sorted(durations)[math.ceil(0.95 * len(durations)) - 1], 1) if durations else None,
        "wall_s": round(wall, 1),
        "calls_per_min": round(len(results) / wall * 60, 1),
        "figures_found_rate": round(sum(audited) / len(audited), 2) if audited else None,
        "errors": errors,
        "results": results,
    }


def summary_table(levels: list[dict]) -> str:
    lines = [
        "| arm | level | calls | ok | success | p50 s | p95 s | wall s | calls/min | figures found | errors |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for lv in levels:
        errs = "; ".join(f"{k} x{v}" for k, v in lv["errors"].items()) or "-"
        lines.append(
            f"| {lv['arm']} | {lv['level']} | {lv['calls']} | {lv['ok']} | {lv['success_rate']:.0%} | {lv['p50_s']} | {lv['p95_s']}"
            f" | {lv['wall_s']} | {lv['calls_per_min']} | {lv['figures_found_rate']} | {errs} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", nargs="+", default=["dax", "sql", "fabric"], choices=sorted(ARMS))
    parser.add_argument("--levels", type=parse_levels, default=list(range(1, 11)), help="1-10 or a list such as 1,2,4,8")
    parser.add_argument("--rounds", type=int, default=3, help="questions per worker at each level (sustained load)")
    parser.add_argument("--ids", default="Q01,Q07,Q12,Q19,Q31", help="questions to cycle through")
    parser.add_argument("--cooldown", type=int, default=30, help="seconds between levels so throttling can clear")
    parser.add_argument("--keep-going", action="store_true", help="continue to higher levels after a level has failures")
    parser.add_argument("--batch", default=time.strftime("%Y%m%dT%H%M%S"))
    args = parser.parse_args()

    run_eval.load_env()
    if "fabric" in args.arms:
        run_eval.preflight_proxy()
    if "sql" in args.arms:
        run_eval.preflight_sql()
    wanted = [i.strip().upper() for i in args.ids.split(",") if i.strip()]
    by_id = {r.id: r for r in load_dataset(ROOT / "evals" / "eval-dataset.jsonl")}
    rows = [by_id[i] for i in wanted]
    config = config_from_env(os.environ)
    project = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=AzureCliCredential())
    client = project.get_openai_client()
    out = OUT / args.batch
    out.mkdir(parents=True, exist_ok=True)

    all_levels: list[dict] = []
    for arm in args.arms:
        agent = ARMS[arm].agent_name
        version = str(project.agents.get(agent).as_dict()["versions"]["latest"]["version"])
        print(f"== {arm}: {agent} v{version}; levels {args.levels[0]}-{args.levels[-1]}, {args.rounds} questions per worker", flush=True)
        warm = ask(client, arm, agent, version, rows[0], -1, config)  # cold servers would skew level 1
        print(f"   warm-up: {'ok' if warm['ok'] else 'FAILED'} {warm['duration_s']}s", flush=True)
        for n, level in enumerate(args.levels):
            if n:
                time.sleep(args.cooldown)
            result = run_level(client, arm, agent, version, rows, level, args.rounds, config)
            all_levels.append(result)
            with (out / f"{arm}.jsonl").open("a", encoding="utf-8") as fh:
                for r in result["results"]:
                    fh.write(json.dumps({"arm": arm, "level": level, **r}) + "\n")
            errs = ", ".join(f"{k} x{v}" for k, v in result["errors"].items()) or "none"
            print(
                f"   level {level:>2}: {result['ok']}/{result['calls']} ok, p50 {result['p50_s']}s, p95 {result['p95_s']}s,"
                f" {result['calls_per_min']} calls/min; errors: {errs}",
                flush=True,
            )
            if result["ok"] < result["calls"] and not args.keep_going:
                print(f"   stopping {arm} at level {level} (use --keep-going to test higher levels anyway)", flush=True)
                break

    lines = ["# Concurrency test", "", f"Batch {args.batch}. Questions: {', '.join(wanted)}; {args.rounds} per worker per level; cooldown {args.cooldown}s.", ""]
    for arm in args.arms:
        levels = [lv for lv in all_levels if lv["arm"] == arm]
        clean = [lv["level"] for lv in levels if lv["ok"] == lv["calls"]]
        best = max(clean) if clean else None
        tested = max(lv["level"] for lv in levels) if levels else None
        lines.append(f"- **{arm}**: highest level with every call ok: {best} (tested up to {tested}).")
    lines += ["", summary_table(all_levels), ""]
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[4 : 4 + len(args.arms)]))
    print(f"summary -> {(out / 'summary.md').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
