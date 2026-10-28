# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Distributed tracing of a card: one trace per card, one span per graph step, model call
and tool call (ADR 0017).

Export is OpenTelemetry behind the optional extra `tradvia[otel]`. Without the extra, or
without `TRADVIA_OTEL=1`, `NullTracer` does nothing and behaviour is unchanged.

Every value reaching the exporter goes through `_safe`, which applies `connections.redact`.
Prompts and tool inputs are never attributes: only names, counts, cost and a short redacted
summary.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Protocol

from .connections import redact

MAX_ATTR = 200  # characters of any text that leaves the process
_INSTALL_HINT = "OpenTelemetry tracing needs the optional extra: pip install tradvia[otel]"


class OtelUnavailable(ImportError):
    """The `tradvia[otel]` extra is not installed."""


def _safe(value: Any, secrets: Iterable[str] = ()) -> str | int | float | bool:
    """The only conversion to an exporter attribute: redact first, then truncate."""
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value
    elif isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, default=str, ensure_ascii=False)
    else:
        text = str(value)
    return redact(text, secrets)[:MAX_ATTR]


class SpanHandle(Protocol):
    def set(self, **attrs: Any) -> None: ...


class _NullSpan:
    def set(self, **attrs: Any) -> None:
        pass


class Tracer(Protocol):
    def span(self, name: str, *, card_id: str | None = None, **attrs: Any): ...


class NullTracer:
    """Default: no span, no dependency, no cost."""

    @contextmanager
    def span(self, name: str, *, card_id: str | None = None, **attrs: Any) -> Iterator[_NullSpan]:
        yield _NullSpan()


def _ids(card_id: str) -> tuple[int, int]:
    """Deterministic (trace id, root span id) of a card, so resumed runs share one trace."""
    digest = hashlib.sha256(card_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:16], "big") or 1, int.from_bytes(digest[16:24], "big") or 1


class _OtelSpan:
    def __init__(self, span: Any, secrets: Iterable[str]):
        self._span, self._secrets = span, tuple(secrets)

    def set(self, **attrs: Any) -> None:
        for key, value in attrs.items():
            if value is not None:
                self._span.set_attribute(key, _safe(value, self._secrets))


class OtelTracer:
    """OpenTelemetry tracer with a private provider: it never touches global OTel state."""

    def __init__(self, exporter: Any = None, secrets: Iterable[str] = ()):
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor
            if exporter is None:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                exporter = OTLPSpanExporter()  # reads the standard OTEL_EXPORTER_OTLP_* variables
        except ImportError as e:
            raise OtelUnavailable(_INSTALL_HINT) from e
        self._trace, self._secrets = trace, tuple(secrets)
        self._provider = TracerProvider()
        self._provider.add_span_processor(SimpleSpanProcessor(exporter))
        self._tracer = self._provider.get_tracer("tradvia")

    def _parent(self, card_id: str | None):
        """Context of the card trace, unless a span of this run is already open."""
        if card_id is None or self._trace.get_current_span().get_span_context().is_valid:
            return None
        trace_id, span_id = _ids(card_id)
        parent = self._trace.NonRecordingSpan(self._trace.SpanContext(
            trace_id=trace_id, span_id=span_id, is_remote=True,
            trace_flags=self._trace.TraceFlags(self._trace.TraceFlags.SAMPLED)))
        return self._trace.set_span_in_context(parent)

    @contextmanager
    def span(self, name: str, *, card_id: str | None = None, **attrs: Any) -> Iterator[_OtelSpan]:
        from langgraph.errors import GraphBubbleUp
        label = str(_safe(name, self._secrets))
        with self._tracer.start_as_current_span(
                label, context=self._parent(card_id), record_exception=False,
                set_status_on_exception=False) as raw:
            handle = _OtelSpan(raw, self._secrets)
            if card_id is not None:
                handle.set(**{"card.id": card_id})
            handle.set(**attrs)
            try:
                yield handle
            except GraphBubbleUp:
                raise  # a pause for human input is not a failure
            except Exception as e:
                message = _safe(str(e), self._secrets)
                raw.add_event("exception", {"exception.type": type(e).__name__,
                                            "exception.message": message})
                raw.set_status(self._trace.Status(self._trace.StatusCode.ERROR, str(message)))
                raise


def get_tracer() -> Tracer:
    """`OtelTracer` only with `TRADVIA_OTEL=1` and the extra installed; else `NullTracer`."""
    if os.environ.get("TRADVIA_OTEL") != "1":
        return NullTracer()
    try:
        return OtelTracer()
    except ImportError:
        return NullTracer()
