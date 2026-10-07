"""Offline MCP/ASGI tests with synthetic metadata and fixture-only reviewed tools."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp import Client, MCPError
from mcp.types import LATEST_PROTOCOL_VERSION, CallToolResult, TextResourceContents, Tool
from starlette.types import Message, Receive, Scope, Send
from test_contracts import NAMES, reviewed_fixture_manifest, synthetic_response
from test_profile import synthetic_metadata

from wealth_management_mcp.backend import BackendError
from wealth_management_mcp.contracts import local_validator
from wealth_management_mcp.http_limits import ResponseLimitMiddleware
from wealth_management_mcp.profile import PROFILE_ID, build_catalog, load_profile_schema
from wealth_management_mcp.rest_backend import RestBackend
from wealth_management_mcp.server import CATALOG_URI, PROFILE_URI, STATUS_URI, Application, result_data
from wealth_management_mcp.settings import Settings

IDS = {"workspace_id": "00000000-0000-4000-8000-000000000001",
       "semantic_model_id": "00000000-0000-4000-8000-000000000002",
       "report_id": "00000000-0000-4000-8000-000000000003"}
OTHER_ID = "00000000-0000-4000-8000-000000000099"


class FakeBackend:
    """Implements the backend protocol without credentials, network, or real results."""

    def __init__(self) -> None:
        self.events: list[Any] = []
        self.result = result_data(synthetic_response())
        self.error: Exception | None = None
        self.binding_error: Exception | None = None

    async def call(self, tool: Tool, arguments: dict[str, Any]) -> CallToolResult:
        self.events.append((tool.name, deepcopy(arguments)))
        if self.error:
            raise self.error
        return self.result.model_copy(deep=True)

    async def verify_report_binding(self) -> None:
        self.events.append("verify_report_binding")
        if self.binding_error:
            raise self.binding_error


class FakeRestBackend(RestBackend):
    """Capture REST dispatch without acquiring credentials or making HTTP requests."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.events: list[Any] = []

    async def execute_query(self, dax_query: str) -> dict[str, Any]:
        self.events.append(("execute_query", dax_query))
        return {"results": [{"tables": [{"rows": [{"[Value]": 1}]}]}]}

    async def execute_query_as_user(
        self,
        dax_query: str,
        effective_username: str,
    ) -> dict[str, Any]:
        self.events.append(("execute_query_as_user", dax_query, effective_username))
        return {"results": [{"tables": [{"rows": [{"[Value]": 1}]}]}]}

    async def report_metadata(self) -> dict[str, Any]:
        self.events.append("report_metadata")
        return {"id": IDS["report_id"], "datasetId": IDS["semantic_model_id"]}


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("WEALTH_MCP_"):
            monkeypatch.delenv(name)


@pytest.fixture
def catalog(tmp_path: Path) -> dict[str, Any]:
    metadata = synthetic_metadata()
    source, summary = tmp_path / "metadata.json", tmp_path / "summary.json"
    source.write_text(json.dumps(metadata), encoding="utf-8")
    summary.write_text(json.dumps({"extraction_issues": [], "table_count": 2, "column_count": 7,
                                   "measure_count": 1, "relationship_count": 1, "source_sha256": "a" * 64}),
                       encoding="utf-8")
    return build_catalog(source, summary, model_id=IDS["semantic_model_id"],
                         workspace_id=IDS["workspace_id"], report_id=IDS["report_id"])


@pytest.fixture
def live(catalog: dict[str, Any], tmp_path: Path) -> Callable[..., tuple[Application, FakeBackend]]:
    def make(**overrides: Any) -> tuple[Application, FakeBackend]:
        settings = Settings(**{**IDS, "backend": "remote_mcp", "tenant_id": OTHER_ID,
                               "contract_path": tmp_path / "fixture-only-contract.json", **overrides})
        backend = FakeBackend()
        return Application(settings, catalog, reviewed_fixture_manifest(), backend), backend
    return make


def arguments(role: str) -> dict[str, Any]:
    if role == "get_report_metadata":
        return {"Target": {"ReportId": IDS["report_id"]}}
    result: dict[str, Any] = {"Target": {"ModelId": IDS["semantic_model_id"]}}
    if role == "execute_query":
        result["DaxText"] = " EVALUATE ROW(\"fixture\", 1)\n"
    elif role == "generate_query":
        result.update(Question="Fixture question", NativeContext={"tables": ["fixture_table"]})
    return result


