"""Server-side timing: phases from worker threads attach to the tool call's span, and no query text is recorded."""

from __future__ import annotations

import asyncio
import hashlib

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from onelake_tables_mcp import timing


def test_tool_call_span_carries_phases_sizes_and_query_hash(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(timing, "_tracer", provider.get_tracer("test"))

    def work() -> None:
        with timing.phase("execute"):
            pass
        timing.note(rows=3)

    async def call() -> None:
        headers = {"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}
        with timing.tool_call("execute_sql", "SELECT 1", headers):
            await asyncio.to_thread(work)

    asyncio.run(call())
    [span] = exporter.get_finished_spans()
    attrs = dict(span.attributes)
    assert attrs["mcp.query.sha256"] == hashlib.sha256(b"SELECT 1").hexdigest()
    assert "mcp.phase.execute_s" in attrs and attrs["mcp.rows"] == 3 and "mcp.total_s" in attrs
    assert "SELECT 1" not in str(attrs.values()), "query text must not leave the server"
    assert format(span.context.trace_id, "032x") == "0af7651916cd43dd8448eb211c80319c", "parented on the caller's trace"


def test_phase_outside_a_tool_call_is_harmless():
    with timing.phase("anything"):
        pass
    timing.note(rows=1)
