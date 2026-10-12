"""Stream one question through each named agent and dump the raw event sequence (G2 smoke).

Run: .\.venv\Scripts\python.exe scripts\foundry_smoke_agents.py wm-dax-agent [wm-sql-agent ...] [--q "question"]
Raw events go to artifacts/foundry-smoke/<agent>.jsonl (gitignored).
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
from openai import APIError

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def show_completed(response: dict) -> None:
    print("status:", response.get("status"), "usage:", response.get("usage"))
    for item in response.get("output", []):
        kind = item.get("type")
        if kind == "mcp_call":
            print(f"  mcp_call {item.get('name')} err={item.get('error')}")
            print(f"    args: {str(item.get('arguments'))[:300]}")
            print(f"    out:  {str(item.get('output'))[:300]}")
        elif kind == "message":
            text = "".join(c.get("text", "") for c in item.get("content", []))
            print("  answer:", text[:800])
        else:
            print(f"  [{kind}]")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("agents", nargs="+")
    parser.add_argument("--q", default="How many clients do we have?")
    args = parser.parse_args()

    load_env()
    project = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=AzureCliCredential())
    client = project.get_openai_client()
    out_dir = ROOT / "artifacts" / "foundry-smoke"
    out_dir.mkdir(parents=True, exist_ok=True)

    for name in args.agents:
        print(f"\n===== {name}: {args.q}")
        started = time.monotonic()
        stream = client.responses.create(
            input=args.q,
            stream=True,
            extra_body={"agent_reference": {"name": name, "type": "agent_reference"}},
        )
        order: list[str] = []
        with (out_dir / f"{name}.jsonl").open("w", encoding="utf-8") as dump:
            try:
                for event in stream:
                    data = event.model_dump(mode="json")
                    data["_t"] = round(time.monotonic() - started, 3)
                    dump.write(json.dumps(data, default=str) + "\n")
                    if not order or order[-1] != event.type:
                        order.append(event.type)
                    if event.type == "response.completed":
                        show_completed(data["response"])
            except APIError as exc:
                print(f"STREAM ERROR ({type(exc).__name__}): {str(exc)[:500]}")
        print("events:", " > ".join(order))
        print(f"elapsed: {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
