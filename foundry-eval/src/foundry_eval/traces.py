"""Join Foundry's server-side trace (Application Insights) onto recorded attempts.

Needs the `traces` extra (requests, azure-identity). Spans take a few minutes to appear, so run this
after the attempts. The tool span reports success even when the call failed; the agent span carries
the failure (success=False, error.type=tool_user_error), so that is what this reads.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from .records import AttemptRecord, TraceRecord
from .store import RunStore

QUERY_URL = "https://api.applicationinsights.io/v1/apps/{app_id}/query"
TOKEN_SCOPE = "https://api.applicationinsights.io/.default"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]+$")

QueryFn = Callable[[str, str], list[dict[str, Any]]]


def trace_query(response_ids: Iterable[str], lookback: str = "30d") -> str:
    ids = [r for r in response_ids if _SAFE_ID.match(r)]
    quoted = ", ".join(f"'{r}'" for r in ids)
    return (
        f"dependencies | where timestamp > ago({lookback}) "
        "| extend rid = tostring(customDimensions['gen_ai.response.id']) "
        f"| where rid in ({quoted}) "
        "| where name startswith 'invoke_agent' or name startswith 'execute_tool' or name startswith 'chat' "
        "| project rid, operation_Id, name, success, resultCode, duration, timestamp, "
        "tool = tostring(customDimensions['gen_ai.tool.name']), "
        "err = tostring(customDimensions['error.type']), "
        "in_tok = toint(customDimensions['gen_ai.usage.input_tokens']), "
        "out_tok = toint(customDimensions['gen_ai.usage.output_tokens']), "
        "cache_read = toint(customDimensions['gen_ai.usage.cache_read.input_tokens']), "
        "cache_create = toint(customDimensions['gen_ai.usage.cache_creation.input_tokens']), "
        "finish = tostring(customDimensions['gen_ai.response.finish_reasons'])"
    )


def query_app_insights(app_id: str, kql: str) -> list[dict[str, Any]]:
    import requests
    from azure.identity import AzureCliCredential

    token = AzureCliCredential().get_token(TOKEN_SCOPE).token
    response = requests.post(
        QUERY_URL.format(app_id=app_id),
        headers={"Authorization": f"Bearer {token}"},
        json={"query": kql},
        timeout=90,
    )
    response.raise_for_status()
    table = response.json()["tables"][0]
    columns = [c["name"] for c in table["columns"]]
    return [dict(zip(columns, row, strict=True)) for row in table["rows"]]


def _union_s(spans: list[dict[str, Any]]) -> float:
    """Wall time covered by the tool spans in seconds; parallel calls count once."""
    intervals = []
    for s in spans:
        if not s.get("timestamp"):
            return sum(float(x["duration"]) for x in spans) / 1000
        start = datetime.fromisoformat(str(s["timestamp"]).replace("Z", "+00:00")).timestamp()
        intervals.append((start, start + float(s["duration"]) / 1000))
    total, covered = 0.0, None
    for start, end in sorted(intervals):
        if covered is None or start > covered:
            total += end - start
            covered = end
        elif end > covered:
            total += end - covered
            covered = end
    return total


def _is_true(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    return str(value).lower() == "true"


def _start(span: dict[str, Any]) -> float | None:
    if not span.get("timestamp"):
        return None
    return datetime.fromisoformat(str(span["timestamp"]).replace("Z", "+00:00")).timestamp()


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))


def _sum(values: list[int | None]) -> int | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _timeline(agent: dict[str, Any] | None, spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every tool and model-call span in start order, offset from the agent span's start.

    Foundry emits a model-call span only for the last model call, so gaps between spans are listed too: they are the
    earlier model calls (choosing and writing each tool call) plus orchestration.
    """
    origin = _start(agent) if agent else None
    agent_s = float(agent["duration"]) / 1000 if agent else None
    out: list[dict[str, Any]] = []
    cursor = 0.0
    for s in sorted(spans, key=lambda x: _start(x) or 0.0):
        name = str(s["name"])
        start = _start(s)
        offset = round(start - origin, 3) if start is not None and origin is not None else None
        duration = round(float(s["duration"]) / 1000, 3)
        if offset is not None and offset - cursor > 0.05:
            out.append({"kind": "gap", "name": "untraced: earlier model call or orchestration", "start_s": round(cursor, 3),
                        "duration_s": round(offset - cursor, 3)})
        out.append(
            {
                "kind": "tool" if name.startswith("execute_tool") else "model",
                "name": s.get("tool") or name,
                "start_s": offset,
                "duration_s": duration,
                "success": _is_true(s.get("success")),
                "input_tokens": _int(s.get("in_tok")),
                "output_tokens": _int(s.get("out_tok")),
                "cache_read_tokens": _int(s.get("cache_read")),
                "cache_creation_tokens": _int(s.get("cache_create")),
                "finish_reasons": s.get("finish") or None,
            }
        )
        if offset is not None:
            cursor = max(cursor, offset + duration)
    if agent_s is not None and agent_s - cursor > 0.05:
        out.append({"kind": "gap", "name": "untraced: after the last span", "start_s": round(cursor, 3),
                    "duration_s": round(agent_s - cursor, 3)})
    return out


