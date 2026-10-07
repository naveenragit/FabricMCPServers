"""Local smoke test for the Fabric REST data path against the live semantic model."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "semantic-model-mcp" / "src"))

os.environ.update(
    {
        "WEALTH_MCP_BACKEND": "fabric_rest",
        "WEALTH_MCP_TENANT_ID": os.environ["WEALTH_MCP_TENANT_ID"],
        "WEALTH_MCP_WORKSPACE_ID": os.environ["WEALTH_MCP_WORKSPACE_ID"],
        "WEALTH_MCP_SEMANTIC_MODEL_ID": os.environ["WEALTH_MCP_SEMANTIC_MODEL_ID"],
        "WEALTH_MCP_REPORT_ID": os.environ["WEALTH_MCP_REPORT_ID"],
        "WEALTH_MCP_ALLOW_UNVERIFIED_LIVE_DEFINITION": "true",
        "WEALTH_MCP_ALLOW_AUTHOR_CATALOG": "true",
        "WEALTH_MCP_CATALOG_PATH": str(ROOT / "artifacts" / "wealth-management-catalog-mcaps.json"),
    }
)

from mcp.types import CallToolRequestParams  # noqa: E402

from wealth_management_mcp.diagnostics import network_report  # noqa: E402
from wealth_management_mcp.server import create_application  # noqa: E402
from wealth_management_mcp.settings import Settings  # noqa: E402


async def main() -> None:
    app = create_application(Settings())
    print("tools:", sorted(app.tools))

    query = 'EVALUATE TOPN(3, SELECTCOLUMNS(INFO.VIEW.MEASURES(), "Measure", [Name]))'
    result = await app.call_tool(
        None,
        CallToolRequestParams(
            name="execute_dax_query",
            arguments={"model_id": os.environ["WEALTH_MCP_SEMANTIC_MODEL_ID"], "dax_query": query},
        ),
    )
    print("execute_dax_query is_error:", result.is_error)
    print(json.dumps(result.structured_content, indent=2)[:700])

    report = await app.call_tool(
        None,
        CallToolRequestParams(
            name="get_report_metadata",
            arguments={"report_id": os.environ["WEALTH_MCP_REPORT_ID"]},
        ),
    )
    print("get_report_metadata is_error:", report.is_error)
    print(str(report.structured_content)[:300])

    denied = await app.call_tool(
        None,
        CallToolRequestParams(
            name="execute_dax_query",
            arguments={"model_id": "00000000-0000-0000-0000-000000000000", "dax_query": "EVALUATE ROW(\"a\",1)"},
        ),
    )
    print("allowlist guard is_error:", denied.is_error, denied.structured_content)

    print("network:", json.dumps(await network_report(os.environ["WEALTH_MCP_TENANT_ID"]), indent=2))


asyncio.run(main())
