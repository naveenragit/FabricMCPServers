"""Authenticated remote MCP transport; never logs credentials or upstream exception details."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Tool

from .settings import Settings

ENDPOINT = "https://api.fabric.microsoft.com/v1/mcp/powerbi"
POWER_BI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"


class BackendError(Exception):
    """A sanitized backend failure suitable for a tool response."""


class BoundedStream(httpx2.AsyncByteStream):
    """Bound bytes as received, before the SDK buffers an HTTP/SSE response."""

    def __init__(self, stream: httpx2.AsyncByteStream, limit: int) -> None:
        self.stream = stream
        self.limit = limit

    async def __aiter__(self) -> AsyncIterator[bytes]:
        count = 0
        async for chunk in self.stream:
            count += len(chunk)
            if count > self.limit:
                raise BackendError("Upstream response exceeded the configured byte limit")
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


class BoundedTransport(httpx2.AsyncBaseTransport):
    """Restrict the credential-bearing transport to the fixed Microsoft origin."""

    def __init__(self, limit: int) -> None:
        self.inner = httpx2.AsyncHTTPTransport()
        self.limit = limit

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.scheme != "https" or request.url.host not in {"api.fabric.microsoft.com", "api.powerbi.com"}:
            raise BackendError("Refused an unapproved downstream origin")
        response = await self.inner.handle_async_request(request)
        # Avoid compressed expansion: accept only identity encoding.
        if response.headers.get("content-encoding", "identity") != "identity":
            await response.aclose()
            raise BackendError("Unsupported upstream content encoding")
        if not isinstance(response.stream, httpx2.AsyncByteStream):
            raise BackendError("Expected an asynchronous upstream response stream")
        response.stream = BoundedStream(response.stream, self.limit)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


class RemoteBackend:
    """One explicitly configured backend identity; a new bounded session per operation."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[Client]:
        credential: Any
        if not self.settings.tenant_id:
            raise BackendError("Set WEALTH_MCP_TENANT_ID before connecting")
        if self.settings.credential_mode == "azure_cli":
            credential = AzureCliCredential(tenant_id=self.settings.tenant_id, process_timeout=20)
        else:
            credential = ManagedIdentityCredential(client_id=self.settings.managed_identity_client_id)
        async with credential:
            try:
                token = await credential.get_token(POWER_BI_SCOPE)
            except Exception:
                raise BackendError("Backend sign-in is unavailable; sign in through the configured provider") from None
            async with httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {token.token}", "Accept-Encoding": "identity"},
                transport=BoundedTransport(self.settings.max_response_bytes),
                timeout=httpx2.Timeout(self.settings.timeout_seconds),
                follow_redirects=False,
            ) as http_client:
                async with Client(
                    streamable_http_client(ENDPOINT, http_client=http_client),
                    read_timeout_seconds=self.settings.timeout_seconds,
                    cache=None,
                ) as client:
                    yield client

    async def discover(self) -> dict[str, Any]:
        """Collect actual wire contracts only; never executes a data query."""
        try:
            async with asyncio.timeout(self.settings.timeout_seconds), self.connect() as client:
                tools = await list_all_tools(client)
                return {
                    "endpoint": ENDPOINT,
                    "protocol_version": client.protocol_version,
                    "tools": [tool.model_dump(by_alias=True, exclude_none=True) for tool in tools],
                }
        except Exception as exc:
            raise BackendError(f"Remote discovery failed ({type(exc).__name__}); no data query was executed") from None

    async def call(self, tool: Tool, arguments: dict[str, Any]) -> CallToolResult:
        try:
            async with asyncio.timeout(self.settings.timeout_seconds), self.connect() as client:
                current = {item.name: item for item in await list_all_tools(client)}
                observed = current.get(tool.name)
                if observed is None or tool_signature(observed) != tool_signature(tool):
                    raise BackendError("Upstream tool contract changed; rediscover and review before retrying")
                return await client.call_tool(tool.name, arguments)
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"Backend operation failed ({type(exc).__name__}); execution status may be unknown") from None

    async def verify_report_binding(self) -> None:
        """Check the configured report's current binding; REST permission is independent."""
        settings = self.settings
        try:
            credential: Any = (
                AzureCliCredential(tenant_id=settings.tenant_id, process_timeout=20)
                if settings.credential_mode == "azure_cli"
                else ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
            )
            async with credential:
                token = await credential.get_token(POWER_BI_SCOPE)
                url = (
                    f"https://api.powerbi.com/v1.0/myorg/groups/{settings.workspace_id}"
                    f"/reports/{settings.report_id}"
                )
                async with httpx2.AsyncClient(
                    headers={"Authorization": f"Bearer {token.token}", "Accept-Encoding": "identity"},
                    transport=BoundedTransport(settings.max_response_bytes),
                    timeout=settings.timeout_seconds,
                    follow_redirects=False,
                ) as client:
                    response = await client.get(url)
                    if response.status_code != 200:
                        raise BackendError("Report binding verification denied or unavailable")
                    data = response.json()
                    if data.get("id") != str(settings.report_id) or data.get("datasetId") != str(settings.semantic_model_id):
                        raise BackendError("Report was rebound or does not match the configured model")
        except BackendError:
            raise
        except Exception:
            raise BackendError("Report binding verification unavailable") from None


async def list_all_tools(client: Client) -> list[Tool]:
    tools: list[Tool] = []
    seen: set[str] = set()
    cursor: str | None = None
    for _ in range(100):
        page = await client.list_tools(cursor=cursor)
        tools.extend(page.tools)
        cursor = page.next_cursor
        if cursor is None:
            if len({item.name for item in tools}) != len(tools):
                raise BackendError("Duplicate upstream tool identifiers")
            return tools
        if cursor in seen:
            raise BackendError("Repeated upstream pagination cursor")
        seen.add(cursor)
    raise BackendError("Upstream discovery pagination exceeded the safety bound")


def tool_signature(tool: Tool) -> str:
    """Compare signatures, not SDK-generated incidental metadata."""
    return json.dumps({"name": tool.name, "input": tool.input_schema, "output": tool.output_schema}, sort_keys=True)