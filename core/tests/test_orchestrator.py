# SPDX-License-Identifier: FSL-1.1-ALv2
"""Orchestrator conversation (ADR 0007), offline: a scripted model replaces Claude Code and a
fake runner replaces `make check`, so nothing here needs a key, a network or a checkout."""

import json
import subprocess
import sys
import types
from types import SimpleNamespace

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from starlette.testclient import TestClient  # noqa: E402

from test_auth import (  # noqa: E402,F401
    A,
    AGENT_PLAN,
    B,
    BASE,
    ORIGIN,
    SERVER_TOKEN,
    TOKEN,
    as_role,
    client,
    clock,
    csrf_headers,
    env,
    key,
    login,
    post,
    store,
)
from test_pulls import PullGitHub  # noqa: E402
from tradvia import orchestrator  # noqa: E402
from tradvia.api import create_app  # noqa: E402
from tradvia.backlog import READY_LABEL  # noqa: E402
from tradvia.orchestrator import Budget, ToolContext, run  # noqa: E402

CHAT = BASE + "/chat"


class ChatGitHub(PullGitHub):
    def update_issue(self, token, repo, number, title, body):
        self.calls.append(("update_issue", token, repo, number, title, body))
        issue = self.issues[number]
        if title is not None:
            issue["title"] = title
        return {"id": 1000 + number, "state": "open", "labels": [], "updated_at": "2026-01-02T00:00:00Z",
                "title": issue.get("title", ""), "number": number}


class ScriptedModel:
    """Answers the orchestrator protocol from a script and remembers every prompt."""

    def __init__(self, steps, usd=0.01):
        self.steps, self.usd, self.prompts, self.timeouts = list(steps), usd, [], []

    def __call__(self, prompt, timeout):
        self.prompts.append(prompt)
        self.timeouts.append(timeout)
        step = dict(self.steps.pop(0) if self.steps else {"reply": "done"})
        usd = step.pop("usd", self.usd)
        return {"result": json.dumps(step), "total_cost_usd": usd,
                "usage": {"input_tokens": 100, "output_tokens": 20}}


def tool(name, **args):
    return {"tool": name, "args": args}


class FakeRunner:
    def __init__(self, code=0, output="all green"):
        self.calls, self.code, self.output = [], code, output

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        code = self.code if cmd[:2] == ["make", "check"] else 0
        return SimpleNamespace(returncode=code, stdout=self.output, stderr="")

    def commands(self):
        return [c for c, _ in self.calls]


def issue_rec(number, title, state="open", labels=()):
    return {"id": 1000 + number, "number": number, "title": title, "state": state,
            "labels": list(labels), "author": "ana", "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z", "closed_at": None,
            "html_url": f"https://github.com/x/issues/{number}", "state_reason": None}


@pytest.fixture
def gh():
    g = ChatGitHub()
    g.issues[8] = {"number": 8, "state": "open", "labels": []}
    g.comments[8] = [AGENT_PLAN]
    return g


def make_ctx(store, gh, repo=A, runner=None, cwd=None):
    return ToolContext(store=store, project_id=store.ensure_project(repo), repo=repo, github=gh,
                       token=TOKEN, login="ana", runner=runner or FakeRunner(), cwd=cwd,
                       clock=lambda: 1.0)


def send(ctx, text, model, budget=None):
    ctx.store.append_message(ctx.project_id, {"author": "ana", "kind": "message", "text": text})
    run(ctx, model, budget)
    return ctx.store.list_messages(ctx.project_id, 100)


def audit_of(store, repo=A):
    pid = store.ensure_project(repo)
    return [r for r in store.audit if r["project_id"] == pid and r["actor_login"] == "orchestrator"]


def pendings(store, repo=A):
    return [m for m in store.list_messages(store.ensure_project(repo), 100) if m["kind"] == "pending"]


# --- a message leads to the right tool ------------------------------------------------------


def test_a_message_reads_the_project_and_replies_with_references(store, gh):
    ctx = make_ctx(store, gh)
    store.upsert_issue(ctx.project_id, issue_rec(7, "Fix the login"))
    model = ScriptedModel([tool("read_cards"), {"reply": "One open card.", "refs": [{"type": "card", "number": 7}]}])
    messages = send(ctx, "what is open?", model)
    last = messages[-1]
    assert (last["author"], last["kind"], last["text"]) == ("orchestrator", "reply", "One open card.")
    assert last["refs"] == [{"type": "card", "number": 7}]
    assert "Fix the login" in model.prompts[1] and "what is open?" in model.prompts[0]
    assert audit_of(store)[0]["action"] == "orchestrator:read_cards" and audit_of(store)[0]["result"] == "ok"


