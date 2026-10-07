"""Exercise the deployed OneLake tables MCP server: handshake, schema scope, a live query, guards."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import Client
from mcp.client.streamable_http import streamable_http_client

URL = sys.argv[1] if len(sys.argv) > 1 else os.environ["ONELAKE_MCP_URL"]

CHECKS = {
    "in-scope query": "SELECT COUNT(*) AS clients FROM EXTERNAL_gold.dim_client",
    "denied pre-aggregate": "SELECT TOP 1 total_aum FROM EXTERNAL_gold.advisor_book_summary",
    "denied mlv view": "SELECT TOP 1 * FROM EXTERNAL_wm.mlv_client_360",
    "write guard": "SELECT 1 DROP TABLE x",
}


async def main() -> None:
    async with Client(streamable_http_client(URL), read_timeout_seconds=120, cache=None) as client:
        print("protocol:", client.protocol_version)
        print("tools:", [tool.name for tool in (await client.list_tools()).tools])

        schema = await client.call_tool("get_database_schema", {})
        tables = (schema.structured_content or {}).get("tables", [])
        print(f"get_database_schema is_error={schema.is_error} tables={len(tables)} "
              f"columns={sum(len(t['columns']) for t in tables)}")

        for label, query in CHECKS.items():
            result = await client.call_tool("execute_sql", {"query": query})
            print(f"{label}: is_error={result.is_error} {json.dumps(result.structured_content)[:200]}")


asyncio.run(main())