async def read_json(client: Client, uri: str) -> dict[str, Any]:
    result = await client.read_resource(uri)
    assert len(result.contents) == 1
    content = result.contents[0]
    assert isinstance(content, TextResourceContents) and str(content.uri) == uri
    return json.loads(content.text)


@pytest.mark.asyncio
async def test_offline_resources_status_counts_schema_and_invalid_uri(catalog: dict[str, Any]) -> None:
    async with Client(Application(Settings(**IDS), catalog).server(), cache=None) as client:
        assert (await client.list_tools()).tools == []
        status = await read_json(client, STATUS_URI)
        assert status["mode"] == "offline" and status["enabled_tools"] == []
        assert status["contract_status"] == "not_discovered" and not status["query_ready"]
        assert status["inbound_auth"] == "none" and status["per_caller_isolation"] is False
        assert status["live_definition"] == "unverified" and status["data_freshness"] == "unknown"
        assert status["catalog_sha256"] == catalog["catalog_sha256"]
        resources = (await client.list_resources()).resources
        assert {str(resource.uri) for resource in resources} == {STATUS_URI, PROFILE_URI, CATALOG_URI}
        snapshot = await read_json(client, CATALOG_URI)
        assert snapshot == catalog
        assert {key: snapshot["coverage"][key] for key in ("datasets", "fields", "metrics", "relationships")} == {
            "datasets": 2, "fields": 7, "metrics": 1, "relationships": 1}
        schema = await read_json(client, PROFILE_URI)
        assert schema == load_profile_schema() and schema["$id"] == PROFILE_ID
        for uri in ("wealth://missing", CATALOG_URI + "/extra", CATALOG_URI + "?bypass=true"):
            with pytest.raises(MCPError) as error:
                await client.read_resource(uri)
            assert error.value.code == -32602


@pytest.mark.asyncio
async def test_live_preserves_names_input_schemas_and_disables_generation_by_default(live) -> None:
    app, backend = live()
    fixture = reviewed_fixture_manifest().verified_tools()
    async with Client(app.server(), cache=None) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(tools) == set(NAMES.values()) - {NAMES["generate_query"]}
        for name, tool in tools.items():
            assert tool.input_schema == fixture[name].input_schema
        schema_tool = tools[NAMES["get_semantic_model_schema"]]
        assert "not the native Microsoft schema" in schema_tool.description
        assert schema_tool.output_schema != fixture[schema_tool.name].output_schema
        with pytest.raises(MCPError) as error:
            await client.call_tool(NAMES["generate_query"], arguments("generate_query"))
        assert error.value.code == -32602
    assert backend.events == []


@pytest.mark.asyncio
async def test_effective_username_tool_is_opt_in_and_server_allowlisted(catalog) -> None:
    common = {
        **IDS,
        "backend": "fabric_rest",
        "tenant_id": OTHER_ID,
        "allow_unverified_live_definition": True,
    }
    disabled_settings = Settings(**common)
    disabled = Application(disabled_settings, catalog, backend=FakeRestBackend(disabled_settings))
    async with Client(disabled.server(), cache=None) as client:
        assert "execute_dax_query_as_user" not in {
            tool.name for tool in (await client.list_tools()).tools
        }

    settings = Settings(
        **common,
        enable_effective_username_test=True,
        effective_username_allowlist=["read-only-user@contoso.com", "build-user@contoso.com"],
    )
    backend = FakeRestBackend(settings)
    app = Application(settings, catalog, backend=backend)
    args = {
        "model_id": IDS["semantic_model_id"],
        "dax_query": ' EVALUATE ROW("Value", 1) ',
        "effective_username": "READ-ONLY-USER@CONTOSO.COM",
    }
    async with Client(app.server(), cache=None) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert "execute_dax_query_as_user" in tools
        assert "server-side allowlist" in tools["execute_dax_query_as_user"].description

        denied = await client.call_tool(
            "execute_dax_query_as_user",
            {**args, "effective_username": "other-user@contoso.com"},
        )
        assert denied.is_error and "server-side test allowlist" in denied.structured_content["message"]
        assert backend.events == []

        result = await client.call_tool("execute_dax_query_as_user", args)
        assert not result.is_error
        assert result.meta["wealth-management-mcp/provenance"]["identity_mode"] == (
            "allowlisted_effective_username_test"
        )
    assert backend.events == [
        (
            "execute_query_as_user",
            ' EVALUATE ROW("Value", 1) ',
            "read-only-user@contoso.com",
        )
    ]


