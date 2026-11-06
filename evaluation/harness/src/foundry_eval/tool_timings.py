"""Join tool servers' own timings onto recorded tool calls (ToolTimingRecord).

Arm 3: the local proxy's log. Arms 1 and 2: the DAX and SQL servers' spans in Application Insights.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

from .records import ToolTimingRecord
from .store import RunStore

_MARKER = "timing {"
_FIELDS = ("sign_in_s", "session_open_s", "tool_discovery_s", "upstream_call_s", "session_close_s", "total_s")


def read_proxy_log(path: Path | str) -> list[dict]:
    entries = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        at = line.find(_MARKER)
        if at < 0:
            continue
        try:
            entry = json.loads(line[at + len("timing ") :])
        except ValueError:
            continue
        if entry.get("event") == "timing" and entry.get("started_at"):
            entry["started"] = datetime.fromisoformat(entry["started_at"])
            entries.append(entry)
    return entries


def join_proxy_timings(store: RunStore, entries: Iterable[dict], *, source: str, window_s: float = 300.0) -> int:
    """Match each Data Agent tool call to the log entry with the same question hash that started nearest to it."""
    done = {(t.attempt_id, t.step_index) for t in store.tool_timings()}
    by_hash: dict[str, list[dict]] = {}
    for entry in entries:
        by_hash.setdefault(entry["question_sha256"], []).append(entry)
    used: set[int] = set()
    steps_by_attempt: dict[str, list] = {}
    for step in store.steps():
        if step.kind == "tool_call" and step.query_text:
            steps_by_attempt.setdefault(step.attempt_id, []).append(step)
    records = []
    for attempt in store.attempts():
        for step in steps_by_attempt.get(attempt.attempt_id, []):
            if (attempt.attempt_id, step.index) in done:
                continue
            expected = attempt.started_at + timedelta(seconds=step.start_s or 0.0)
            candidates = [
                e for e in by_hash.get(hashlib.sha256(step.query_text.encode()).hexdigest(), []) if id(e) not in used
            ]
            if not candidates:
                continue
            best = min(candidates, key=lambda e: abs((e["started"] - expected).total_seconds()))
            if abs((best["started"] - expected).total_seconds()) > window_s:
                continue
            used.add(id(best))
            records.append(
                ToolTimingRecord(
                    attempt_id=attempt.attempt_id,
                    run_id=attempt.run_id,
                    arm=attempt.arm,
                    step_index=step.index,
                    tool_name=step.tool_name or "",
                    source=source,
                    started_at=best["started"],
                    is_error=best.get("is_error"),
                    answer_chars=best.get("answer_chars"),
                    **{field: best.get(field) for field in _FIELDS},
                )
            )
    store.add_tool_timings(records)
    return len(records)


def server_span_query(start: datetime, end: datetime) -> str:
    """The DAX and SQL servers' per-tool-call spans (see each server's timing.py) in a time window."""
    return (
        f"dependencies | where timestamp between (datetime({start:%Y-%m-%dT%H:%M:%S}Z) .. datetime({end:%Y-%m-%dT%H:%M:%S}Z)) "
        "| where name startswith 'mcp.tool ' "
        "| project timestamp, operation_Id, role = cloud_RoleName, customDimensions"
    )


def _span_entry(row: dict) -> dict | None:
    dims = row.get("customDimensions")
    dims = json.loads(dims) if isinstance(dims, str) else (dims or {})
    tool = dims.get("mcp.tool.name")
    if not tool or not row.get("timestamp"):
        return None
    phases = {
        key[len("mcp.phase.") : -len("_s")]: float(value)
        for key, value in dims.items()
        if key.startswith("mcp.phase.") and key.endswith("_s")
    }
    total = dims.get("mcp.total_s")
    return {
        "tool": tool,
        "sha": dims.get("mcp.query.sha256"),
        "operation_id": row.get("operation_Id"),
        "started": datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00")),
        "total_s": float(total) if total not in (None, "") else None,
        "phases": phases,
        "rows": int(float(dims["mcp.rows"])) if dims.get("mcp.rows") not in (None, "") else None,
        "response_bytes": int(float(dims["mcp.response_bytes"])) if dims.get("mcp.response_bytes") not in (None, "") else None,
        "is_error": str(dims.get("mcp.is_error", "")).lower() == "true" if dims.get("mcp.is_error") is not None else None,
        "error_code": dims.get("mcp.error_code"),
    }


def join_server_spans(store: RunStore, rows: Iterable[dict], *, source: str, window_s: float = 180.0) -> tuple[int, int]:
    """Match each DAX/SQL tool call to its server span: by Foundry trace id when the server received it, else by tool,
    query hash and nearest start. Returns (joined, tool calls left without a span)."""
    entries = [e for e in (_span_entry(r) for r in rows) if e]
    done = {(t.attempt_id, t.step_index) for t in store.tool_timings()}
    trace_ids = {t.attempt_id: t.trace_id for t in store.traces()}
    steps_by_attempt: dict[str, list] = {}
    for step in store.steps():
        if step.kind == "tool_call" and step.tool_name:
            steps_by_attempt.setdefault(step.attempt_id, []).append(step)
    used: set[int] = set()
    records, missing = [], 0
    for attempt in store.attempts():
        for step in steps_by_attempt.get(attempt.attempt_id, []):
            if (attempt.attempt_id, step.index) in done:
                continue
            candidates = [e for e in entries if e["tool"] == step.tool_name and id(e) not in used]
            linked = "trace_id"
            by_trace = [e for e in candidates if trace_ids.get(attempt.attempt_id) and e["operation_id"] == trace_ids[attempt.attempt_id]]
            if by_trace:
                candidates = by_trace
            else:
                linked = "query_hash_and_time"
                if step.query_text:
                    sha = hashlib.sha256(step.query_text.encode()).hexdigest()
                    candidates = [e for e in candidates if e["sha"] == sha]
            expected = attempt.started_at + timedelta(seconds=step.start_s or 0.0)
            candidates = [e for e in candidates if abs((e["started"] - expected).total_seconds()) <= window_s]
            if not candidates:
                missing += 1
                continue
            best = min(candidates, key=lambda e: abs((e["started"] - expected).total_seconds()))
            used.add(id(best))
            records.append(
                ToolTimingRecord(
                    attempt_id=attempt.attempt_id,
                    run_id=attempt.run_id,
                    arm=attempt.arm,
                    step_index=step.index,
                    tool_name=step.tool_name,
                    source=source,
                    started_at=best["started"],
                    total_s=best["total_s"],
                    phases=best["phases"],
                    rows=best["rows"],
                    response_bytes=best["response_bytes"],
                    is_error=best["is_error"],
                    error_code=best["error_code"],
                    linked_by=linked,
                )
            )
    store.add_tool_timings(records)
    return len(records), missing
