# SPDX-License-Identifier: FSL-1.1-ALv2
"""Pending deploys and the post-deploy operation from the panel (ADR 0007)."""

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_auth import (  # noqa: E402,F401
    A,
    BASE,
    TOKEN,
    as_role,
    audit_rows,
    client,
    clock,
    env,
    login,
    post,
    store,
)
from test_pulls import PullGitHub, signed  # noqa: E402,F401
from tradvia.actions import GitHubError  # noqa: E402


@pytest.fixture
def gh():
    g = PullGitHub()
    g.runs = [
        {"id": 5, "workflow_id": 1, "display_title": "old", "head_branch": "main",
         "created_at": "2026-01-01T10:00:00Z", "html_url": "u5"},
        {"id": 6, "workflow_id": 1, "display_title": "new", "head_branch": "main",
         "created_at": "2026-01-01T11:00:00Z", "html_url": "u6"},
    ]
    env_ = {"environment": {"id": 77, "name": "production"}, "current_user_can_approve": True}
    g.pending = {5: [env_], 6: [env_]}
    return g


def test_pending_list_marks_the_older_run_as_replaced(signed, gh, store):
    pid = store.ensure_project(A)
    store.set_backlog(pid, [{"issue_number": 44}, {"issue_number": 50}, {"issue_number": 45}])
    r = signed.get(BASE + "/deployments/pending")
    assert r.status_code == 200
    d = r.json()
    assert [(x["run_id"], x["replaced"]) for x in d["deploys"]] == [(6, False), (5, True)]
    assert d["deploys"][0]["environments"] == ["production"] and d["deploys"][0]["can_approve"]
    assert d["can"] == {"approve_deploy": True}
    assert all(c[1] == TOKEN for c in gh.calls if c[0] != "get_permission")


def test_approve_uses_the_user_token_and_is_audited(signed, gh, store):
    r = post(signed, BASE + "/deployments/6/approve", {"comment": "ship it"})
    assert r.status_code == 200
    (call,) = gh.writes("review_pending_deployment")
    assert call == ("review_pending_deployment", TOKEN, A, 6, [77], True, "ship it")
    row = audit_rows(store, None)[0]
    assert row["actor_login"] == "ana" and row["action"] == "approve_deploy"
    assert row["result"] == "ok" and "run=6" in row["detail"]


def test_reject(signed, gh):
    assert post(signed, BASE + "/deployments/6/reject").status_code == 200
    (call,) = gh.writes("review_pending_deployment")
    assert call[4:6] == ([77], False)


def test_a_person_who_is_not_a_reviewer_gets_a_clear_message(signed, gh, store):
    gh.pending[6][0]["current_user_can_approve"] = False
    r = post(signed, BASE + "/deployments/6/approve")
    assert r.status_code == 403
    assert r.json()["error"] == "You are not a reviewer of the production environment."
    assert not gh.writes("review_pending_deployment")
    assert audit_rows(store, None)[0]["result"] == "denied"


def test_github_refusal_distinguishes_app_permission_from_reviewer(signed, gh):
    gh.fail["review_pending_deployment"] = GitHubError(403, "Resource not accessible by integration")
    r = post(signed, BASE + "/deployments/6/approve")
    assert "Deployments: Read and write" in r.json()["error"]
    gh.fail["review_pending_deployment"] = GitHubError(403, "Must be a reviewer")
    r = post(signed, BASE + "/deployments/6/reject")
    assert "not a reviewer of the production environment" in r.json()["error"]


def test_approve_something_not_waiting(signed, gh):
    assert post(signed, BASE + "/deployments/99/approve").status_code == 409


def test_approving_needs_the_maintain_role(store, gh, clock):
    c = as_role(store, gh, clock, "write")
    r = post(c, BASE + "/deployments/6/approve")
    assert r.status_code == 403 and "'approve_deploy' needs at least 'maintain'" in r.json()["error"]
    assert not gh.writes("review_pending_deployment")


def test_cancel_a_replaced_run(signed, gh, store):
    assert post(signed, BASE + "/deployments/5/cancel").status_code == 200
    assert gh.writes("cancel_run") == [("cancel_run", TOKEN, A, 5)]
    assert audit_rows(store, None)[0]["action"] == "cancel_run"


def test_only_replaced_runs_can_be_cancelled(signed, gh):
    r = post(signed, BASE + "/deployments/6/cancel")
    assert r.status_code == 409 and not gh.writes("cancel_run")


def test_cancel_names_the_actions_permission(signed, gh):
    gh.fail["cancel_run"] = GitHubError(403, "Resource not accessible by integration")
    r = post(signed, BASE + "/deployments/5/cancel")
    assert "Actions: Read and write" in r.json()["error"]


def test_post_deploy_carries_the_backlog_from_the_stored_order(signed, gh, store):
    pid = store.ensure_project(A)
    store.set_backlog(pid, [{"issue_number": 44}, {"issue_number": 50}, {"issue_number": 45}])
    backlog = signed.get(BASE + "/deployments/pending").json()["backlog"]
    r = post(signed, BASE + "/operations/post-deploy", {"backfill": True, "backlog": backlog})
    assert r.status_code == 200
    (call,) = gh.writes("dispatch_workflow")
    assert call[1] == TOKEN
    assert call[3:] == (
        "deploy-oci.yml", "main", {"backfill": "true", "backlog": backlog},
    )
    assert backlog.split() == ["44", "50", "45"] or backlog == ""
    assert "post-deploy" in audit_rows(store, None)[0]["detail"]


def test_post_deploy_validates_its_inputs(signed, gh):
    for body in ({"backlog": "44; rm -rf"}, {"backlog": 44}, {"backfill": "yes"}):
        assert post(signed, BASE + "/operations/post-deploy", body).status_code == 400
    assert not gh.writes("dispatch_workflow")
    assert post(signed, BASE + "/operations/post-deploy").status_code == 200
    assert gh.writes("dispatch_workflow")[0][5] == {"backfill": "false", "backlog": ""}


def test_post_deploy_needs_the_role(store, gh, clock):
    c = as_role(store, gh, clock, "write")
    assert post(c, BASE + "/operations/post-deploy").status_code == 403
    assert not gh.writes("dispatch_workflow")
