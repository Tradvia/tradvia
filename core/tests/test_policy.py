# SPDX-License-Identifier: FSL-1.1-ALv2
import base64

import pytest

from tradvia import cli
from tradvia.integrations import GitHubREST
from tradvia.policy import Interactions, Policy, PolicyError, load_policy, parse_policy, role_of

EXAMPLE = """
access:
  view: inherit
interactions:
  start_card: write
  approve_plan: maintain
  mention_assistant: write
  live_takeover: admin
  budget_control: admin
"""


def test_defaults_without_file():
    assert parse_policy(None) == Policy() and parse_policy("") == Policy()
    i = Policy().interactions
    assert (i.start_card, i.approve_plan, i.mention_assistant, i.live_takeover, i.budget_control) == (
        "triage", "write", "write", "maintain", "admin")


def test_valid_yaml_and_partial_override():
    p = parse_policy(EXAMPLE)
    assert p.interactions == Interactions(start_card="write", approve_plan="maintain",
                                          live_takeover="admin")
    assert parse_policy("interactions:\n  approve_plan: admin\n").interactions.start_card == "triage"
    assert parse_policy("access:\n  view: write\n").view == "write"


@pytest.mark.parametrize("text", [
    "foo: 1",
    "access:\n  who: x\n",
    "interactions:\n  delete_repo: admin\n",
    "interactions:\n  start_card: owner\n",
    "interactions:\n  start_card: 1\n",
    "interactions:\n  start_card: Write\n",
    "interactions:\n  start_card:\n",            # null
    "interactions:\n  start_card: none\n",
    "interactions:\n  approve_plan: anonymous\n",
    "interactions:\n  approve_plan: public\n",
    "access:\n  view: everyone\n",
    "access:\n  view:\n",
    "- a\n- b\n",
    "interactions: [a]\n",
    "a: [unclosed",
])
def test_invalid_policy_fails_closed(text):
    with pytest.raises(PolicyError):
        parse_policy(text)


def test_allows_needs_a_role_at_least_the_minimum():
    p = Policy()
    assert not p.allows("approve_plan", None)
    assert not p.allows("approve_plan", "triage")
    assert p.allows("approve_plan", "write") and p.allows("approve_plan", "admin")
    assert p.allows("start_card", "triage") and not p.allows("start_card", "read")


class FakeGH:
    def __init__(self, file=None, roles=None, base="main"):
        self.file, self.roles, self.base = file, roles or {}, base
        self.refs, self.comments, self.repo = [], [], "o/r"

    def get_file(self, path, ref):
        self.refs.append((path, ref))
        return self.file

    def get_collaborator_role(self, login):
        return self.roles.get(login)

    def comment(self, number, body):
        self.comments.append((number, body))


def test_load_policy_reads_default_branch():
    gh = FakeGH(file=EXAMPLE, base="trunk")
    assert load_policy(gh).interactions.approve_plan == "maintain"
    assert gh.refs == [(".tradvia.yml", "trunk")]
    assert load_policy(FakeGH()) == Policy()


def test_role_of_ignores_bots_and_non_collaborators():
    gh = FakeGH(roles={"ci[bot]": "admin", "ana": "write"})
    assert role_of(gh, "ci[bot]") is None
    assert role_of(gh, "") is None and role_of(gh, None) is None
    assert role_of(gh, "stranger") is None
    assert role_of(gh, "ana") == "write"


class Resp:
    def __init__(self, status_code, data=None):
        self.status_code, self._data, self.text = status_code, data or {}, ""

    def json(self):
        return self._data


def test_github_rest_get_file_and_role():
    gh = GitHubREST("t", "o/r", "main")
    calls = []

    def get(url, params=None, timeout=None):
        calls.append((url, params))
        if "/contents/" in url:
            if params["ref"] == "main":
                return Resp(200, {"content": base64.b64encode(b"a: 1").decode()})
            return Resp(404)
        return Resp(200, {"role_name": "triage"}) if url.endswith("/ana/permission") else Resp(404)

    gh.s.get = get
    assert gh.get_file(".tradvia.yml", "main") == "a: 1"
    assert gh.get_file(".tradvia.yml", "other") is None
    assert gh.get_collaborator_role("ana") == "triage"
    assert gh.get_collaborator_role("nobody") is None
    gh.s.get = lambda *a, **k: Resp(500)
    with pytest.raises(RuntimeError):
        gh.get_file(".tradvia.yml", "main")


@pytest.fixture
def run_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("TRADVIA_REPO", "o/r")
    monkeypatch.setenv("TRADVIA_OFFLINE", "1")
    monkeypatch.delenv("TRADVIA_ACTOR", raising=False)

    def install(gh):
        built = []
        monkeypatch.setattr(cli, "GitHubREST", lambda *a, **k: gh)
        monkeypatch.setattr(cli, "make_chat", lambda *a, **k: object())
        monkeypatch.setattr(cli, "build_graph", lambda *a, **k: built.append(1) or pytest.fail("graph ran"))
        return built

    return install


def test_start_card_denied_comments_and_does_not_plan(run_env, monkeypatch):
    gh = FakeGH(roles={"ana": "read"})
    run_env(gh)
    monkeypatch.setenv("TRADVIA_ACTOR", "ana")
    card = cli.run(5, 0, "github")
    assert card["status"] == "BACKLOG"
    assert len(gh.comments) == 1 and "triage" in gh.comments[0][1] and gh.comments[0][0] == 5


def test_start_card_allowed_runs_graph(run_env, monkeypatch):
    gh = FakeGH(roles={"ana": "triage"})
    run_env(gh)
    monkeypatch.setenv("TRADVIA_ACTOR", "ana")
    ran = []

    class App:
        def invoke(self, state, config):
            ran.append(state["id"])
            return {"id": state["id"], "status": "WAITING_HUMAN"}

    monkeypatch.setattr(cli, "build_graph", lambda *a, **k: App())
    assert cli.run(5, 0, "github")["status"] == "WAITING_HUMAN"
    assert ran == ["CARD-5"] and gh.comments == []


def test_invalid_policy_leaves_card_unstarted_with_reason(run_env):
    gh = FakeGH(file="interactions:\n  start_card: nobody\n")
    run_env(gh)
    card = cli.run(5, 0, "github")
    assert card["status"] == "BACKLOG"
    assert "Invalid .tradvia.yml on main" in gh.comments[0][1] and "nobody" in gh.comments[0][1]


def test_pull_and_deploy_interactions_default_to_maintain_and_can_be_changed():
    p = Policy()
    assert p.allows("merge_pr", "maintain") and not p.allows("merge_pr", "write")
    assert p.allows("update_branch", "admin") and not p.allows("update_branch", "triage")
    assert p.allows("approve_deploy", "maintain") and not p.allows("approve_deploy", None)
    custom = parse_policy("interactions:\n  merge_pr: write\n  approve_deploy: admin\n")
    assert custom.allows("merge_pr", "write") and not custom.allows("approve_deploy", "maintain")
