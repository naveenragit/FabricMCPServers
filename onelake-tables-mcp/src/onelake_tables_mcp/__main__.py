"""Serve over stdio (local clients) or Streamable HTTP (App Service)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import uvicorn
from mcp.server.stdio import stdio_server

from .server import Application
from .settings import Settings


def _quiet_logging() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.ERROR, format="%(levelname)s %(name)s: %(message)s")
    # SDK and identity loggers can echo query arguments or token-adjacent payloads.
    for name in ("mcp", "azure", "mssql_python"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.CRITICAL + 1)
        logger.propagate = False


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
    arguments = parser.parse_args()
    _quiet_logging()
    settings = Settings(**({"port": arguments.port} if arguments.port else {}))
    application = Application(settings)
    if arguments.transport == "stdio":
        asyncio.run(_run_stdio(application))
    else:
        uvicorn.run(application.http_app(), host=settings.host, port=settings.port,
                    log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