@pytest.mark.asyncio
async def test_allowlists_and_exact_input_contract_reject_before_backend(live) -> None:
    app, backend = live(enable_generate_query=True, allow_author_catalog=True, allow_unverified_live_definition=True)
    cases = []
    for role in NAMES:
        args = arguments(role)
        args["Target"]["ReportId" if role == "get_report_metadata" else "ModelId"] = OTHER_ID
        cases.append((role, args))
    cases.extend(("execute_query", args) for args in (
        {}, {**arguments("execute_query"), "DaxText": 42},
        {**arguments("execute_query"), "DaxText": " \n"},
        {**arguments("execute_query"), "modelId": IDS["semantic_model_id"]},
        {**arguments("execute_query"), "Target": {"ModelId": "not-a-uuid"}},
    ))
    async with Client(app.server(), cache=None) as client:
        for role, args in cases:
            result = await client.call_tool(NAMES[role], args)
            assert result.is_error, (role, args)
            assert backend.events == []


@pytest.mark.asyncio
async def test_author_catalog_requires_approval_and_successful_native_access_check(live, catalog) -> None:
    name, args = NAMES["get_semantic_model_schema"], arguments("get_semantic_model_schema")
    app, backend = live()
    async with Client(app.server(), cache=None) as client:
        result = await client.call_tool(name, args)
        assert result.is_error and "explicit approval" in result.structured_content["message"]
    assert backend.events == []
    app, backend = live(allow_author_catalog=True)
    async with Client(app.server(), cache=None) as client:
        result = await client.call_tool(name, args)
        assert not result.is_error and result.structured_content == catalog
        assert local_validator(app.tools[name].output_schema).is_valid(result.structured_content)
        assert backend.events == [(name, args)]
        assert result.meta["wealth-management-mcp/provenance"]["live_definition"] == "unverified"
        backend.result = result_data({**synthetic_response(), "success": False,
                                      "error": {"message": "native access denied"}}, error=True)
        denied = await client.call_tool(name, args)
        assert denied.is_error and "semantic_document" not in denied.structured_content
        for success in (False, None):
            payload = synthetic_response()
            if success is None:
                del payload["success"]
            else:
                payload["success"] = success
            backend.result = result_data(payload)
            denied = await client.call_tool(name, args)
            assert denied.is_error and denied.structured_content["code"] == "operation_rejected"
            assert "semantic_document" not in denied.model_dump_json()


@pytest.mark.asyncio
async def test_nested_native_errors_never_authorize_catalog_disclosure(live, catalog, caplog) -> None:
    app, backend = live(allow_author_catalog=True)
    name, args = NAMES["get_semantic_model_schema"], arguments("get_semantic_model_schema")
    secret = "PRIVATE_NATIVE_NESTED_ERROR_SENTINEL"
    mapped = synthetic_response()
    mapped["results"][0]["failure"] = secret
    unmapped = synthetic_response()
    unmapped["diagnostics"] = {"tables": [{"details": {"Errors": [{"message": secret}]}}]}
    async with Client(app.server(), cache=None) as client:
        for payload in (mapped, unmapped):
            assert payload["success"] is True
            assert local_validator(app.native_tools[name].output_schema).is_valid(payload)
            backend.result = result_data(payload)
            assert not backend.result.is_error
            denied = await client.call_tool(name, args)
            assert denied.is_error and denied.structured_content["code"] == "operation_rejected"
            serialized = denied.model_dump_json()
            assert "semantic_document" not in serialized and catalog["catalog_sha256"] not in serialized
            assert secret not in serialized
    assert backend.events == [(name, args), (name, args)]
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_query_and_generation_require_unverified_definition_approval(live) -> None:
    for approved in (False, True):
        app, backend = live(enable_generate_query=True, allow_unverified_live_definition=approved)
        async with Client(app.server(), cache=None) as client:
            for role in ("execute_query", "generate_query"):
                args = arguments(role)
                result = await client.call_tool(NAMES[role], args)
                assert result.is_error is not approved
                if approved:
                    assert backend.events[-1] == (NAMES[role], args)
                    assert result.structured_content == backend.result.structured_content
                else:
                    assert "unverified" in result.structured_content["message"]
                    assert backend.events == []


