"""Focused tests for the Power BI REST query payloads."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from azure.core.credentials import AccessToken

from wealth_management_mcp import rest_backend as rest_backend_module
from wealth_management_mcp.backend import BackendError
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


class CountingCredential:
    def __init__(self, lifetime_s: float) -> None:
        self.lifetime_s = lifetime_s
        self.calls = 0

    async def get_token(self, scope: str) -> AccessToken:
        self.calls += 1
        await asyncio.sleep(0.01)
        return AccessToken(f"token-{self.calls}", int(time.time() + self.lifetime_s))


@pytest.mark.asyncio
async def test_token_is_fetched_once_and_shared_by_concurrent_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = RestBackend(Settings(**IDS, backend="fabric_rest", tenant_id=TENANT_ID))
    credential = CountingCredential(lifetime_s=3600)
    created: list[CountingCredential] = []
    monkeypatch.setattr(backend, "_credential", lambda: created.append(credential) or credential)

    await backend.warm_up()
    tokens = await asyncio.gather(*(backend._bearer_token() for _ in range(5)))

    assert tokens == ["token-1"] * 5
    assert credential.calls == 1
    assert len(created) == 1


@pytest.mark.asyncio
async def test_token_is_refreshed_near_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = RestBackend(Settings(**IDS, backend="fabric_rest", tenant_id=TENANT_ID))
    credential = CountingCredential(lifetime_s=60)
    monkeypatch.setattr(backend, "_credential", lambda: credential)

    assert await backend._bearer_token() == "token-1"
    assert await backend._bearer_token() == "token-2"


@pytest.mark.asyncio
async def test_warm_up_swallows_sign_in_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = RestBackend(Settings(**IDS, backend="fabric_rest", tenant_id=TENANT_ID))

    def broken() -> Any:
        raise ValueError("PRIVATE_SENTINEL")

    monkeypatch.setattr(backend, "_credential", broken)

    await backend.warm_up()
    with pytest.raises(BackendError) as error:
        await backend._bearer_token()
    assert "PRIVATE_SENTINEL" not in str(error.value)