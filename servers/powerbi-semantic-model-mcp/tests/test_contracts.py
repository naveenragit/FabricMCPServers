"""Synthetic contracts only: none of these names/schemas are Microsoft discoveries."""

from __future__ import annotations

import copy
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.types import LATEST_PROTOCOL_VERSION, CallToolResult, ListToolsResult

from wealth_management_mcp import backend as backend_module
from wealth_management_mcp.backend import BackendError, RemoteBackend
from wealth_management_mcp.contracts import (
    ContractManifest,
    argument_value,
    discovery_manifest,
    load_manifest,
    local_validator,
)
from wealth_management_mcp.profile import canonical_sha256
from wealth_management_mcp.settings import Settings

NAMES = {
    "get_semantic_model_schema": "fixture_only.SchemaV1",
    "execute_query": "fixture_only.ExecuteV1",
    "get_report_metadata": "fixture_only.ReportV1",
    "generate_query": "fixture_only.GenerateV1",
}


def object_schema(**properties: Any) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def synthetic_response() -> dict[str, Any]:
    """Small native success payload; no real service response or private rows."""
    return {"fixture": "native schema/result sentinel", "success": True,
            "rows": [{"fixture": "native row sentinel"}], "error": None,
            "results": [{"records": [], "failure": None}]}


def reviewed_fixture_manifest() -> ContractManifest:
    """Review is simulated exclusively for tests, never evidence of a real contract."""
    model = object_schema(ModelId={"type": "string"})
    rows = {"type": "array", "items": object_schema(fixture={"type": "string"})}
    output = object_schema(
        fixture={"type": "string"}, success={"type": "boolean"}, rows=rows,
        error={"type": ["null", "object"]},
        results={"type": "array", "items": object_schema(records=rows, failure={"type": ["null", "string"]})},
    )
    output["properties"]["diagnostics"] = {"type": "object"}
    inputs = {
        "get_semantic_model_schema": object_schema(Target=model),
        "execute_query": object_schema(Target=model, DaxText={"type": "string"}),
        "get_report_metadata": object_schema(Target=object_schema(ReportId={"type": "string"})),
        "generate_query": object_schema(Target=model, Question={"type": "string"},
                                         NativeContext={"type": "object"}),
    }
    bindings = {
        "get_semantic_model_schema": {"model_id_path": ["Target", "ModelId"]},
        "execute_query": {"model_id_path": ["Target", "ModelId"], "dax_query_path": ["DaxText"],
                          "response_rows_paths": [["rows"], ["results", "*", "records"]]},
        "get_report_metadata": {"report_id_path": ["Target", "ReportId"]},
        "generate_query": {"model_id_path": ["Target", "ModelId"], "question_path": ["Question"],
                           "schema_context_path": ["NativeContext"]},
    }
    document = discovery_manifest({
        "protocol_version": LATEST_PROTOCOL_VERSION,
        "tools": [{"name": NAMES[role], "description": "TEST FIXTURE ONLY; not a Microsoft tool.",
                   "inputSchema": schema, "outputSchema": copy.deepcopy(output)}
                  for role, schema in inputs.items()],
    })
    document.update(status="reviewed", roles=[
        {"role": role, "tool_name": NAMES[role], "response_policy_reviewed": True,
         "response_error_paths": [["error"], ["results", "*", "failure"]],
         "response_success_path": ["success"], "response_success_value": True,
         **paths} for role, paths in bindings.items()
    ])
    return ContractManifest.model_validate(document)


