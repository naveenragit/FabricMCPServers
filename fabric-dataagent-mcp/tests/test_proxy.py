"""Proxy behaviour against an in-memory fake of the data agent endpoint; no network, no credentials."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from mcp import Client
from mcp.server import Server, ServerRequestContext
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, TextContent, Tool

from fabric_dataagent_mcp import backend as backend_module
from fabric_dataagent_mcp.backend import BackendError, DataAgentBackend, describe_failure
from fabric_dataagent_mcp.server import TOOL, Application
from fabric_dataagent_mcp.settings import Settings

NIL = "00000000-0000-0000-0000-000000000000"
ONE = "11111111-1111-1111-1111-111111111111"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "tenant_id": NIL, "workspace_id": ONE, "data_agent_id": ONE,
        "client_id": ONE, "client_secret": "not-a-real-secret",
    }
    values.update(overrides)
    return Settings(**values)


class FakeCredential:
    async def get_token(self, scope: str) -> Any:
        return SimpleNamespace(token=f"token-for:{scope}")


def fake_upstream(reply: str = "ok", *, is_error: bool = False, tool_name: str = "data_agent",
                  seen: list[dict[str, Any]] | None = None) -> Server[Any]:
    async def list_tools(ctx: ServerRequestContext[Any], params: Any) -> ListToolsResult:
        return ListToolsResult(tools=[Tool(
            name=tool_name, description="fake",
            input_schema={"type": "object", "properties": {"userQuestion": {"type": "string"}}},
        )])

    async def call_tool(ctx: ServerRequestContext[Any], params: CallToolRequestParams) -> CallToolResult:
        if seen is not None:
            seen.append(params.arguments or {})
        return CallToolResult(content=[TextContent(type="text", text=reply)], is_error=is_error)

    return Server("fake-data-agent", on_list_tools=list_tools, on_call_tool=call_tool)


def backend_for(upstream: Server[Any], monkeypatch: pytest.MonkeyPatch) -> DataAgentBackend:
    backend = DataAgentBackend(make_settings(), credential=FakeCredential())
    monkeypatch.setattr(DataAgentBackend, "_client", lambda self, bearer: Client(upstream, cache=None))
    return backend


def test_settings_reject_missing_secret() -> None:
    with pytest.raises(ValueError):
        Settings(tenant_id=NIL, workspace_id=ONE, data_agent_id=ONE)


def test_settings_reject_non_fabric_host() -> None:
    with pytest.raises(ValueError):
        make_settings(fabric_api_host="example.com")


def test_settings_build_endpoint_from_ids() -> None:
    assert make_settings().mcp_url == f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{ONE}/dataagents/{ONE}/agent"


def test_tool_schema_is_foundry_safe() -> None:
    schema = TOOL.input_schema
    assert schema["required"] == ["question"] and list(schema["properties"]) == ["question"]
    assert not {"anyOf", "allOf", "oneOf"} & set(schema["properties"]["question"])


async def test_question_and_answer_pass_through_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    backend = backend_for(fake_upstream("exactly this\nanswer", seen=seen), monkeypatch)
    question = "  How many clients do we have?  "
    assert await backend.ask(question) == "exactly this\nanswer"
    assert seen == [{"userQuestion": question}]


async def test_upstream_error_becomes_backend_error(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = backend_for(fake_upstream("agent says no", is_error=True), monkeypatch)
    with pytest.raises(BackendError, match="agent says no"):
        await backend.ask("q")


async def test_application_rejects_extra_arguments() -> None:
    class Unused:
        async def ask(self, question: str) -> str:
            raise AssertionError("must not be called")

    application = Application(make_settings(), backend=Unused())  # type: ignore[arg-type]
    for arguments in ({}, {"question": ""}, {"question": "x", "extra": 1}, {"question": 5}):
        result = await application.call_tool(None, CallToolRequestParams(name=TOOL.name, arguments=arguments))  # type: ignore[arg-type]
        assert result.is_error


async def test_application_maps_backend_error_without_internals() -> None:
    class Failing:
        async def ask(self, question: str) -> str:
            raise BackendError("Fabric endpoint rejected the request (HTTP 401)")

    application = Application(make_settings(), backend=Failing())  # type: ignore[arg-type]
    result = await application.call_tool(None, CallToolRequestParams(name=TOOL.name, arguments={"question": "q"}))  # type: ignore[arg-type]
    assert result.is_error and "HTTP 401" in result.content[0].text


def test_describe_failure_unwraps_exception_groups() -> None:
    import httpx2

    response = httpx2.Response(403, request=httpx2.Request("POST", "https://api.fabric.microsoft.com/x"))
    error = httpx2.HTTPStatusError("denied", request=response.request, response=response)
    assert "HTTP 403" in describe_failure(ExceptionGroup("g", [ExceptionGroup("inner", [error])]))
    assert backend_module.describe_failure(ValueError("x")).startswith("Fabric data agent call failed")