def test_the_cost_of_a_run_is_recorded_per_project(store, gh):
    ctx = make_ctx(store, gh)
    send(ctx, "hi", ScriptedModel([tool("read_backlog"), {"reply": "ok"}], usd=0.25))
    cost = store.project_cost(ctx.project_id)
    assert cost == {"usd": 0.5, "tokens_in": 200, "tokens_out": 40, "runs": 1}
    assert store.project_cost(store.ensure_project(B))["runs"] == 0


def test_write_tools_act_with_the_user_token(store, gh):
    ctx = make_ctx(store, gh)
    store.set_backlog(ctx.project_id, [{"issue_number": 7}])
    model = ScriptedModel([
        tool("create_card", title="Novo", description="Depende do #7."),
        tool("edit_card", number=7, title="Renamed"),
        tool("start_card", number=7),
        tool("reorder_backlog", number=101, direction="up"),
        {"reply": "done"},
    ])
    messages = send(ctx, "organize", model)
    (created,) = gh.writes("create_issue")
    assert created[1] == TOKEN and created[3] == "Novo"
    assert gh.writes("update_issue") == [("update_issue", TOKEN, A, 7, "Renamed", None)]
    assert gh.writes("add_label") == [("add_label", TOKEN, A, 7, READY_LABEL)]
    assert [i["issue_number"] for i in store.list_backlog(ctx.project_id)] == [101, 7]
    assert {"type": "card", "number": 101} in messages[-1]["refs"]
    assert [r["result"] for r in audit_of(store)] == ["ok"] * 4


def test_reply_plan_accepts_only_an_adjustment(store, gh):
    ctx = make_ctx(store, gh)
    send(ctx, "tweak the plan", ScriptedModel([
        tool("reply_plan", number=8, decision="adjust", text="Add a test"), {"reply": "posted"}]))
    assert gh.writes("comment") == [("comment", TOKEN, A, 8, "Ajuste: Add a test")]


# --- the human keeps approval, merge and deploy (ADR 0004) ------------------------------------


def test_trying_to_approve_a_plan_is_refused_and_queued_for_the_human(store, gh):
    ctx = make_ctx(store, gh)
    model = ScriptedModel([
        tool("reply_plan", number=8, decision="approve"),
        tool("reply_plan", number=8, decision="reject"),
        tool("approve_plan", number=8),
        {"reply": "You need to approve it."},
    ])
    messages = send(ctx, "approve card 8", model)
    assert not gh.writes("comment")
    assert "Aprovado" not in json.dumps(gh.calls)
    assert "ADR 0004" in model.prompts[1]  # the refusal went back to the model
    assert [r["result"] for r in audit_of(store)] == ["denied"] * 3
    assert all(m["refs"] == [{"type": "card", "number": 8, "action": "approve_plan"}] for m in pendings(store))
    assert len(pendings(store)) == 3 and messages[-1]["kind"] == "reply"


def test_merge_and_deploy_have_no_tool_and_become_pending_items(store, gh):
    ctx = make_ctx(store, gh)
    send(ctx, "ship it", ScriptedModel([
        tool("merge_pr", number=12), tool("approve_deploy", run_id=55), tool("deploy"), {"reply": "ask them"}]))
    assert not gh.writes("merge_pull") and not gh.writes("review_pending_deployment")
    assert not gh.writes("dispatch_workflow")
    refs = [m["refs"] for m in pendings(store)]
    assert refs == [[{"type": "pr", "number": 12, "action": "merge_pr"}],
                    [{"type": "deploy", "number": 55, "action": "approve_deploy"}], []]
    assert "merge_pr" not in orchestrator.TOOLS and "approve_plan" not in orchestrator.TOOLS


def test_unknown_tools_and_unexpected_arguments_are_refused(store, gh):
    ctx = make_ctx(store, gh)
    model = ScriptedModel([
        tool("run_shell", command="rm -rf /"),
        tool("read_cards", repo=B),
        tool("start_card", number=7, repo=B),
        {"reply": "sorry"},
    ])
    send(ctx, "do things", model)
    assert [r["result"] for r in audit_of(store)] == ["denied"] * 3
    assert not gh.calls


