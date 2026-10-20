"""Forward one natural-language question to the Fabric data agent MCP endpoint as the configured identity."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx2
from azure.identity.aio import AzureCliCredential, ClientSecretCredential
from mcp import Client, MCPError
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from .settings import Settings

log = logging.getLogger("fabric_dataagent_mcp")

_MAX_UPSTREAM_ERROR_CHARS = 2000
_TOKEN_REFRESH_MARGIN_S = 300


class BackendError(Exception):
    """Safe-to-return message; never carries tokens or credentials."""


def _leaves(error: BaseException) -> list[BaseException]:
    """Flatten exception groups raised by the transport's task group."""
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in _leaves(inner)]
    return [error]


def describe_failure(error: BaseException) -> str:
    """Name the failing layer and HTTP status, which is what distinguishes a refused token from a bad request."""
    for leaf in _leaves(error):
        if isinstance(leaf, MCPError):
            return f"Fabric endpoint returned an MCP error ({leaf.code}): {leaf.message[:300]}"
        if isinstance(leaf, httpx2.HTTPStatusError):
            return f"Fabric endpoint rejected the request (HTTP {leaf.response.status_code})"
        if isinstance(leaf, httpx2.TimeoutException):
            return "Fabric endpoint timed out"
        if isinstance(leaf, httpx2.HTTPError):
            return f"Could not reach the Fabric endpoint ({type(leaf).__name__})"
    return f"Fabric data agent call failed ({type(_leaves(error)[0]).__name__})"


class DataAgentBackend:
    def __init__(self, settings: Settings, credential: Any = None) -> None:
        self.settings = settings
        if credential is not None:
            self._credential = credential
        elif settings.credential_mode == "azure_cli":
            self._credential = AzureCliCredential(tenant_id=settings.tenant_id, process_timeout=20)
        else:
            assert settings.client_id is not None and settings.client_secret is not None
            self._credential = ClientSecretCredential(
                tenant_id=settings.tenant_id,
                client_id=settings.client_id,
                client_secret=settings.client_secret.get_secret_value(),
            )
        # The data agent advertises one tool; its name and question argument are discovered, not assumed.
        self._tool: tuple[str, str] | None = None
        self._token: Any = None

    async def close(self) -> None:
        await self._credential.close()

    async def token(self) -> Any:
        # AzureCliCredential does not cache: each call runs `az` (about 2 s), which would inflate every measured answer.
        if self._token is None or getattr(self._token, "expires_on", 0) - time.time() < _TOKEN_REFRESH_MARGIN_S:
            try:
                self._token = await self._credential.get_token(self.settings.token_scope)
            except Exception as error:
                raise BackendError(f"Backend sign-in failed ({type(error).__name__})") from None
        return self._token

    def _client(self, bearer: str) -> Client:
        timeout = httpx2.Timeout(30.0, read=float(self.settings.timeout_seconds))
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {bearer}"}, timeout=timeout)
        return Client(
            streamable_http_client(self.settings.mcp_url, http_client=http),
            read_timeout_seconds=float(self.settings.timeout_seconds),
            # Fabric advertises protocol 2026-07-28 but omits cacheScope/ttlMs, which that version requires.
            mode="legacy",
            cache=None,
        )

    async def list_upstream_tools(self) -> list[Any]:
        token = await self.token()
        try:
            async with self._client(token.token) as client:
                return list((await client.list_tools()).tools)
        except BackendError:
            raise
        except Exception as error:
            raise BackendError(describe_failure(error)) from None

    async def ask(self, question: str) -> str:
        started = time.monotonic()
        timing: dict[str, Any] = {
            "event": "timing",
            "started_at": datetime.now(UTC).isoformat(),
            "question_sha256": hashlib.sha256(question.encode()).hexdigest(),
        }
        mark = started

        def lap(name: str) -> None:
            nonlocal mark
            now = time.monotonic()
            timing[name] = round(now - mark, 3)
            mark = now

        result = None
        try:
            token = await self.token()
            lap("sign_in_s")
            try:
                async with self._client(token.token) as client:
                    lap("session_open_s")
                    if self._tool is None:
                        tools = (await client.list_tools()).tools
                        if len(tools) != 1:
                            raise BackendError(f"Expected one data agent tool, found {len(tools)}")
                        properties = tools[0].input_schema.get("properties") or {}
                        if not properties:
                            raise BackendError("The data agent tool declares no input argument")
                        self._tool = (tools[0].name, next(iter(properties)))
                        lap("tool_discovery_s")
                    name, argument = self._tool
                    result = await client.call_tool(name, {argument: question})
                    lap("upstream_call_s")
                lap("session_close_s")
            except BackendError:
                raise
            except Exception as error:
                log.error("upstream failure after %.1fs: %s", time.monotonic() - started, describe_failure(error))
                raise BackendError(describe_failure(error)) from None
        finally:
            timing["total_s"] = round(time.monotonic() - started, 3)
            timing["is_error"] = result is None or bool(result.is_error)
            if result is not None:
                timing["answer_chars"] = sum(len(b.text) for b in result.content if b.type == "text")
            # One JSON line per call, joined onto the evaluation runs by question hash and time.
            log.info("timing %s", json.dumps(timing))
        answer = "\n".join(block.text for block in result.content if block.type == "text")
        log.info("answered in %.1fs (error=%s, chars=%d)", time.monotonic() - started, result.is_error, len(answer))
        if result.is_error:
            raise BackendError(answer[:_MAX_UPSTREAM_ERROR_CHARS] or "The data agent returned an error")
        return answer
