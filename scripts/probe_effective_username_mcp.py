"""Verify the executeDaxQueries effectiveUsername permission matrix through MCP.

The probe returns only a constant and reports success/error status, so it does not retrieve
business rows. The expected result is: backend service principal succeeds, a Read-only
effective user fails, and a Read+Build effective user succeeds.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from mcp import Client
from mcp.client.streamable_http import streamable_http_client

URL = sys.argv[1] if len(sys.argv) > 1 else os.environ["WEALTH_MCP_URL"]
MODEL_ID = os.environ["WEALTH_MCP_SEMANTIC_MODEL_ID"]
READ_ONLY_USER = os.environ["WEALTH_MCP_READ_ONLY_TEST_USER"]
BUILD_USER = os.environ["WEALTH_MCP_BUILD_TEST_USER"]
QUERY = 'EVALUATE ROW("Probe", 1)'


def summarize(result: Any) -> dict[str, Any]:
    content = result.structured_content
    details = content if isinstance(content, dict) else {}
    return {
        "success": not bool(result.is_error),
        "code": details.get("code"),
        "message": details.get("message"),
    }


async def main() -> None:
    if READ_ONLY_USER.casefold() == BUILD_USER.casefold():
        raise SystemExit("Read-only and Read+Build probes require two distinct tenant users")

    async with Client(streamable_http_client(URL), read_timeout_seconds=120, cache=None) as client:
        tool_names = {tool.name for tool in (await client.list_tools()).tools}
        if "execute_dax_query_as_user" not in tool_names:
            raise SystemExit("MCP effectiveUsername test tool is not enabled")

        baseline = await client.call_tool(
            "execute_dax_query",
            {"model_id": MODEL_ID, "dax_query": QUERY},
        )
        read_only = await client.call_tool(
            "execute_dax_query_as_user",
            {
                "model_id": MODEL_ID,
                "dax_query": QUERY,
                "effective_username": READ_ONLY_USER,
            },
        )
        build = await client.call_tool(
            "execute_dax_query_as_user",
            {
                "model_id": MODEL_ID,
                "dax_query": QUERY,
                "effective_username": BUILD_USER,
            },
        )

    outcomes = {
        "service_principal": summarize(baseline),
        "read_only_effective_user": summarize(read_only),
        "read_build_effective_user": summarize(build),
    }
    print(json.dumps(outcomes, indent=2))

    failures = []
    if not outcomes["service_principal"]["success"]:
        failures.append("the backend service principal could not execute the baseline query")
    if outcomes["read_only_effective_user"]["success"]:
        failures.append("the Read-only effective user unexpectedly succeeded")
    if not outcomes["read_build_effective_user"]["success"]:
        failures.append("the Read+Build effective user did not succeed")
    if failures:
        raise SystemExit("Permission matrix did not match expectations: " + "; ".join(failures))

    print("Verified: effectiveUsername requires Build in addition to Read for this semantic model.")


asyncio.run(main())