# SPDX-License-Identifier: FSL-1.1-ALv2
"""Review, merge and deploy from the panel (ADR 0007), against a fake GitHub that records tokens."""

import copy

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_auth import (  # noqa: E402,F401
    A,
    BASE,
    ORIGIN,
    SERVER_TOKEN,
    TOKEN,
    FakeGitHub,
    as_role,
    audit_rows,
    client,
    clock,
    csrf_headers,
    env,
    key,
    login,
    make_client,
    post,
    store,
)
from tradvia.actions import GitHubError  # noqa: E402

BODY = (
    "Closes #9\n\nOpened by **@Agent-Coder**.\n\nCard `c` · estimated cost so far "
    "(API-equivalent): $0.5000\n\n### Claude Code\nAdicionei a rota.\n\n"
    "Estimated cost (API-equivalent): $0.4200 · 31s"
)
PULL = {
    "number": 12, "title": "feat(core): add route", "state": "open", "merged": False,
    "body": BODY, "html_url": f"https://github.com/{A}/pull/12", "mergeable_state": "clean",
    "user": {"login": "github-actions[bot]"},
    "head": {"ref": "tradvia/issue-9", "sha": "abc123", "repo": {"full_name": A}},
    "base": {"ref": "main"},
    "files": [
        {"filename": "core/src/x.py", "status": "added", "additions": 5, "deletions": 1,
         "patch": "@@ -1 +1,5 @@\n+x"},
        {"filename": "logo.png", "status": "added", "additions": 0, "deletions": 0},
    ],
}
GREEN = [
    {"name": n, "status": "completed", "conclusion": "success"}
    for n in ("core-tests", "license-boundary", "cla")
]


def check(name, conclusion, status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion}


