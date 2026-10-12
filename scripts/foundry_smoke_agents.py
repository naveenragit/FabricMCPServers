"""Stream one question through each named agent and record it as a run (G2 smoke).

Run: .\.venv\Scripts\python.exe scripts\foundry_smoke_agents.py wm-dax-agent [wm-sql-agent ...] [--q "question"]
Records go to artifacts/runs/<run_id>/; raw events to artifacts/foundry-smoke/<agent>.jsonl (both gitignored).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential
from foundry_eval import RunStore
from foundry_eval.agents import ARMS
from openai import APIError

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("agents", nargs="+")
    parser.add_argument("--q", default="How many clients do we have?")
    parser.add_argument("--question-id", default="smoke")
    args = parser.parse_args()

    load_env()
    project = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=AzureCliCredential())
    client = project.get_openai_client()
    raw_dir = ROOT / "artifacts" / "foundry-smoke"
    raw_dir.mkdir(parents=True, exist_ok=True)
    arm_by_agent = {arm.agent_name: arm for arm in ARMS.values()}

    for name in args.agents:
        arm = arm_by_agent[name]
        store = RunStore.create(
            ROOT / "artifacts" / "runs",
            arm=arm.key,
            agent_name=name,
            agent_model=os.environ.get("FOUNDRY_AGENT_MODEL"),
            reasoning_effort=os.environ.get("FOUNDRY_AGENT_REASONING_EFFORT") or None,
            judge_model=os.environ.get("FOUNDRY_JUDGE_MODEL") or None,
            tools=[f"{arm.server_label}:{tool}" for tool in arm.allowed_tools],
            dataset_name="smoke",
        )
        print(f"\n===== {name} run={store.run.run_id}: {args.q}")
        recorder = store.recorder(question_id=args.question_id, question=args.q)
        started = time.monotonic()
        with (raw_dir / f"{name}.jsonl").open("w", encoding="utf-8") as dump:
            try:
                stream = client.responses.create(
                    input=args.q,
                    stream=True,
                    extra_body={"agent_reference": {"name": name, "type": "agent_reference"}},
                )
                for event in stream:
                    data = event.model_dump(mode="json")
                    data["_t"] = round(time.monotonic() - started, 3)
                    dump.write(json.dumps(data, default=str) + "\n")
                    recorder.on_event(data)
            except APIError as exc:
                recorder.fail(exc)
        attempt = store.add(recorder)
        run = store.finish()

        print(f"status={attempt.status} duration={attempt.duration_s:.1f}s tokens_in={attempt.input_tokens}")
        print(f"tool_error={attempt.tool_error} tool={attempt.tool_error_tool} message={attempt.tool_error_message}")
        print(f"tool_calls={attempt.tool_calls} failed={attempt.tool_calls_failed} response_id={attempt.response_id}")
        for line in (store.directory / "steps.jsonl").read_text(encoding="utf-8").splitlines():
            step = json.loads(line)
            if step["kind"] == "tool_call":
                print(f"  {step['tool_name']} status={step['tool_status']} query={str(step['query_text'])[:120]}")
        print("answer:", (attempt.answer_text or "")[:500])
        print(f"run: status={run.status} n_tool_errors={run.n_tool_errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
