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
        "| where name startswith 'invoke_agent' or name startswith 'execute_tool' "
        "| project rid, operation_Id, name, success, resultCode, duration, timestamp, "
        "tool = tostring(customDimensions['gen_ai.tool.name']), "
        "err = tostring(customDimensions['error.type'])"
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
        error_type = ((agent or {}).get("err") or "") or None
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
                agent_span_s=(float(agent["duration"]) / 1000 if agent else None),
                tool_spans=len(tools),
                tool_names=[s["tool"] for s in tools if s.get("tool")],
                tool_time_s=_union_s(tools) if tools else None,
                tool_span_s=[float(s["duration"]) / 1000 for s in tools],
                tool_error=bool(error_type and error_type.startswith("tool"))
                or any(_is_true(s.get("success")) is False for s in tools),
                fetched_at=fetched_at or datetime.now(UTC),
            )
        )
    return records


def enrich_run(
    store: RunStore, app_id: str, *, query: QueryFn = query_app_insights, chunk: int = 100
) -> tuple[list[TraceRecord], int]:
    """Fetch and store trace records for the run's untraced attempts. Returns (records, attempts_without_spans)."""
    traced = {t.attempt_id for t in store.traces()}
    pending = [a for a in store.attempts() if a.response_id and a.attempt_id not in traced]
    records: list[TraceRecord] = []
    for start in range(0, len(pending), chunk):
        batch = pending[start : start + chunk]
        rows = query(app_id, trace_query(a.response_id or "" for a in batch))
        records += build_trace_records(batch, rows)
    store.add_traces(records)
    store.refresh_rollups()
    return records, len(pending) - len(records)
