"""Read-only probe of the Foundry project: connections, agents, and one throwaway model call.

Run: .\.venv\Scripts\python.exe scripts\foundry_probe.py [--effort low|medium|high|xhigh|none]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential


def load_env(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--effort", default=None)
    args = parser.parse_args()

    load_env(Path(__file__).resolve().parents[1] / ".env")
    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    model = os.environ["FOUNDRY_AGENT_MODEL"]

    project = AIProjectClient(endpoint=endpoint, credential=AzureCliCredential())

    print("== connections ==")
    for conn in project.connections.list():
        print(f"  {conn.name}  type={conn.type}  target={getattr(conn, 'target', None)}")

    print("== agents ==")
    for agent in project.agents.list():
        print(f"  {agent.name}")

    print(f"== model call: {model} effort={args.effort} ==")
    client = project.get_openai_client()
    kwargs: dict = {"model": model, "input": "Reply with the single word OK."}
    if args.effort:
        kwargs["reasoning"] = {"effort": args.effort}
    response = client.responses.create(**kwargs)
    print("  output_text:", response.output_text)
    print("  usage:", response.usage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