@pytest.mark.asyncio
async def test_query_row_limit_aggregates_all_reviewed_locations(live) -> None:
    app, backend = live(allow_unverified_live_definition=True, max_rows=3)
    name, args = NAMES["execute_query"], arguments("execute_query")
    secret = "PRIVATE_AGGREGATED_ROW_SENTINEL"
    payload = synthetic_response()
    payload["rows"] = [{"fixture": secret}]
    payload["results"] = [{"records": [{"fixture": secret}], "failure": None} for _ in range(2)]
    async with Client(app.server(), cache=None) as client:
        backend.result = result_data(payload)
        allowed = await client.call_tool(name, args)
        assert not allowed.is_error and allowed.structured_content == payload
        payload["results"][1]["records"].append({"fixture": secret})
        assert max(len(payload["rows"]), *(len(item["records"]) for item in payload["results"])) < 3
        assert local_validator(app.native_tools[name].output_schema).is_valid(payload)
        backend.result = result_data(payload)
        denied = await client.call_tool(name, args)
        assert denied.is_error and denied.structured_content["code"] == "operation_rejected"
        assert "total row limit" in denied.structured_content["message"]
        assert secret not in denied.model_dump_json()
    assert backend.events == [(name, args), (name, args)]


@pytest.mark.asyncio
async def test_report_binding_is_checked_before_forwarding_report_request(live) -> None:
    app, backend = live()
    name, args = NAMES["get_report_metadata"], arguments("get_report_metadata")
    async with Client(app.server(), cache=None) as client:
        assert not (await client.call_tool(name, args)).is_error
        assert backend.events == ["verify_report_binding", (name, args)]
        backend.events.clear()
        backend.binding_error = BackendError("Report was rebound or does not match the configured model")
        assert (await client.call_tool(name, args)).is_error
        assert backend.events == ["verify_report_binding"]


@pytest.mark.asyncio
async def test_tool_errors_do_not_expose_upstream_secrets(live, caplog) -> None:
    app, backend = live(allow_unverified_live_definition=True)
    secret = "PRIVATE_TOKEN_AND_RESULT_SENTINEL"
    async with Client(app.server(), cache=None) as client:
        for failure, code in ((None, "upstream_error"), (RuntimeError(secret), "internal_error"),
                              (ValueError(secret), "internal_error"),
                              (TimeoutError(secret), "timeout")):
            backend.error = failure
            backend.result = result_data({**synthetic_response(), "success": False,
                                          "error": {"message": secret}}, error=True)
            result = await client.call_tool(NAMES["execute_query"], arguments("execute_query"))
            assert result.is_error and result.structured_content["code"] == code
            assert secret not in result.model_dump_json()
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_live_catalog_resource_cannot_bypass_tool_authorization(live) -> None:
    for approved in (False, True):
        app, backend = live(allow_author_catalog=approved)
        async with Client(app.server(), cache=None) as client:
            assert {str(resource.uri) for resource in (await client.list_resources()).resources} == {
                STATUS_URI, PROFILE_URI}
            assert (await read_json(client, STATUS_URI))["mode"] == "remote_mcp"
            assert await read_json(client, PROFILE_URI) == load_profile_schema()
            with pytest.raises(MCPError) as error:
                await client.read_resource(CATALOG_URI)
            assert error.value.code == -32602
        assert backend.events == []