# --- isolation between projects ---------------------------------------------------------------


def test_a_project_cannot_read_or_write_another_projects_data(store, gh):
    a, b = make_ctx(store, gh, A), make_ctx(store, gh, B)
    store.upsert_issue(a.project_id, issue_rec(1, "Secret card of A"))
    store.set_backlog(a.project_id, [{"issue_number": 1}])
    store.upsert_issue(b.project_id, issue_rec(2, "Card of B"))
    store.append_message(a.project_id, {"author": "ana", "text": "private note of A"})
    store.append_message(a.project_id, {"author": "ana", "text": "x", "kind": "message"})
    model = ScriptedModel([tool("read_cards"), tool("read_backlog"), tool("create_card", title="B only"),
                           {"reply": "ok"}])
    send(b, "list", model)
    prompt = " ".join(model.prompts)
    assert "Card of B" in prompt and "Secret card of A" not in prompt and "private note" not in prompt
    assert [c[2] for c in gh.writes("create_issue")] == [B]
    assert store.list_backlog(a.project_id) == [{"issue_number": 1, "position": 0, "depends_on": [], "note": ""}]
    assert [i["issue_number"] for i in store.list_backlog(b.project_id)] == [101]
    assert all(m["author"] != "orchestrator" for m in store.list_messages(a.project_id, 100))
    assert {r["project_id"] for r in audit_of(store, B)} == {b.project_id}
    assert audit_of(store, A) == []


# --- budget ---------------------------------------------------------------------------------


def test_an_exceeded_budget_ends_the_run_with_a_warning(store, gh):
    ctx = make_ctx(store, gh)
    model = ScriptedModel([{**tool("create_card", title="never"), "usd": 5.0}])
    messages = send(ctx, "go", model, Budget(usd=1.0))
    last = messages[-1]
    assert last["kind"] == "warning" and "cost limit" in last["text"] and last["cost_usd"] == 5.0
    assert not gh.writes("create_issue") and len(model.prompts) == 1


def test_the_turn_limit_ends_the_run_with_a_warning(store, gh):
    ctx = make_ctx(store, gh)
    model = ScriptedModel([tool("read_backlog")] * 10)
    last = send(ctx, "loop", model, Budget(max_turns=3))[-1]
    assert last["kind"] == "warning" and "turn limit (3)" in last["text"] and len(model.prompts) == 3


def test_the_token_limit_and_the_time_limit_end_the_run(store, gh):
    ctx = make_ctx(store, gh)
    last = send(ctx, "big", ScriptedModel([tool("read_backlog")] * 5), Budget(tokens=100))[-1]
    assert last["kind"] == "warning" and "token limit" in last["text"]
    ticks = iter([0, 0, 1000, 1000])
    ctx.store.append_message(ctx.project_id, {"author": "ana", "text": "slow"})
    run(ctx, ScriptedModel([tool("read_backlog")] * 5), Budget(timeout_s=10), monotonic=lambda: next(ticks))
    final = store.list_messages(ctx.project_id, 100)[-1]
    assert final["kind"] == "warning" and "time limit" in final["text"]


def test_budget_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("TRADVIA_ORCH_MAX_TURNS", "4")
    monkeypatch.setenv("TRADVIA_ORCH_BUDGET_USD", "0.5")
    monkeypatch.setenv("TRADVIA_ORCH_TIMEOUT", "bad")
    b = Budget.from_env()
    assert (b.max_turns, b.usd, b.timeout_s) == (4, 0.5, orchestrator.DEFAULT_TIMEOUT)


def test_a_failing_model_ends_with_a_warning_not_a_crash(store, gh):
    def broken(prompt, timeout):
        raise RuntimeError("Claude Code timed out after 5s")

    last = send(make_ctx(store, gh), "hi", broken)[-1]
    assert last["kind"] == "warning" and "timed out" in last["text"]


# --- no credentials in the agent's context ---------------------------------------------------


