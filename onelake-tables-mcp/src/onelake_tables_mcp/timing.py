"""Server-side timing: one span per tool call, sent to Application Insights.

Enabled when APPLICATIONINSIGHTS_CONNECTION_STRING is set. Each span records the tool name, a SHA-256 of the query (so
evaluation runs can join it to their tool calls without the query text leaving the server), the time spent in each
phase, result sizes, and the caller's W3C traceparent when one is sent. Query text, results and tokens are never recorded.
"""

from __future__ import annotations

import contextvars
import hashlib
import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

_current: contextvars.ContextVar[ToolTiming | None] = contextvars.ContextVar("tool_timing", default=None)
_tracer: Any = None


def configure(service_name: str) -> bool:
    """Start exporting spans; returns False when no connection string or exporter is available."""
    global _tracer
    connection = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not connection:
        return False
    try:
        from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(AzureMonitorTraceExporter(connection_string=connection)))
    _tracer = provider.get_tracer(service_name)
    return True


class ToolTiming:
    def __init__(self, tool: str, query: str | None) -> None:
        self.started = time.perf_counter()
        self.phases: dict[str, float] = {}
        self.attributes: dict[str, Any] = {"mcp.tool.name": tool}
        if query is not None:
            self.attributes["mcp.query.sha256"] = hashlib.sha256(query.encode()).hexdigest()
            self.attributes["mcp.query.chars"] = len(query)

    def add_phase(self, name: str, seconds: float) -> None:
        self.phases[name] = self.phases.get(name, 0.0) + seconds


@contextmanager
def tool_call(tool: str, query: str | None = None, headers: Mapping[str, str] | None = None) -> Iterator[ToolTiming]:
    """Time one tool call; phases recorded with `phase()` anywhere below it (threads included) attach to it."""
    timing = ToolTiming(tool, query)
    token = _current.set(timing)
    span = None
    if _tracer is not None:
        from opentelemetry.propagate import extract

        parent = extract(dict(headers or {})) if headers and "traceparent" in headers else None
        span = _tracer.start_span(f"mcp.tool {tool}", context=parent)
        if headers and headers.get("traceparent"):
            timing.attributes["mcp.caller.traceparent"] = headers["traceparent"]
    try:
        yield timing
    finally:
        _current.reset(token)
        if span is not None:
            for key, value in timing.attributes.items():
                span.set_attribute(key, value)
            for name, seconds in timing.phases.items():
                span.set_attribute(f"mcp.phase.{name}_s", round(seconds, 4))
            span.set_attribute("mcp.total_s", round(time.perf_counter() - timing.started, 4))
            span.end()


@contextmanager
def phase(name: str) -> Iterator[None]:
    """Add the time spent in the block to the current tool call; does nothing outside one."""
    timing = _current.get()
    started = time.perf_counter()
    try:
        yield
    finally:
        if timing is not None:
            timing.add_phase(name, time.perf_counter() - started)


def note(**attributes: Any) -> None:
    """Attach sizes or outcomes (never content) to the current tool call."""
    timing = _current.get()
    if timing is not None:
        timing.attributes.update({f"mcp.{key}": value for key, value in attributes.items() if value is not None})