@pytest.mark.asyncio
async def test_http_loopback_asgi_health_and_mcp_session(catalog) -> None:
    app = Application(Settings(**IDS), catalog).http_app()
    assert any(middleware.cls is ResponseLimitMiddleware for middleware in app.user_middleware)
    async with app.router.lifespan_context(app), httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        headers={"Accept": "application/json, text/event-stream"},
    ) as client:
        health = await client.get("/health")
        assert health.status_code == 200 and health.json() == {"status": "alive", "mode": "offline"}
        response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "synthetic-asgi-test", "version": "1.0.0"}}})
        assert response.status_code == 200 and "result" in response.json()
        client.headers["Mcp-Session-Id"] = response.headers["Mcp-Session-Id"]
        client.headers["MCP-Protocol-Version"] = response.json()["result"]["protocolVersion"]
        initialized = await client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert initialized.status_code == 202
        response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert response.status_code == 200 and response.json()["result"]["tools"] == []
        response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "resources/read",
                                                   "params": {"uri": STATUS_URI}})
        assert response.status_code == 200
        assert json.loads(response.json()["result"]["contents"][0]["text"])["mode"] == "offline"
        for headers in ({"Host": "fixture.invalid"}, {"Origin": "https://fixture.invalid"}):
            rejected = await client.post("/mcp", headers=headers,
                                         json={"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
            assert rejected.status_code in {400, 403, 421}


@pytest.mark.asyncio
async def test_final_http_response_limit_buffers_all_data_before_502() -> None:
    secret = "PRIVATE_WIRE_RESULT_SENTINEL"
    wire = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"rows": [{"fixture": secret}]}}).encode()
    scope: Scope = {"type": "http", "method": "POST", "path": "/mcp"}
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def record(message: Message) -> None:
        sent.append(deepcopy(message))

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(wire)).encode())]})
        assert sent == [], "Success headers must not escape before the size is known"
        middle = len(wire) // 2
        await send({"type": "http.response.body", "body": wire[:middle], "more_body": True})
        assert sent == [], "Partial result data must remain buffered"
        await send({"type": "http.response.body", "body": wire[middle:], "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    for limit in (len(wire), len(wire) - 1):
        sent.clear()
        await ResponseLimitMiddleware(downstream, limit)(scope, receive, record)
        assert [message["type"] for message in sent] == ["http.response.start", "http.response.body"]
        assert not sent[1].get("more_body", False)
        if limit == len(wire):
            assert sent[0]["status"] == 200 and sent[1]["body"] == wire
        else:
            assert sent[0]["status"] == 502
            assert json.loads(sent[1]["body"]) == {"error": "response_limit_exceeded"}
            assert secret.encode() not in sent[1]["body"]


def test_settings_nonloopback_requires_acknowledgment_and_host_allowlist() -> None:
    for host in ("127.0.0.1", "localhost", "::1"):
        assert Settings(host=host).host == host
    for overrides in ({}, {"restricted_network_confirmed": True}, {"allowed_hosts": ["fixture.internal:8000"]}):
        with pytest.raises(ValueError, match="Non-loopback"):
            Settings(host="0.0.0.0", **overrides)
    assert Settings(host="0.0.0.0", restricted_network_confirmed=True,
                    allowed_hosts=["fixture.internal:8000"]).host == "0.0.0.0"


def test_settings_managed_identity_requires_explicit_approval_and_live_requires_scope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Application identity requires explicit approval"):
        Settings(credential_mode="managed_identity")
    assert Settings(credential_mode="managed_identity", allow_application_identity=True).allow_application_identity
    for overrides in ({}, {"tenant_id": OTHER_ID}, {"contract_path": tmp_path / "fixture.json"}):
        with pytest.raises(ValueError, match="explicit tenant and reviewed contract"):
            Settings(backend="remote_mcp", **overrides)


def test_settings_effective_username_test_requires_arrow_rest_and_allowlist() -> None:
    common = {**IDS, "backend": "fabric_rest", "tenant_id": OTHER_ID}
    with pytest.raises(ValueError, match="nonempty server-side allowlist"):
        Settings(**common, enable_effective_username_test=True)
    with pytest.raises(ValueError, match="requires the fabric_rest backend"):
        Settings(
            **IDS,
            enable_effective_username_test=True,
            effective_username_allowlist=["user@contoso.com"],
        )
    with pytest.raises(ValueError, match="requires executeDaxQueries"):
        Settings(
            **common,
            dax_endpoint="execute_queries",
            enable_effective_username_test=True,
            effective_username_allowlist=["user@contoso.com"],
        )
    with pytest.raises(ValueError, match="must be unique"):
        Settings(
            **common,
            enable_effective_username_test=True,
            effective_username_allowlist=["User@contoso.com", "user@contoso.com"],
        )

    settings = Settings(
        **common,
        enable_effective_username_test=True,
        effective_username_allowlist=[" user@contoso.com "],
    )
    assert settings.effective_username_allowlist == ["user@contoso.com"]