"""Serve the R12 injection stub on port 8001: copies tool definitions from the real servers, then serves the stub.

Run: .\.venv\Scripts\python.exe scripts\serve_stubs.py
Reads INJECTION_STUB_URL from .env (its host goes into the Host-header allowlist). Needs no backend and holds no data.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import uvicorn

from foundry_eval.net import install_dns_retry
from foundry_eval.stub_server import build_app, fetch_tools

install_dns_retry()
ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


async def collect() -> list:
    from fabric_dataagent_mcp.server import TOOL as FABRIC_TOOL

    tools = {FABRIC_TOOL.name: FABRIC_TOOL}
    for env in ("SEMANTIC_MCP_URL", "ONELAKE_MCP_URL"):
        for tool in await fetch_tools(os.environ[env]):
            tools[tool.name] = tool
    return list(tools.values())


def main() -> int:
    load_env()
    url = os.environ.get("INJECTION_STUB_URL")
    if not url:
        sys.exit("INJECTION_STUB_URL is not set in .env")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    host = urlparse(url).netloc
    tools = asyncio.run(collect())
    print("stub tools:", sorted(t.name for t in tools), flush=True)
    app = build_app(
        tools,
        allowed_hosts=[host, "localhost:*", "127.0.0.1:*"],
        allowed_origins=[f"https://{host}", "http://localhost:*", "http://127.0.0.1:*"],
    )
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
