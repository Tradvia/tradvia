# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Tracing (ADR 0012): one trace per card, redact-first, no change without the extra."""

import sys

import pytest
from langgraph.types import Command

from tradvia import tracing
from tradvia.graph import build_graph
from tradvia.tracing import NullTracer, OtelTracer, OtelUnavailable, _safe, get_tracer

SECRET = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
REGISTERED = "hunter2-registered-value"
PROMPT = "PROMPT-TEXT-THAT-MUST-NOT-LEAVE"


class FakeGitHub:
    def __init__(self):
        self.prs = []

    def get_issue(self, number):
        return {"number": number, "title": "Add hello world",
                "body": f"Say hi. {SECRET} {PROMPT}", "url": "https://example.test/issues/1"}

    def create_branch(self, name):
        pass

    def put_file(self, branch, path, content, message):
        pass

    def open_pull_request(self, branch, title, body):
        self.prs.append(body)
        return "https://example.test/pull/2"

    def comment(self, number, body):
        pass


class FakeChat:
    def post(self, text):
        return "1"


def planner(title, body, number):
    return {"summary": "s", "plan": ["a"], "question": "ok?", "file_path": "hello.txt",
            "file_content": "hi", "_tool_calls": 2,
            "_cost": {"usd": 0.01, "tokens_in": 100, "tokens_out": 50}}


class Executor:
    name = "fake-executor"

    def __init__(self, tmp_path, fail=False):
        self.tmp, self.fail = tmp_path, fail

    def execute(self, card, cwd=".", budget_notice="", on_tool=None):
        on_tool("Read", f"/repo/{REGISTERED}.txt {SECRET}")
        on_tool("Bash", "ls")
        if self.fail:
            raise RuntimeError(f"boom {SECRET} {REGISTERED}")
        (self.tmp / "out.txt").write_text("x")
        return {"changed_files": ["out.txt"], "untracked_files": [], "cost_usd": 0.02,
                "tokens_in": 10, "tokens_out": 5, "duration_s": 3.0, "tool_calls": 2,
                "summary": "done"}


def run_card(app, issue=1):
    config = {"configurable": {"thread_id": f"CARD-{issue}"}}
    app.invoke({"id": f"CARD-{issue}", "status": "BACKLOG",
                "source": {"provider": "github", "issue_number": issue}}, config)
    return app.invoke(Command(resume={"text": "Aprovado", "by": "h"}), config)


@pytest.fixture
def memory():
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    return InMemorySpanExporter()


def dump(spans):
    out = []
    for s in spans:
        out += [s.name, str(s.status.description), *map(str, s.attributes.values())]
        for ev in s.events:
            out += [ev.name, *map(str, ev.attributes.values())]
    return "\n".join(out)


def test_one_trace_per_card_with_node_model_and_tool_spans(memory, tmp_path):
    tracer = OtelTracer(memory)
    app = build_graph(FakeGitHub(), FakeChat(), planner=planner, executor=Executor(tmp_path),
                      cwd=str(tmp_path), tracer=tracer)
    run_card(app)  # planner and executor run in different invocations (interrupt in between)
    spans = memory.get_finished_spans()
    assert len({s.context.trace_id for s in spans}) == 1
    names = {s.name for s in spans}
    assert {"node.start_work", "node.plan", "node.open_pr", "model.planner", "model.executor",
            "tool.Read", "tool.Bash"} <= names
    assert any(s.attributes.get("card.id") == "CARD-1" and s.attributes.get("issue.number") == 1
               for s in spans if s.name == "node.plan")
    model = next(s for s in spans if s.name == "model.executor")
    assert model.attributes["usd"] == 0.02 and model.attributes["tool_calls"] == 2
    other = OtelTracer(memory)
    with other.span("x", card_id="CARD-2"):
        pass
    assert memory.get_finished_spans()[-1].context.trace_id != spans[0].context.trace_id


def test_nothing_secret_or_prompt_reaches_the_exporter(memory, tmp_path):
    tracer = OtelTracer(memory, secrets=[REGISTERED])
    app = build_graph(FakeGitHub(), FakeChat(), planner=planner, executor=Executor(tmp_path),
                      cwd=str(tmp_path), tracer=tracer)
    run_card(app)
    text = dump(memory.get_finished_spans())
    assert text
    for leaked in (SECRET, REGISTERED, PROMPT):
        assert leaked not in text


def test_exception_message_is_redacted(memory, tmp_path):
    tracer = OtelTracer(memory, secrets=[REGISTERED])
    app = build_graph(FakeGitHub(), FakeChat(), planner=planner,
                      executor=Executor(tmp_path, fail=True), cwd=str(tmp_path), tracer=tracer)
    run_card(app)
    spans = memory.get_finished_spans()
    failed = next(s for s in spans if s.name == "model.executor")
    assert failed.status.status_code.name == "ERROR"
    assert any(ev.name == "exception" for ev in failed.events)
    assert SECRET not in dump(spans) and REGISTERED not in dump(spans)
    assert "boom" in dump(spans)


def test_safe_keeps_scalars_flattens_and_truncates():
    assert _safe(3) == 3 and _safe(1.5) == 1.5 and _safe(True) is True
    assert SECRET not in _safe({"k": SECRET})
    assert SECRET not in _safe([SECRET])
    assert len(_safe("x" * 1000)) == tracing.MAX_ATTR


def test_null_tracer_and_default_change_nothing(memory, tmp_path, monkeypatch):
    monkeypatch.delenv("TRADVIA_OTEL", raising=False)
    assert isinstance(get_tracer(), NullTracer)
    with NullTracer().span("x", card_id="c", a=1) as sp:
        sp.set(b=2)
    results = []
    for tracer in (None, NullTracer(), OtelTracer(memory)):
        app = build_graph(FakeGitHub(), FakeChat(), planner=planner,
                          executor=Executor(tmp_path), cwd=str(tmp_path), tracer=tracer)
        r = run_card(app)
        results.append({k: r[k] for k in ("status", "branch", "pull_request_url", "cost", "usage")})
    assert results[0] == results[1] == results[2]
    assert [s for s in memory.get_finished_spans()]  # only the explicit OtelTracer exported


def test_without_the_extra_falls_back_and_fails_friendly(monkeypatch):
    for mod in [m for m in sys.modules if m == "opentelemetry" or m.startswith("opentelemetry.")]:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setitem(sys.modules, "opentelemetry", None)  # `import opentelemetry...` fails
    monkeypatch.setenv("TRADVIA_OTEL", "1")
    assert isinstance(get_tracer(), NullTracer)
    with pytest.raises(OtelUnavailable, match=r"pip install tradvia\[otel\]"):
        OtelTracer()
