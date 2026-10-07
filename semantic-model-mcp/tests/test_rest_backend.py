"""Focused tests for the Power BI REST query payloads."""

from __future__ import annotations

from typing import Any

import pytest

from wealth_management_mcp import rest_backend as rest_backend_module
from wealth_management_mcp.rest_backend import RestBackend
from wealth_management_mcp.settings import Settings

IDS = {
    "workspace_id": "00000000-0000-4000-8000-000000000001",
    "semantic_model_id": "00000000-0000-4000-8000-000000000002",
    "report_id": "00000000-0000-4000-8000-000000000003",
}
TENANT_ID = "00000000-0000-4000-8000-000000000004"


@pytest.mark.asyncio
async def test_execute_query_does_not_send_effective_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RestBackend(Settings(**IDS, backend="fabric_rest", tenant_id=TENANT_ID))
    requests: list[dict[str, Any]] = []

    async def request_bytes(method: str, path: str, payload: dict[str, Any]) -> bytes:
        requests.append(payload)
        return b"fixture-arrow"

    monkeypatch.setattr(backend, "_request_bytes", request_bytes)
    monkeypatch.setattr(
        rest_backend_module,
        "_decode_arrow",
        lambda content, max_rows: [{"rows": [{"[Value]": 1}]}],
    )

    await backend.execute_query('EVALUATE ROW("Value", 1)')

    assert requests == [{"query": 'EVALUATE ROW("Value", 1)'}]


@pytest.mark.asyncio
async def test_execute_query_as_user_sends_effective_username(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RestBackend(Settings(**IDS, backend="fabric_rest", tenant_id=TENANT_ID))
    requests: list[tuple[str, str, dict[str, Any]]] = []

    async def request_bytes(method: str, path: str, payload: dict[str, Any]) -> bytes:
        requests.append((method, path, payload))
        return b"fixture-arrow"

    monkeypatch.setattr(backend, "_request_bytes", request_bytes)
    monkeypatch.setattr(
        rest_backend_module,
        "_decode_arrow",
        lambda content, max_rows: [{"rows": [{"[Value]": 1}]}],
    )

    result = await backend.execute_query_as_user(
        'EVALUATE ROW("Value", 1)',
        "read-only-user@contoso.com",
    )

    assert result == {"results": [{"tables": [{"rows": [{"[Value]": 1}]}]}]}
    assert requests == [
        (
            "POST",
            "/groups/00000000-0000-4000-8000-000000000001/"
            "datasets/00000000-0000-4000-8000-000000000002/executeDaxQueries",
            {
                "query": 'EVALUATE ROW("Value", 1)',
                "effectiveUsername": "read-only-user@contoso.com",
            },
        )
    ]