def test_no_token_or_secret_reaches_the_model_call(store, gh, monkeypatch):
    monkeypatch.delenv("TRADVIA_OFFLINE")
    monkeypatch.setenv("GITHUB_TOKEN", SERVER_TOKEN)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:dbpass@h/db")
    litellm = fake_litellm(monkeypatch, json.dumps({"reply": "ok"}))
    ctx = make_ctx(store, gh, cwd="/runner/checkout")
    send(ctx, "hello", orchestrator.litellm_model("anthropic/claude-sonnet-5-5", "sk-project"))
    (call,) = litellm.calls
    blob = json.dumps(call, default=str)
    assert TOKEN not in blob and SERVER_TOKEN not in blob and "dbpass" not in blob
    assert call["api_key"] == "sk-project"


def test_the_orchestrator_is_off_in_offline_mode(store, gh):
    model = orchestrator.litellm_model("anthropic/claude-sonnet-5-5", "sk-test")
    last = send(make_ctx(store, gh), "hi", model)[-1]
    assert last["kind"] == "warning" and "offline" in last["text"]


def test_a_run_without_a_model_only_says_the_chat_needs_an_api_key(store, gh):
    ctx = make_ctx(store, gh)
    store.append_message(ctx.project_id, {"author": "ana", "kind": "message", "text": "hi"})
    orchestrator.run(ctx, None)
    last = store.list_messages(ctx.project_id, 10)[-1]
    assert last["kind"] == "warning" and last["text"] == orchestrator.NO_API_KEY


def test_the_chat_has_no_claude_code_or_subscription_path():
    assert not hasattr(orchestrator, "default_model")
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in orchestrator.MODEL_ENV
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in orchestrator.review_env({"CLAUDE_CODE_OAUTH_TOKEN": "x"})


# --- multi-provider model selection (ADR 0009's `orchestrator` role) -------------------------


def fake_litellm(monkeypatch, content, usd=0.004, prompt_tokens=10, completion_tokens=2):
    """Replaces `litellm` with a fake that records every `completion()` call."""
    calls = []
    module = types.ModuleType("litellm")
    module.calls = calls

    def completion(**kw):
        calls.append(kw)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
        )

    module.completion = completion
    module.completion_cost = lambda completion_response=None: usd
    monkeypatch.setitem(sys.modules, "litellm", module)
    return module


def test_litellm_model_calls_with_the_projects_model_and_key_never_the_environment(monkeypatch):
    monkeypatch.delenv("TRADVIA_OFFLINE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-wide-key-should-not-be-used")
    litellm = fake_litellm(monkeypatch, json.dumps({"reply": "via anthropic"}))

    model = orchestrator.litellm_model("anthropic/claude-sonnet-5-5", "project-own-secret")
    data = model("prompt text", 30)

    (call,) = litellm.calls
    assert call["model"] == "anthropic/claude-sonnet-5-5" and call["api_key"] == "project-own-secret"
    assert call["messages"] == [{"role": "user", "content": "prompt text"}]
    assert data["result"] == json.dumps({"reply": "via anthropic"})
    assert data["total_cost_usd"] == 0.004
    assert data["usage"] == {"input_tokens": 10, "output_tokens": 2}


def test_litellm_model_redacts_its_own_key_from_a_provider_error(monkeypatch):
    monkeypatch.delenv("TRADVIA_OFFLINE", raising=False)
    module = types.ModuleType("litellm")

    def completion(**kw):
        raise RuntimeError(f"invalid api key: {kw['api_key']}")

    module.completion = completion
    monkeypatch.setitem(sys.modules, "litellm", module)

    model = orchestrator.litellm_model("anthropic/claude-sonnet-5-5", "sk-ant-project-secret")
    with pytest.raises(RuntimeError) as excinfo:
        model("hi", 30)
    assert "sk-ant-project-secret" not in str(excinfo.value)


def test_litellm_model_is_off_in_offline_mode(monkeypatch):
    model = orchestrator.litellm_model("anthropic/claude-sonnet-5-5", "sk-test")
    with pytest.raises(RuntimeError, match="offline"):
        model("hi", 30)


def test_model_for_connection_uses_litellm_for_a_configured_ai_provider(monkeypatch):
    monkeypatch.delenv("TRADVIA_OFFLINE", raising=False)
    litellm = fake_litellm(monkeypatch, json.dumps({"reply": "ok"}))
    model = orchestrator.model_for_connection("anthropic", "claude-sonnet-5-5", "project-secret")
    model("hi", 30)
    assert litellm.calls[0]["model"] == "anthropic/claude-sonnet-5-5"


