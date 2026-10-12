"""Create the project connections and agent versions for the MCP-backed arms.

Run: .\.venv\Scripts\python.exe scripts\foundry_create_agents.py [--arms dax sql] [--stub]
Connections are created through ARM (the SDK has no create). Names are written back to .env.
--stub creates the R12 test agents instead: same instructions and tools, pointed at INJECTION_STUB_URL.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

import requests
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPTool, PromptAgentDefinition, Reasoning
from azure.identity import AzureCliCredential

from foundry_eval.agents import ARMS, build_instructions

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_CONNECTION = {"dax": "wm-semantic-mcp", "sql": "wm-onelake-mcp", "fabric": "wm-fabric-proxy-mcp"}
ARM_API = "2025-04-01-preview"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def set_env(key: str, value: str) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.partition("=")[0].strip() == key:
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value


def require(key: str) -> str:
    value = os.environ.get(key, "")
    if not value:
        sys.exit(f"{key} is not set in .env")
    return value


def put_connection(credential: AzureCliCredential, name: str, target: str) -> None:
    token = credential.get_token("https://management.azure.com/.default").token
    url = f"https://management.azure.com{require('FOUNDRY_PROJECT_RESOURCE_ID')}/connections/{name}"
    body = {
        "properties": {
            "authType": "None",
            "category": "RemoteTool",
            "group": "GenericProtocol",
            "target": target,
            "isSharedToAll": False,
            "metadata": {"type": "custom_MCP"},
        }
    }
    resp = requests.put(
        url, params={"api-version": ARM_API}, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=60
    )
    if not resp.ok:
        sys.exit(f"connection PUT failed {resp.status_code}: {resp.text}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", nargs="+", default=["dax", "sql"], choices=sorted(ARMS))
    parser.add_argument("--stub", action="store_true", help="create the R12 stub agents instead of the real ones")
    args = parser.parse_args()

    load_env()
    if not os.environ.get("EVAL_CANARY"):
        set_env("EVAL_CANARY", f"CANARY-{secrets.token_hex(8)}")
    credential = AzureCliCredential()
    project = AIProjectClient(endpoint=require("FOUNDRY_PROJECT_ENDPOINT"), credential=credential)
    model = require("FOUNDRY_AGENT_MODEL")
    effort = os.environ.get("FOUNDRY_AGENT_REASONING_EFFORT") or None

    for key in args.arms:
        arm = ARMS[key]
        if args.stub:
            url = require("INJECTION_STUB_URL")
            connection, agent_name = arm.stub_connection_name, arm.stub_agent_name
        else:
            url = require(arm.url_env)
            connection = os.environ.get(arm.connection_env) or DEFAULT_CONNECTION[key]
            agent_name = arm.agent_name
        put_connection(credential, connection, url)
        if not args.stub:
            set_env(arm.connection_env, connection)

        instructions = build_instructions(
            arm, os.environ["EVAL_CANARY"], os.environ.get("SEMANTIC_MODEL_ID") if arm.needs_model_id else None
        )
        tool = MCPTool(
            server_label=arm.server_label,
            server_url=url,
            project_connection_id=connection,
            allowed_tools=list(arm.allowed_tools),
            require_approval="never",
        )
        definition = PromptAgentDefinition(
            model=model,
            instructions=instructions,
            tools=[tool],
            reasoning=Reasoning(effort=effort) if effort else None,
        )
        version = project.agents.create_version(agent_name=agent_name, definition=definition)
        print(f"{agent_name}: version {version.version} (connection {connection})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