def test_discovery_requires_review_and_preserves_exact_contract(tmp_path: Path) -> None:
    fixture = reviewed_fixture_manifest()
    discovered = discovery_manifest({"protocol_version": fixture.protocol_version, "tools": fixture.tools})
    assert discovered["status"] == "unreviewed" and discovered["roles"] == []
    assert discovered["tools"] == fixture.tools
    assert discovered["discovery_sha256"] == canonical_sha256(fixture.tools)
    path = tmp_path / "synthetic-contract.json"
    path.write_text(json.dumps(discovered), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed"):
        load_manifest(path)
    path.write_text(fixture.model_dump_json(), encoding="utf-8")
    tools = load_manifest(path).verified_tools()
    assert set(tools) == set(NAMES.values())
    for raw in fixture.tools:
        assert tools[raw["name"]].input_schema == raw["inputSchema"]
        assert tools[raw["name"]].output_schema == raw["outputSchema"]
        assert local_validator(raw["outputSchema"]).is_valid(synthetic_response())
    for role in fixture.roles:
        assert role.response_policy_reviewed and role.response_error_paths
        assert role.response_success_path == ["success"] and role.response_success_value is True
        if role.role == "execute_query":
            assert role.response_rows_paths == [["rows"], ["results", "*", "records"]]
    assert argument_value({"Target": {"ModelId": "fixture"}}, ["Target", "ModelId"]) == "fixture"
    with pytest.raises(ValueError, match="missing"):
        argument_value({"Target": {}}, ["Target", "ModelId"])


def test_manifest_rejects_tampering_duplicates_and_missing_bindings() -> None:
    original = reviewed_fixture_manifest().model_dump(mode="json")
    cases = ["hash", "endpoint", "tool_duplicate", "role_duplicate", "wire_reused", "absent_tool",
             "missing_model", "missing_report", "missing_dax", "missing_question", "missing_context",
             "missing_response_review", "unreviewed_response", "missing_schema_success", "missing_query_rows",
             "unmapped_property"]
    for case in cases:
        document = copy.deepcopy(original)
        roles = document["roles"]
        if case == "hash":
            document["tools"][0]["description"] += " altered after review"
        elif case == "endpoint":
            document["endpoint"] = "https://fixture.invalid/mcp"
        elif case == "tool_duplicate":
            document["tools"].append(copy.deepcopy(document["tools"][0]))
        elif case == "role_duplicate":
            roles.append(copy.deepcopy(roles[0]))
        elif case == "wire_reused":
            roles[1]["tool_name"] = roles[0]["tool_name"]
        elif case == "absent_tool":
            roles[0]["tool_name"] = "fixture_only.Missing"
        elif case == "unreviewed_response":
            roles[0]["response_policy_reviewed"] = False
        elif case.startswith("missing_"):
            index, field = {"missing_model": (0, "model_id_path"), "missing_report": (2, "report_id_path"),
                            "missing_dax": (1, "dax_query_path"), "missing_question": (3, "question_path"),
                            "missing_context": (3, "schema_context_path"),
                            "missing_response_review": (0, "response_policy_reviewed"),
                            "missing_schema_success": (0, "response_success_path"),
                            "missing_query_rows": (1, "response_rows_paths")}[case]
            del roles[index][field]
        else:
            roles[0]["model_id_path"] = ["Target", "UndiscoveredAlias"]
        if case != "hash":
            document["discovery_sha256"] = canonical_sha256(document["tools"])
        with pytest.raises(ValueError):
            ContractManifest.model_validate(document).verified_tools()


def test_contract_schemas_accept_local_refs_but_reject_external_refs() -> None:
    schema = {**object_schema(value={"$ref": "#/$defs/text"}), "$defs": {"text": {"type": "string"}}}
    assert local_validator(schema).is_valid({"value": "fixture"})
    assert not local_validator(schema).is_valid({"value": 123})
    for location in ("inputSchema", "outputSchema"):
        for keyword in ("$ref", "$dynamicRef"):
            for uri in ("https://fixture.invalid/schema", "file:///fixture-secret.json", "relative.json"):
                document = reviewed_fixture_manifest().model_dump(mode="json")
                document["tools"][0][location]["$defs"] = {"external": {keyword: uri}}
                document["discovery_sha256"] = canonical_sha256(document["tools"])
                with pytest.raises(ValueError, match="Remote schema references"):
                    ContractManifest.model_validate(document).verified_tools()


def test_contract_schemas_reject_draft7_instead_of_reinterpreting_it() -> None:
    draft7 = {**object_schema(value={"type": "string"}),
              "$schema": "http://json-schema.org/draft-07/schema#"}
    with pytest.raises(ValueError, match="Draft 2020-12"):
        local_validator(draft7)
    for location in ("inputSchema", "outputSchema"):
        document = reviewed_fixture_manifest().model_dump(mode="json")
        document["tools"][0][location]["$schema"] = draft7["$schema"]
        document["discovery_sha256"] = canonical_sha256(document["tools"])
        with pytest.raises(ValueError, match="Draft 2020-12"):
            ContractManifest.model_validate(document).verified_tools()


@pytest.mark.asyncio
async def test_backend_rechecks_contract_and_sanitizes_upstream_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("WEALTH_MCP_"):
            monkeypatch.delenv(name)
    backend = RemoteBackend(Settings(timeout_seconds=5))
    tool = reviewed_fixture_manifest().verified_tools()[NAMES["execute_query"]]
    upstream = AsyncMock()
    payload = synthetic_response()
    upstream.call_tool.return_value = CallToolResult(content=[], structured_content=payload)

    @asynccontextmanager
    async def connect():
        yield upstream

    monkeypatch.setattr(backend, "connect", connect)
    for field, value in (("input_schema", object_schema(Changed={"type": "string"})),
                         ("output_schema", object_schema(Changed={"type": "string"}))):
        upstream.list_tools.return_value = ListToolsResult(tools=[tool.model_copy(update={field: value})])
        with pytest.raises(BackendError, match="contract changed"):
            await backend.call(tool, {})
        upstream.call_tool.assert_not_awaited()
    upstream.list_tools.return_value = ListToolsResult(tools=[tool.model_copy(update={"description": "new prose"})])
    arguments = {"Target": {"ModelId": "fixture"}, "DaxText": "EVALUATE ROW(\"fixture\", 1)"}
    assert (await backend.call(tool, arguments)).structured_content == payload
    upstream.call_tool.assert_awaited_once_with(tool.name, arguments)
    secret = "PRIVATE_UPSTREAM_TOKEN_SENTINEL"
    upstream.call_tool.side_effect = RuntimeError(secret)
    with pytest.raises(BackendError) as error:
        await backend.call(tool, arguments)
    assert secret not in str(error.value)
    upstream.list_tools.side_effect = ValueError(secret)
    with pytest.raises(BackendError) as error:
        await backend.discover()
    assert secret not in str(error.value)


@pytest.mark.asyncio
async def test_backend_boundaries_sanitize_credential_factory_errors(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    for name in tuple(os.environ):
        if name.startswith("WEALTH_MCP_"):
            monkeypatch.delenv(name)
    secret = "PRIVATE_CREDENTIAL_LIFECYCLE_SENTINEL"
    factory = Mock(side_effect=ValueError(secret))
    monkeypatch.setattr(backend_module, "AzureCliCredential", factory)
    backend = RemoteBackend(Settings(tenant_id="00000000-0000-4000-8000-000000000099", timeout_seconds=5))
    tool = reviewed_fixture_manifest().verified_tools()[NAMES["execute_query"]]
    for operation in (backend.discover, backend.verify_report_binding):
        with pytest.raises(BackendError) as error:
            await operation()
        assert secret not in str(error.value)
    with pytest.raises(BackendError) as error:
        await backend.call(tool, {"Target": {"ModelId": "fixture"}, "DaxText": "EVALUATE ROW(\"fixture\", 1)"})
    assert secret not in str(error.value)
    assert factory.call_count == 3
    assert secret not in caplog.text