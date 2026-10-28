# SPDX-License-Identifier: FSL-1.1-ALv2
"""Guardrails (ADR 0015), offline: no API keys, no network."""

from langgraph.types import Command

from tradvia.guardrails import scan_files, scan_text
from tradvia.graph import build_graph
from test_hello_world import FakeChat, FakeGitHub, start

FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
FAKE_GH = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def rules(findings):
    return {f.rule for f in findings}


def test_destructive_command_is_detected():
    assert rules(scan_text("sudo rm -rf /")) == {"rm-rf-root"}
    assert "git-force-push" in rules(scan_text("git push --force origin main"))
    assert "sql-drop" in rules(scan_text("drop table users;"))
    assert scan_text("rm -rf /tmp/build") == []
    assert scan_text("git push --force-with-lease") == []


def test_credentials_are_detected_and_masked():
    for secret, rule in ((FAKE_AWS, "aws-access-key"), (FAKE_GH, "github-token")):
        (f,) = scan_text(f"key = {secret}")
        assert f.rule == rule and f.kind == "credential"
        assert secret not in f.excerpt and f.excerpt.startswith(secret[:4])


def test_cpf_checks_digits():
    assert rules(scan_text("CPF 529.982.247-25")) == {"cpf"}
    assert scan_text("CPF 123.456.789-00") == []  # invalid check digits
    assert scan_text("pedido 12345678901") == []


def test_clean_text_has_no_findings():
    assert scan_files({"a.py": "def add(a: int, b: int) -> int:\n    return a + b\n"}) == []


def test_allow_marker_and_known_test_values_are_suppressed_but_reported():
    line = f"x = '{FAKE_AWS}'  # tradvia: allow aws-access-key"
    assert scan_text(line) == []
    (f,) = scan_text(line, with_allowed=True)
    assert f.allowed and f.rule == "aws-access-key"
    assert scan_text("AKIAIOSFODNN7EXAMPLE") == []


def _run(plan_overrides):
    gh, chat = FakeGitHub(), FakeChat()

    def planner(title, body, number):
        return {"plan": ["Do it"], "question": "Ok?", "summary": "s",
                "file_path": "docs/x.md", "file_content": "hello", **plan_overrides}

    app = build_graph(gh, chat, planner=planner)
    _, config = start(app)
    return gh, chat, app.invoke(Command(resume={"text": "Aprovado", "by": "ana"}), config)


def test_destructive_plan_blocks_before_execution():
    gh, chat, card = _run({"plan": ["Limpar com rm -rf /"]})
    assert gh.branches == [] and gh.prs == []
    assert card["status"] == "WAITING_HUMAN"
    assert card["guardrail"]["state"] == "blocked"
    assert card["guardrail"]["phase"] == "pre_execution"
    assert card["guardrail"]["findings"][0]["rule"] == "rm-rf-root"
    assert "bloqueado por guardrail: rm-rf-root" in chat.messages[-1]
    assert card["history"][-1]["reason"].startswith("Guardrail: rm-rf-root")


def test_credential_in_file_content_blocks_pr():
    gh, chat, card = _run({"file_content": f"token_value = {FAKE_GH}\n"})
    assert gh.branches == [] and gh.prs == []
    assert card["status"] == "WAITING_HUMAN"
    assert card["guardrail"]["findings"][0]["rule"] == "github-token"
    assert FAKE_GH not in str(card["guardrail"]) + chat.messages[-1]
    assert "github-token" in chat.messages[-1]


def test_documented_false_positive_passes_with_rule_recorded():
    content = f"key = '{FAKE_AWS}'  # tradvia: allow aws-access-key\n"
    gh, chat, card = _run({"file_content": content})
    assert card["status"] == "IN_REVIEW_PR"
    assert gh.prs and gh.branches == ["tradvia/issue-1"]
    assert card["guardrail"]["state"] == "ok"
    assert card["guardrail"]["suppressed"] == ["aws-access-key"]
