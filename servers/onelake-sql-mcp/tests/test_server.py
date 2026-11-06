"""Tool results: a successful result carries one copy of the data, errors keep the structured code."""

from __future__ import annotations

import asyncio
import json

from mcp.types import CallToolRequestParams

from onelake_tables_mcp.server import Application
from onelake_tables_mcp.settings import Settings
from onelake_tables_mcp.sql_backend import BackendError


class FakeBackend:
    async def execute_sql(self, query: str) -> dict:
        if "boom" in query:
            raise BackendError("rejected")
        return {"columns": ["n"], "rows": [[1]]}

    async def database_schema(self) -> dict:
        return {"tables": []}


def call(name: str, arguments: dict):
    settings = Settings(tenant_id="00000000-0000-0000-0000-000000000001", sql_endpoint="x.datawarehouse.fabric.microsoft.com",
                        database="db")
    app = Application(settings, backend=FakeBackend())
    return asyncio.run(app.call_tool(None, CallToolRequestParams(name=name, arguments=arguments)))


def test_success_is_text_only():
    result = call("execute_sql", {"query": "SELECT 1"})
    assert not result.is_error and result.structured_content is None
    assert json.loads(result.content[0].text) == {"columns": ["n"], "rows": [[1]]}


def test_error_keeps_structured_code():
    result = call("execute_sql", {"query": "SELECT boom"})
    assert result.is_error and result.structured_content["code"] == "operation_rejected"
