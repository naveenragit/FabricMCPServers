"""Exercise the deployed MCP server over Streamable HTTP: handshake, tools, and a live query."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import Client
from mcp.client.streamable_http import streamable_http_client

URL = sys.argv[1] if len(sys.argv) > 1 else os.environ["WEALTH_MCP_URL"]
MODEL_ID = os.environ["WEALTH_MCP_SEMANTIC_MODEL_ID"]
REPORT_ID = os.environ["WEALTH_MCP_REPORT_ID"]


async def main() -> None:
    async with Client(streamable_http_client(URL), read_timeout_seconds=120, cache=None) as client:
        print("protocol:", client.protocol_version)

        tools = await client.list_tools()
        print("tools:", [tool.name for tool in tools.tools])

        resources = await client.list_resources()
        print("resources:", [str(item.uri) for item in resources.resources])

        status = await client.read_resource("wealth://status")
        print("status:", json.loads(status.contents[0].text))

        result = await client.call_tool(
            "execute_dax_query",
            {"model_id": MODEL_ID, "dax_query": 'EVALUATE ROW("Tables", COUNTROWS(INFO.VIEW.TABLES()))'},
        )
        print("execute_dax_query is_error:", result.is_error)
        print("execute_dax_query result:", json.dumps(result.structured_content)[:500])

        report = await client.call_tool("get_report_metadata", {"report_id": REPORT_ID})
        print("get_report_metadata is_error:", report.is_error)
        print("get_report_metadata result:", json.dumps(report.structured_content)[:300])


asyncio.run(main())
