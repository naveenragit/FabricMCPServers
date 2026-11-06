"""Serve over stdio or Streamable HTTP, or probe the upstream endpoint with the configured identity."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import sys

import uvicorn
from mcp.server.stdio import stdio_server

from .backend import BackendError, DataAgentBackend
from .server import Application
from .settings import Settings


def _logging() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # SDK and identity loggers can echo request bodies or token-adjacent payloads.
    for name in ("mcp", "azure", "httpx2", "httpcore"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.CRITICAL + 1)
        logger.propagate = False


def _claims(token: str) -> dict[str, object]:
    payload = token.split(".")[1]
    data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    return {key: data.get(key) for key in ("aud", "tid", "appid", "azp", "idtyp", "scp", "roles") if key in data}


async def _probe(settings: Settings, question: str | None) -> int:
    backend = DataAgentBackend(settings)
    try:
        token = await backend.token()
        print("token scope:", settings.token_scope)
        print("token claims:", json.dumps(_claims(token.token)))
        tools = await backend.list_upstream_tools()
        for tool in tools:
            print("upstream tool:", tool.name)
            print("  description:", (tool.description or "")[:300])
            print("  input schema:", json.dumps(tool.input_schema))
        if question:
            print("answer:", await backend.ask(question))
    except BackendError as error:
        print("FAILED:", error)
        return 1
    finally:
        await backend.close()
    return 0


async def _run_stdio(application: Application) -> None:
    server = application.server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    serve.add_argument("--port", type=int)
    probe = commands.add_parser("probe", help="Get a token, list the upstream tool, optionally ask a question")
    probe.add_argument("--question")
    arguments = parser.parse_args()
    _logging()
    if arguments.command == "probe":
        sys.exit(asyncio.run(_probe(Settings(), arguments.question)))
    settings = Settings(**({"port": arguments.port} if arguments.port else {}))
    application = Application(settings)
    if arguments.transport == "stdio":
        asyncio.run(_run_stdio(application))
    else:
        uvicorn.run(application.http_app(), host=settings.host, port=settings.port,
                    log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
