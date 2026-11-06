"""Reviewed native response semantics; fail closed on unknown result shapes."""

from typing import Any

from mcp.types import CallToolResult, TextContent

from .backend import BackendError
from .contracts import RoleBinding


def native_payload(result: CallToolResult) -> Any:
    import json

    if result.structured_content is not None:
        return result.structured_content
    if len(result.content) == 1 and isinstance(result.content[0], TextContent):
        try:
            return json.loads(result.content[0].text)
        except ValueError:
            pass
    raise BackendError("Native response shape is unsupported; review the adapter before enabling this role")


def path_values(value: Any, path: list[str]) -> list[Any]:
    if not path:
        return [value]
    key, *rest = path
    if key == "*" and isinstance(value, list):
        return [item for child in value for item in path_values(child, rest)]
    if isinstance(value, dict) and key in value:
        return path_values(value[key], rest)
    return []


def verify_native_response(result: CallToolResult, role: RoleBinding, max_rows: int) -> None:
    payload = native_payload(result)
    for path in role.response_error_paths:
        if any(value not in (None, False, "", [], {}) for value in path_values(payload, path)):
            raise BackendError("Native response contains an error/partial-result indicator; no data returned")
    # An extra conservative guard: an unexpected nested error is never proof of
    # schema authorization, even if omitted accidentally from a reviewed mapping.
    def has_error(value: Any) -> bool:
        if isinstance(value, dict):
            return any(
                (key.lower() in {"error", "errors"} and child not in (None, False, "", [], {}))
                or has_error(child) for key, child in value.items()
            )
        return isinstance(value, list) and any(has_error(child) for child in value)

    if has_error(payload):
        raise BackendError("Native response contains an error indicator; no data returned")
    if role.response_success_path:
        values = path_values(payload, role.response_success_path)
        if len(values) != 1 or values[0] != role.response_success_value:
            raise BackendError("Native success condition was not established")
    if role.role == "execute_query":
        rows = [item for path in role.response_rows_paths for item in path_values(payload, path)]
        if not rows or any(not isinstance(item, list) for item in rows):
            raise BackendError("Configured native row locations were not present")
        if sum(len(item) for item in rows) > max_rows:
            raise BackendError("Result exceeds the configured total row limit; no partial result returned")