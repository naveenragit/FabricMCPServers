"""Join the arm 3 proxy's own per-call timing log onto recorded tool calls (ToolTimingRecord)."""

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
