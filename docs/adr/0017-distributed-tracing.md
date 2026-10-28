# ADR 0017: Distributed tracing of card runs

- Status: Partial (becomes Shipped once merged and verified against a real collector)
- Date: 2026-10-26

## Context

Only aggregate cost and duration were recorded. Answering "why did the agent do X" meant
reading the raw Actions log.

## Decision

- **One trace per card.** The trace id is derived from the card id (SHA-256, 128 bits), so
  the planner, the approval wait and the executor, which run in different invocations after
  an `interrupt`, end up in the same trace.
- **Spans**: `node.<name>` for each graph node, `model.planner` / `model.executor` for
  model runs (model name, `usd`, `tokens_in`, `tokens_out`, `tool_calls`) and `tool.<name>`
  for each tool call of the executor (name and a short summary).
- **Optional extra.** Export is OpenTelemetry behind `tradvia[otel]`; the default package
  gains no dependency. Tracing is on only with `TRADVIA_OTEL=1` and the extra installed;
  otherwise `NullTracer` does nothing and behaviour is unchanged. The SDK uses a private
  provider and never touches global OpenTelemetry state.
- **Redact first.** The single path to the exporter is `tracing._safe`, which runs every
  string and exception message through `connections.redact` and truncates to 200
  characters. Prompts and tool inputs are never attributes.

## Consequences

Dependencies are only needed by those who export: `opentelemetry-sdk` for spans and the
OTLP/HTTP exporter to send them. Spans are exported synchronously (`SimpleSpanProcessor`)
so that short Actions runs lose nothing. Gemini and Codex adapters emit model spans but no
tool spans until their streams are parsed (Planned).