@pytest.mark.parametrize("provider, model_name, secret", [
    (None, None, None),
    ("claude-code", None, None),
    ("claude-code", None, "oauth-token"),  # a subscription login never answers the chat
    ("anthropic", "claude-sonnet-5-5", None),  # chosen, but no API key saved yet
    ("anthropic", None, "key"),
    ("gemini-cli", "gemini-2.5-pro", "key"),  # a coding-agent CLI, not a LiteLLM provider
    ("codex-cli", "gpt-5", "key"),
])
def test_model_for_connection_is_none_without_an_ai_provider_and_its_key(provider, model_name, secret):
    assert orchestrator.model_for_connection(provider, model_name, secret) is None


# --- review_pr ------------------------------------------------------------------------------


def test_review_pr_runs_make_check_on_the_branch_and_comments(store, gh, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", SERVER_TOKEN)
    runner = FakeRunner(code=2, output="FAILED test_x")
    ctx = make_ctx(store, gh, runner=runner, cwd="/runner/repo")
    messages = send(ctx, "review 12", ScriptedModel([tool("review_pr", number=12), {"reply": "failed"}]))
    cmds = runner.commands()
    assert cmds[0] == ["git", "fetch", "origin", "pull/12/head"] and cmds[1][:3] == ["git", "worktree", "add"]
    assert ["make", "check"] in cmds and cmds[-1][:3] == ["git", "worktree", "remove"]
    (_, kw), = [c for c in runner.calls if c[0] == ["make", "check"]]
    assert kw["cwd"] != "/runner/repo" and kw["env"]["TRADVIA_OFFLINE"] == "1"
    assert "GITHUB_TOKEN" not in kw["env"] and "CLAUDE_CODE_OAUTH_TOKEN" not in kw["env"]
    (_, token, _, number, body), = gh.writes("comment")
    assert token == TOKEN and number == 12 and "failed" in body and "FAILED test_x" in body
    assert not gh.writes("merge_pull")
    assert {"type": "pr", "number": 12} in messages[-1]["refs"]


def test_review_pr_never_runs_a_forks_code_or_a_closed_pr(store, gh):
    runner = FakeRunner()
    ctx = make_ctx(store, gh, runner=runner, cwd="/runner/repo")
    gh.pull["head"]["repo"] = {"full_name": "fork/tradvia"}
    assert "fork" in orchestrator.call_tool(ctx, "review_pr", {"number": 12})["error"]
    gh.pull["head"]["repo"] = {"full_name": A}
    gh.pull["state"] = "closed"
    assert "not open" in orchestrator.call_tool(ctx, "review_pr", {"number": 12})["error"]
    assert not runner.calls and not gh.writes("comment")
    no_checkout = make_ctx(store, gh, runner=runner)
    gh.pull["state"] = "open"
    assert "runner checkout" in orchestrator.call_tool(no_checkout, "review_pr", {"number": 12})["error"]


def test_review_pr_times_out_as_a_failed_check(store, gh):
    def runner(cmd, **kw):
        if cmd[:2] == ["make", "check"]:
            raise subprocess.TimeoutExpired(cmd, 1)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ctx = make_ctx(store, gh, runner=runner, cwd="/runner/repo")
    out = orchestrator.call_tool(ctx, "review_pr", {"number": 12})
    assert out["result"]["passed"] is False and "timeout" in gh.writes("comment")[0][4]


# --- the store ------------------------------------------------------------------------------


def test_messages_are_listed_oldest_first_and_per_project(store):
    a, b = store.ensure_project(A), store.ensure_project(B)
    for i in range(5):
        store.append_message(a, {"author": "ana", "text": f"m{i}"})
    store.append_message(b, {"author": "bia", "text": "other"})
    assert [m["text"] for m in store.list_messages(a, 3)] == ["m2", "m3", "m4"]
    assert [m["text"] for m in store.list_messages(b, 3)] == ["other"]
    assert all("project_id" not in m for m in store.list_messages(a, 3))


# --- API ------------------------------------------------------------------------------------


def chat_client(store, gh, clock, model=None, runner=None, spawn=lambda job: job()):
    app = create_app(store=store, identity=gh, clock=clock, chat_model=model,
                     chat_runner=runner, spawn=spawn)
    c = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    login(c)
    return c


def test_post_stores_the_message_runs_the_orchestrator_and_the_history_shows_both(store, gh, clock):
    model = ScriptedModel([tool("read_backlog"), {"reply": "Empty backlog."}])
    c = chat_client(store, gh, clock, model)
    r = post(c, CHAT, {"text": "show the backlog", "refs": [{"type": "card", "number": 7}]})
    assert r.status_code == 202 and r.json()["ok"] is True and r.headers["cache-control"] == "no-store"
    h = c.get(CHAT)
    assert h.status_code == 200
    body = h.json()
    assert [(m["author"], m["kind"]) for m in body["messages"]] == [("ana", "message"), ("orchestrator", "reply")]
    assert body["messages"][0]["refs"] == [{"type": "card", "number": 7}]
    assert body["working"] is False and body["can_chat"] is True
    assert body["cost"]["runs"] == 1 and body["cost"]["usd"] == pytest.approx(0.02)
    audit = [r for r in store.audit if r["action"] == "chat"]
    assert audit and audit[0]["actor_login"] == "ana" and audit[0]["result"] == "ok"
    assert audit_of(store)[0]["detail"].startswith("for @ana")
    assert set(model.prompts) and all(TOKEN not in p for p in model.prompts)


def test_the_working_flag_is_up_until_the_run_ends(store, gh, clock):
    jobs = []
    c = chat_client(store, gh, clock, ScriptedModel([{"reply": "hi"}]), spawn=jobs.append)
    assert post(c, CHAT, {"text": "one"}).status_code == 202
    assert c.get(CHAT).json()["working"] is True
    again = post(c, CHAT, {"text": "two"})
    assert again.status_code == 409 and "still working" in again.json()["error"]
    assert [m["text"] for m in c.get(CHAT).json()["messages"]] == ["one"]
    jobs[0]()
    body = c.get(CHAT).json()
    assert body["working"] is False and body["messages"][-1]["text"] == "hi"


def test_a_crash_in_the_run_leaves_a_warning_and_clears_the_flag(store, gh, clock, monkeypatch):
    def boom(ctx, model=None, budget=None):
        raise ValueError("secret detail")

    monkeypatch.setattr(orchestrator, "run", boom)
    c = chat_client(store, gh, clock, ScriptedModel([]))
    assert post(c, CHAT, {"text": "hi"}).status_code == 202
    body = c.get(CHAT).json()
    assert body["working"] is False and body["messages"][-1]["kind"] == "warning"
    assert "secret detail" not in json.dumps(body)


def test_chat_needs_a_session_and_csrf(client, store, gh, clock):
    assert client.get(CHAT).status_code == 401
    anonymous = client.post(CHAT, json={"text": "hi"}, headers={"Origin": ORIGIN})
    assert anonymous.status_code == 401
    login(client)
    r = post(client, CHAT, {"text": "hi"}, csrf=False)
    assert r.status_code == 403 and store.list_messages(store.ensure_project(A), 10) == []


def test_chat_needs_the_start_card_role(store, gh, clock):
    c = as_role(store, gh, clock, "read")
    r = post(c, CHAT, {"text": "hi"})
    assert r.status_code == 403 and "'start_card' needs at least 'triage'" in r.json()["error"]
    assert store.list_messages(store.ensure_project(A), 10) == []
    denied = [x for x in store.audit if x["action"] == "chat"]
    assert denied[0]["result"] == "denied"
    body = c.get(CHAT).json()  # reading is allowed, writing is not
    assert body["can_chat"] is False and body["messages"] == []


def test_chat_validates_the_message(store, gh, clock):
    c = chat_client(store, gh, clock, ScriptedModel([]))
    for body in ({}, {"text": "   "}, {"text": "x" * 2001}, {"text": "hi", "refs": "no"},
                 {"text": "hi", "refs": [{"type": "issue", "number": 1}]}):
        assert post(c, CHAT, body).status_code == 400
    assert c.get(CHAT).json()["messages"] == []


def test_chat_is_idempotent_for_a_repeated_key(store, gh, clock):
    c = chat_client(store, gh, clock, ScriptedModel([{"reply": "once"}]))
    k = key()
    assert post(c, CHAT, {"text": "hi"}, idem=k).status_code == 202
    again = post(c, CHAT, {"text": "hi"}, idem=k)
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert len([m for m in c.get(CHAT).json()["messages"] if m["author"] == "ana"]) == 1


def test_chat_history_is_per_project(store, gh, clock):
    c = chat_client(store, gh, clock, ScriptedModel([{"reply": "A only"}]))
    assert post(c, CHAT, {"text": "for A"}).status_code == 202
    other = c.get(f"/api/projects/{B}/chat")
    assert other.status_code == 200 and other.json()["messages"] == []
    assert other.json()["can_chat"] is False
    assert c.get("/api/projects/Nope/nope/chat").status_code == 404
    assert post(c, "/api/projects/Nope/nope/chat", {"text": "x"}).status_code == 404


def test_production_chat_uses_the_projects_own_orchestrator_connection_not_only_claude_code(
    store, gh, clock, monkeypatch,
):
    """No `chat_model` is injected here (unlike `chat_client` above): this is the real
    production path (api.py's `chat_model_for`), proving the orchestrator is not tied to
    one API/CLI when the project has configured a different provider (ADR 0009)."""
    monkeypatch.delenv("TRADVIA_OFFLINE", raising=False)
    gh.perm[A] = ("admin", "admin")
    litellm = fake_litellm(monkeypatch, json.dumps({"reply": "via anthropic"}))

    def cli_runner(cmd, **kw):
        raise AssertionError("should not shell out to Claude Code when anthropic is configured")

    app = create_app(store=store, identity=gh, clock=clock, chat_runner=cli_runner, spawn=lambda job: job())
    c = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    login(c)
    r = c.put(f"{BASE}/connections/roles/orchestrator",
             json={"provider": "anthropic", "model": "claude-sonnet-5-5"}, headers=csrf_headers(c))
    assert r.status_code == 200
    r = c.put(f"{BASE}/connections/secrets/anthropic",
             json={"value": "sk-ant-project-own-secret"}, headers=csrf_headers(c))
    assert r.status_code == 200

    assert post(c, CHAT, {"text": "status?"}).status_code == 202

    (call,) = litellm.calls
    assert call["model"] == "anthropic/claude-sonnet-5-5" and call["api_key"] == "sk-ant-project-own-secret"
    assert c.get(CHAT).json()["messages"][-1]["text"] == "via anthropic"


def test_production_chat_is_off_without_an_api_key_and_says_why(store, gh, clock, monkeypatch):
    """Same production path, no AI provider with a key: the chat does not run at all (ADR 0013).
    No fallback to Claude Code, even with its subscription token in the environment."""
    monkeypatch.delenv("TRADVIA_OFFLINE", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "owner-subscription")
    gh.perm[A] = ("admin", "admin")
    litellm = fake_litellm(monkeypatch, json.dumps({"reply": "never"}))

    def cli_runner(cmd, **kw):
        raise AssertionError("the chat must never shell out to Claude Code")

    app = create_app(store=store, identity=gh, clock=clock, chat_runner=cli_runner, spawn=lambda job: job())
    c = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    login(c)
    body = c.get(CHAT).json()
    assert body["can_chat"] is False and body["chat_off_reason"] == "no_api_key"

    r = post(c, CHAT, {"text": "status?"})
    assert r.status_code == 409 and r.json()["error"] == orchestrator.NO_API_KEY
    assert store.list_messages(store.ensure_project(A), 10) == [] and not litellm.calls

    # a model chosen but no key saved yet: still off
    r = c.put(f"{BASE}/connections/roles/orchestrator",
              json={"provider": "anthropic", "model": "claude-sonnet-5-5"}, headers=csrf_headers(c))
    assert r.status_code == 200
    assert c.get(CHAT).json()["can_chat"] is False
    assert post(c, CHAT, {"text": "status?"}).status_code == 409

    # with the key the chat is on, and the reason is gone
    r = c.put(f"{BASE}/connections/secrets/anthropic",
              json={"value": "sk-ant-project-own-secret"}, headers=csrf_headers(c))
    assert r.status_code == 200
    body = c.get(CHAT).json()
    assert body["can_chat"] is True and body["chat_off_reason"] is None


def test_a_reader_gets_no_chat_off_reason(store, gh, clock):
    c = as_role(store, gh, clock, "read")
    body = c.get(CHAT).json()
    assert body["can_chat"] is False and body["chat_off_reason"] is None
