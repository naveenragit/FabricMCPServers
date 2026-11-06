"""Real stdio subprocess and TCP HTTP smoke checks; no Power BI requests."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path

import httpx2
import pytest
import uvicorn
from mcp import Client, StdioServerParameters
from test_profile import synthetic_metadata

from wealth_management_mcp.contracts import write_json
from wealth_management_mcp.profile import build_catalog
from wealth_management_mcp.server import CATALOG_URI, STATUS_URI, Application
from wealth_management_mcp.settings import Settings


@pytest.fixture
def sample_catalog(tmp_path: Path) -> tuple[dict, Path]:
    source = tmp_path / "metadata.json"
    summary = tmp_path / "summary.json"
    write_json(source, synthetic_metadata())
    write_json(summary, {"source_sha256": "a" * 64, "extraction_issues": [],
                         "table_count": 2, "column_count": 7, "measure_count": 1, "relationship_count": 1})
    catalog = build_catalog(source, summary)
    output = tmp_path / "catalog.json"
    write_json(output, catalog)
    return catalog, output


async def test_real_stdio_subprocess(sample_catalog: tuple[dict, Path]) -> None:
    _, path = sample_catalog
    async with asyncio.timeout(30), Client(StdioServerParameters(
        command=sys.executable,
        args=["-m", "wealth_management_mcp", "serve", "--catalog", str(path), "--transport", "stdio"],
        env={"WEALTH_MCP_BACKEND": "offline"},
    )) as client:
        assert not (await client.list_tools()).tools
        result = await client.read_resource(CATALOG_URI)
        assert json.loads(result.contents[0].text)["coverage"]["metrics"] == 1


async def test_real_loopback_http(sample_catalog: tuple[dict, Path]) -> None:
    catalog, _ = sample_catalog
    ready = asyncio.Event()

    class SignaledServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            ready.set()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = SignaledServer(uvicorn.Config(
            Application(Settings(), catalog).http_app(), log_level="critical", access_log=False,
        ))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(30):
                await ready.wait()
                async with httpx2.AsyncClient() as http:
                    response = await http.get(f"http://127.0.0.1:{port}/health")
                    assert response.json()["status"] == "alive"
                async with Client(f"http://127.0.0.1:{port}/mcp", cache=None) as client:
                    assert not (await client.list_tools()).tools
                    result = await client.read_resource(STATUS_URI)
                    assert json.loads(result.contents[0].text)["inbound_auth"] == "none"
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=10)