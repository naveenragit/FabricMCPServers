"""Fabric REST data path for the semantic model; used when the preview MCP endpoint is unavailable.

Traffic targets ``api.powerbi.com``, which redirects to the tenant's regional
``*.analysis.windows.net`` cluster. Only that cluster namespace is covered by the
Power BI tenant private link, so see ``diagnostics.py`` for the resolution check.

Two DAX endpoints exist and they authorize differently: the legacy ``executeQueries``
rejects this managed identity, while ``executeDaxQueries`` accepts it and returns Apache
Arrow instead of JSON.
"""

from __future__ import annotations

import asyncio
import io
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import httpx2
from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential

from .backend import POWER_BI_SCOPE, BackendError, BoundedTransport
from .settings import Settings

API_ROOT = "https://api.powerbi.com/v1.0/myorg"

#: ``executeQueries`` accepts read-only DAX. Reject anything that is not a query up front
#: so a malformed argument fails locally instead of consuming the backend identity.
_REQUIRED_KEYWORD = "EVALUATE"


class RestBackend:
    """One fixed backend identity; a bounded HTTPS client per operation."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _credential(self) -> Any:
        if self.settings.credential_mode == "azure_cli":
            return AzureCliCredential(tenant_id=self.settings.tenant_id, process_timeout=20)
        return ManagedIdentityCredential(client_id=self.settings.managed_identity_client_id)

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.settings.tenant_id:
            raise BackendError("Set WEALTH_MCP_TENANT_ID before connecting")
        credential = self._credential()
        async with credential:
            try:
                token = await credential.get_token(POWER_BI_SCOPE)
            except Exception:
                raise BackendError("Backend sign-in is unavailable; check the configured identity") from None
            async with httpx2.AsyncClient(
                headers={
                    "Authorization": f"Bearer {token.token}",
                    "Accept-Encoding": "identity",
                    "Content-Type": "application/json",
                },
                transport=BoundedTransport(self.settings.max_response_bytes),
                timeout=httpx2.Timeout(self.settings.timeout_seconds),
                follow_redirects=False,
            ) as client:
                try:
                    response = await client.request(method, f"{API_ROOT}{path}", json=payload)
                except BackendError:
                    raise
                except Exception as exc:
                    raise BackendError(f"Backend request failed ({type(exc).__name__})") from None
                if response.status_code == 401:
                    raise BackendError("Backend identity was not authorized for this semantic model")
                if response.status_code == 403:
                    raise BackendError("Access to the semantic model was forbidden for the backend identity")
                if response.status_code >= 400:
                    raise BackendError(f"Power BI rejected the operation (HTTP {response.status_code})")
                if not response.content:
                    return {}
                try:
                    data = response.json()
                except ValueError:
                    raise BackendError("Upstream response was not valid JSON") from None
                if not isinstance(data, dict):
                    raise BackendError("Upstream response was not a JSON object")
                return data

    async def execute_query(self, dax_query: str) -> dict[str, Any]:
        """Run a read-only DAX query against the allowlisted semantic model."""
        query = _validated_query(dax_query)
        if self.settings.dax_endpoint == "execute_dax_queries":
            return await self._execute_dax_queries(query)
        return await self._execute_queries(query)

    async def execute_query_as_user(self, dax_query: str, effective_username: str) -> dict[str, Any]:
        """Run an Arrow DAX query using an explicitly supplied effective identity."""
        query = _validated_query(dax_query)
        username = effective_username.strip()
        if not username:
            raise BackendError("Effective username must not be blank")
        if self.settings.dax_endpoint != "execute_dax_queries":
            raise BackendError("Effective username testing requires executeDaxQueries")
        return await self._execute_dax_queries(query, effective_username=username)

    async def _execute_queries(self, query: str) -> dict[str, Any]:
        path = (
            f"/groups/{self.settings.workspace_id}"
            f"/datasets/{self.settings.semantic_model_id}/executeQueries"
        )
        payload = {
            "queries": [{"query": query}],
            "serializerSettings": {"includeNulls": True},
        }
        async with asyncio.timeout(self.settings.timeout_seconds):
            data = await self._request("POST", path, payload)
        return self._bound_rows(data)

    async def _execute_dax_queries(
        self,
        query: str,
        *,
        effective_username: str | None = None,
    ) -> dict[str, Any]:
        """Run the Arrow-returning endpoint and normalize it to the JSON row shape."""
        path = (
            f"/groups/{self.settings.workspace_id}"
            f"/datasets/{self.settings.semantic_model_id}/executeDaxQueries"
        )
        payload = {"query": query}
        if effective_username is not None:
            payload["effectiveUsername"] = effective_username
        async with asyncio.timeout(self.settings.timeout_seconds):
            content = await self._request_bytes("POST", path, payload)
        return self._bound_rows({"results": [{"tables": _decode_arrow(content, self.settings.max_rows)}]})

    async def _request_bytes(self, method: str, path: str, payload: dict[str, Any]) -> bytes:
        if not self.settings.tenant_id:
            raise BackendError("Set WEALTH_MCP_TENANT_ID before connecting")
        credential = self._credential()
        async with credential:
            try:
                token = await credential.get_token(POWER_BI_SCOPE)
            except Exception:
                raise BackendError("Backend sign-in is unavailable; check the configured identity") from None
            async with httpx2.AsyncClient(
                headers={
                    "Authorization": f"Bearer {token.token}",
                    "Accept-Encoding": "identity",
                    "Content-Type": "application/json",
                },
                transport=BoundedTransport(self.settings.max_response_bytes),
                timeout=httpx2.Timeout(self.settings.timeout_seconds),
                follow_redirects=False,
            ) as client:
                try:
                    response = await client.request(method, f"{API_ROOT}{path}", json=payload)
                except BackendError:
                    raise
                except Exception as exc:
                    raise BackendError(f"Backend request failed ({type(exc).__name__})") from None
                if response.status_code == 401:
                    raise BackendError("Backend identity was not authorized for this semantic model")
                if response.status_code == 403:
                    raise BackendError("Access to the semantic model was forbidden for the backend identity")
                if response.status_code >= 400:
                    raise BackendError(f"Power BI rejected the operation (HTTP {response.status_code})")
                return bytes(response.content)

    def _bound_rows(self, data: dict[str, Any]) -> dict[str, Any]:
        """Refuse oversized results rather than returning a silently truncated one."""
        for result in data.get("results", []):
            for table in result.get("tables", []):
                rows = table.get("rows")
                if isinstance(rows, list) and len(rows) > self.settings.max_rows:
                    raise BackendError(
                        "Result exceeds the configured row limit; refine the query with TOPN or a filter"
                    )
        return data

    async def report_metadata(self) -> dict[str, Any]:
        """Return the allowlisted report and confirm it is still bound to the model."""
        path = f"/groups/{self.settings.workspace_id}/reports/{self.settings.report_id}"
        async with asyncio.timeout(self.settings.timeout_seconds):
            data = await self._request("GET", path)
        if data.get("id") != str(self.settings.report_id):
            raise BackendError("Report identifier did not match the allowlisted report")
        if data.get("datasetId") != str(self.settings.semantic_model_id):
            raise BackendError("Report was rebound and no longer targets the configured model")
        return data

    async def verify_report_binding(self) -> None:
        await self.report_metadata()


def _validated_query(dax_query: str) -> str:
    query = dax_query.strip()
    if not query:
        raise BackendError("DAX query must not be blank")
    if _REQUIRED_KEYWORD not in query.upper():
        raise BackendError("Only read-only DAX queries containing EVALUATE are accepted")
    return query


def _json_safe(value: Any) -> Any:
    """Arrow yields datetime/Decimal/bytes, none of which survive JSON serialization."""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _decode_arrow(content: bytes, max_rows: int) -> list[dict[str, Any]]:
    """Decode concatenated Arrow IPC streams, treating an error rowset as a failure.

    executeDaxQueries reports query errors as HTTP 200 with an error rowset, so the
    schema metadata must be inspected rather than the status code.
    """
    try:
        import pyarrow as arrow
    except ImportError:
        raise BackendError("Arrow support is unavailable; install pyarrow to use executeDaxQueries") from None

    tables: list[dict[str, Any]] = []
    stream = io.BytesIO(content)
    total = len(content)
    while stream.tell() < total:
        try:
            reader = arrow.ipc.open_stream(stream)
            table = reader.read_all()
        except Exception:
            raise BackendError("Upstream returned a malformed Arrow response") from None
        metadata = {
            key.decode("utf-8", "replace"): value.decode("utf-8", "replace")
            for key, value in (reader.schema.metadata or {}).items()
        }
        if metadata.get("IsError", "").lower() == "true":
            raise BackendError(f"DAX query failed ({metadata.get('FaultCode', 'unknown')})")
        if table.num_rows > max_rows:
            raise BackendError(
                "Result exceeds the configured row limit; refine the query with TOPN or a filter"
            )
        tables.append({"rows": [_json_safe(row) for row in table.to_pylist()]})
    if not tables:
        raise BackendError("Upstream returned an empty Arrow response")
    return tables