def build_trace_records(
    attempts: Iterable[AttemptRecord], rows: Iterable[dict[str, Any]], *, fetched_at: datetime | None = None
) -> list[TraceRecord]:
    """One record per attempt that has spans; attempts with none yet are left out so they can be retried."""
    by_response: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_response[row["rid"]].append(row)
    records = []
    for attempt in attempts:
        spans = by_response.get(attempt.response_id or "")
        if not spans:
            continue
        agent = next((s for s in spans if str(s["name"]).startswith("invoke_agent")), None)
        tools = [s for s in spans if str(s["name"]).startswith("execute_tool")]
        chats = [s for s in spans if str(s["name"]).startswith("chat")]
        error_type = ((agent or {}).get("err") or "") or None
        agent_s = float(agent["duration"]) / 1000 if agent else None
        traced_s = _union_s(tools + chats) if tools or chats else 0.0
        records.append(
            TraceRecord(
                attempt_id=attempt.attempt_id,
                run_id=attempt.run_id,
                arm=attempt.arm,
                response_id=attempt.response_id or "",
                trace_id=(agent or spans[0])["operation_Id"],
                agent_span_success=_is_true((agent or {}).get("success")),
                agent_result_code=(str(agent["resultCode"]) if agent else None),
                error_type=error_type,
                agent_span_s=agent_s,
                tool_spans=len(tools),
                tool_names=[s["tool"] for s in tools if s.get("tool")],
                tool_time_s=_union_s(tools) if tools else None,
                tool_span_s=[float(s["duration"]) / 1000 for s in tools],
                tool_error=bool(error_type and error_type.startswith("tool"))
                or any(_is_true(s.get("success")) is False for s in tools),
                model_spans=len(chats),
                model_time_s=_union_s(chats) if chats else None,
                untraced_s=max(0.0, agent_s - traced_s) if agent_s is not None else None,
                model_input_tokens=_sum([_int(s.get("in_tok")) for s in chats]),
                model_output_tokens=_sum([_int(s.get("out_tok")) for s in chats]),
                model_cache_read_tokens=_sum([_int(s.get("cache_read")) for s in chats]),
                model_cache_creation_tokens=_sum([_int(s.get("cache_create")) for s in chats]),
                spans=_timeline(agent, tools + chats),
                fetched_at=fetched_at or datetime.now(UTC),
            )
        )
    return records


def enrich_run(
    store: RunStore, app_id: str, *, query: QueryFn = query_app_insights, chunk: int = 100, refresh: bool = False
) -> tuple[list[TraceRecord], int]:
    """Fetch and store trace records for untraced attempts (every attempt with refresh). Returns (records, without_spans)."""
    traced = set() if refresh else {t.attempt_id for t in store.traces()}
    pending = [a for a in store.attempts() if a.response_id and a.attempt_id not in traced]
    records: list[TraceRecord] = []
    for start in range(0, len(pending), chunk):
        batch = pending[start : start + chunk]
        rows = query(app_id, trace_query(a.response_id or "" for a in batch))
        records += build_trace_records(batch, rows)
    store.add_traces(records)
    store.refresh_rollups()
    return records, len(pending) - len(records)