class PullGitHub(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.pull = copy.deepcopy(PULL)
        self.checks = copy.deepcopy(GREEN)
        self.review_comments = [
            {"user": {"login": "bia"}, "body": "ok", "state": "APPROVED", "path": None,
             "created_at": "2026-01-02T00:00:00Z"},
        ]
        self.runs = []
        self.pending = {}
        self.fail = {}  # method name -> GitHubError

    def _call(self, name, token, *args):
        self.calls.append((name, token, A, *args))
        if name in self.fail:
            raise self.fail[name]

    def get_pull(self, token, repo, number):
        self._call("get_pull", token, number)
        return copy.deepcopy(self.pull)

    def list_checks(self, token, repo, sha):
        self._call("list_checks", token, sha)
        return self.checks

    def list_review_comments(self, token, repo, number):
        self._call("list_review_comments", token, number)
        return self.review_comments

    def merge_pull(self, token, repo, number, title, sha):
        self._call("merge_pull", token, number, title, sha)

    def delete_branch(self, token, repo, branch):
        self._call("delete_branch", token, branch)

    def update_branch(self, token, repo, number):
        self._call("update_branch", token, number)

    def list_runs(self, token, repo, status):
        self._call("list_runs", token, status)
        return self.runs

    def list_pending_deployments(self, token, repo, run_id):
        self._call("list_pending_deployments", token, run_id)
        return self.pending.get(run_id, [])

    def review_pending_deployment(self, token, repo, run_id, environment_ids, approve, comment):
        self._call("review_pending_deployment", token, run_id, environment_ids, approve, comment)

    def cancel_run(self, token, repo, run_id):
        self._call("cancel_run", token, run_id)

    def dispatch_workflow(self, token, repo, workflow, ref, inputs):
        self._call("dispatch_workflow", token, workflow, ref, inputs)


@pytest.fixture
def gh():
    return PullGitHub()


@pytest.fixture
def signed(client, gh, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", SERVER_TOKEN)
    login(client)
    return client


def merge(c, body=None):
    return post(c, BASE + "/pulls/12/merge", body)


def test_pull_view_has_everything_and_uses_the_user_token(signed, gh):
    r = signed.get(BASE + "/pulls/12")
    assert r.status_code == 200
    d = r.json()
    assert d["title"] == "feat(core): add route" and d["state"] == "open"
    assert d["executor"] == {"summary": "Adicionei a rota.", "cost_usd": 0.42, "duration_s": 31.0}
    assert d["files"][0] == {
        "filename": "core/src/x.py", "status": "added", "additions": 5, "deletions": 1,
        "has_diff": True,
    }
    assert d["files"][1]["has_diff"] is False
    assert [c["name"] for c in d["checks"]] == ["core-tests", "license-boundary", "cla"]
    assert {c["state"] for c in d["checks"]} == {"passed"}
    assert d["review_comments"][0]["author"] == "bia"
    assert d["can"] == {"merge_pr": True, "update_branch": True}
    assert r.headers["cache-control"] == "no-store"
    tokens = {c[1] for c in gh.calls if c[0] != "get_permission"}
    assert tokens == {TOKEN}


def test_pull_diff_on_demand(signed, gh):
    r = signed.get(BASE + "/pulls/12/files/core/src/x.py/diff")
    assert r.status_code == 200 and r.json()["diff"].startswith("@@")
    assert signed.get(BASE + "/pulls/12/files/logo.png/diff").json()["diff"] is None
    assert signed.get(BASE + "/pulls/12/files/nope.py/diff").status_code == 404
    assert signed.get(BASE + "/pulls/12/files/../x/diff").status_code == 404


def test_pull_view_needs_a_session(client):
    assert client.get(BASE + "/pulls/12").status_code == 401
    assert client.get(BASE + "/deployments/pending").status_code == 401


def test_pull_not_found(signed, gh):
    gh.fail["get_pull"] = GitHubError(404)
    r = signed.get(BASE + "/pulls/12")
    assert r.status_code == 404 and "Pull request" in r.json()["error"]


def test_merge_squashes_with_the_pr_title_and_deletes_the_branch(signed, gh, store):
    r = merge(signed)
    assert r.status_code == 200 and r.json()["branch_deleted"] is True
    (m,) = gh.writes("merge_pull")
    assert m[1] == TOKEN and m[3:] == (12, "feat(core): add route (#12)", "abc123")
    assert gh.writes("delete_branch") == [("delete_branch", TOKEN, A, "tradvia/issue-9")]
    assert all(c[1] == TOKEN for c in gh.calls if c[0] != "get_permission")
    (row,) = audit_rows(store, 12)
    assert row["actor_login"] == "ana" and row["action"] == "merge_pr" and row["result"] == "ok"


def test_merge_does_not_delete_a_fork_branch(signed, gh):
    gh.pull["head"]["repo"] = {"full_name": "fork/tradvia"}
    assert merge(signed).json()["branch_deleted"] is False
    assert not gh.writes("delete_branch")


def test_merge_survives_a_failed_branch_delete(signed, gh):
    gh.fail["delete_branch"] = GitHubError(422)
    r = merge(signed)
    assert r.status_code == 200 and r.json()["branch_deleted"] is False


def test_merge_needs_the_maintain_role(store, gh, clock):
    c = as_role(store, gh, clock, "write")
    r = merge(c)
    assert r.status_code == 403 and "'merge_pr' needs at least 'maintain'" in r.json()["error"]
    assert not gh.writes("merge_pull") and not gh.writes("get_pull")
    (row,) = audit_rows(store, 12)
    assert row["result"] == "denied"


def test_merge_without_any_role(store, gh, clock):
    gh.perm[A] = (None, None)
    c = make_client(store, gh, clock)
    login(c)
    r = merge(c)
    assert r.status_code == 403 and "no access" in r.json()["error"]
    assert not gh.writes("merge_pull")


def test_merge_role_can_be_changed_in_the_policy(store, gh, clock):
    gh.policy = "interactions:\n  merge_pr: write\n"
    c = as_role(store, gh, clock, "write")
    assert merge(c).status_code == 200


def test_merge_refused_when_a_required_check_failed(signed, gh, store):
    gh.checks = [check("core-tests", "failure"), *GREEN[1:]]
    r = merge(signed)
    assert r.status_code == 409
    assert "core-tests" in r.json()["error"] and "Only the 'cla' check" in r.json()["error"]
    assert not gh.writes("merge_pull")
    assert audit_rows(store, 12)[0]["result"] == "failed"


def test_a_failed_cla_blocks_unless_ignored_and_the_audit_says_so(signed, gh, store):
    gh.checks = [*GREEN[:2], check("cla", "failure")]
    r = merge(signed)
    assert r.status_code == 409 and "cla" in r.json()["error"]
    assert not gh.writes("merge_pull")
    assert merge(signed, {"ignore_cla": True}).status_code == 200
    assert len(gh.writes("merge_pull")) == 1
    detail = [r["detail"] for r in audit_rows(store, 12) if r["result"] == "ok"][0]
    assert "ignore_cla" in detail


def test_ignore_cla_does_not_ignore_other_checks(signed, gh):
    gh.checks = [check("license-boundary", "failure"), check("cla", "failure"), GREEN[0]]
    r = merge(signed, {"ignore_cla": True})
    assert r.status_code == 409 and "license-boundary" in r.json()["error"]
    assert not gh.writes("merge_pull")


def test_ignore_cla_must_be_a_boolean(signed, gh):
    assert merge(signed, {"ignore_cla": "yes"}).status_code == 400


def test_merge_when_the_branch_is_behind(signed, gh):
    gh.pull["mergeable_state"] = "behind"
    r = merge(signed)
    assert r.status_code == 409 and "behind main" in r.json()["error"]
    assert not gh.writes("merge_pull")


def test_merge_needs_a_conventional_title_and_an_open_pr(signed, gh):
    gh.pull["title"] = "Add route"
    assert merge(signed).status_code == 400
    gh.pull.update(title="fix: x", state="closed")
    assert merge(signed).status_code == 409
    assert not gh.writes("merge_pull")


def test_merge_permission_errors_name_the_missing_permission(signed, gh):
    gh.fail["merge_pull"] = GitHubError(403, "Resource not accessible by integration")
    r = merge(signed)
    assert r.status_code == 403 and "Contents: Read and write" in r.json()["error"]
    gh.fail["merge_pull"] = GitHubError(405, "Pull Request is not mergeable")
    assert merge(signed).status_code == 409


def test_update_branch_is_allowed_and_audited(signed, gh, store):
    r = post(signed, BASE + "/pulls/12/update-branch")
    assert r.status_code == 200
    assert gh.writes("update_branch") == [("update_branch", TOKEN, A, 12)]
    assert audit_rows(store, 12)[0]["action"] == "update_branch"


def test_update_branch_needs_the_role_and_names_the_permission(store, gh, clock):
    c = as_role(store, gh, clock, "triage")
    assert post(c, BASE + "/pulls/12/update-branch").status_code == 403
    assert not gh.writes("update_branch")
    c = as_role(store, gh, clock, "maintain")
    gh.fail["update_branch"] = GitHubError(403, "Resource not accessible by integration")
    r = post(c, BASE + "/pulls/12/update-branch")
    assert "Pull requests: Read and write" in r.json()["error"]


def test_writes_need_the_csrf_header(signed, gh):
    for path in ("/pulls/12/merge", "/pulls/12/update-branch", "/deployments/5/approve",
                 "/deployments/5/reject", "/deployments/5/cancel", "/operations/post-deploy"):
        r = post(signed, BASE + path, csrf=False)
        assert r.status_code == 403, path
    assert not [c for c in gh.calls if c[0] not in ("get_permission", "get_file")]


class _Resp:
    def __init__(self, status, payload=None, content=b""):
        self.status_code, self._payload, self.content = status, payload, content

    def json(self):
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


def test_http_client_accepts_empty_success_and_keeps_the_github_reason(monkeypatch):
    from tradvia import auth

    answers = iter([
        _Resp(204),
        _Resp(403, {"message": "Resource not accessible by integration"}, b"x"),
        _Resp(500),
    ])
    monkeypatch.setattr(auth.requests, "request", lambda *a, **k: next(answers))
    http = auth.HttpGitHub("id", "secret")
    assert http.delete_branch("t", A, "feature/x") is None
    with pytest.raises(GitHubError) as err:
        http.cancel_run("t", A, 5)
    assert err.value.status == 403 and err.value.app_permission_missing
    assert "integration" not in str(err.value)
    with pytest.raises(GitHubError) as err:
        http.update_branch("t", A, 1)
    assert err.value.status == 500 and not err.value.app_permission_missing
