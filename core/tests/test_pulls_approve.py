# SPDX-License-Identifier: FSL-1.1-ALv2
"""Approving the workflows a pull request waits for (issue #97), against the fake of test_pulls."""

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_auth import (  # noqa: E402,F401
    BASE,
    TOKEN,
    as_role,
    audit_rows,
    client,
    clock,
    env,
    login,
    make_client,
    post,
    store,
)
from test_pulls import GREEN, check, gh, merge, signed  # noqa: E402,F401
from tradvia.actions import GitHubError  # noqa: E402

HEAD = "abc123"  # the head commit of the fake pull request (test_pulls.PULL)


def waiting(run_id, sha=HEAD):
    return {"id": run_id, "head_sha": sha, "status": "completed", "conclusion": "action_required"}


def approve(c, body=None):
    return post(c, BASE + "/pulls/12/approve-workflows", body if body is not None else {"sha": HEAD})


def approved(gh):
    return [c[3] for c in gh.calls if c[0] == "approve_run"]


def test_pull_view_says_when_the_workflows_wait_for_approval(signed, gh):
    gh.checks = [check("core-tests", "action_required"), *GREEN[1:]]
    d = signed.get(BASE + "/pulls/12").json()
    assert d["workflows_waiting"] is True and d["head_sha"] == HEAD
    assert {c["name"]: c["state"] for c in d["checks"]} == {
        "core-tests": "awaiting", "license-boundary": "passed", "cla": "passed",
    }
    assert d["can"]["merge_pr"] is True


def test_pull_view_without_waiting_workflows(signed, gh):
    d = signed.get(BASE + "/pulls/12").json()
    assert d["workflows_waiting"] is False and d["head_sha"] == HEAD


def test_approves_only_the_runs_of_the_commit_the_person_saw(signed, gh, store):
    gh.runs = [waiting(11), waiting(12, sha="def456")]
    r = approve(signed)
    assert r.status_code == 200 and r.json()["runs"] == [11]
    assert approved(gh) == [11]
    assert {c[1] for c in gh.calls if c[0] == "approve_run"} == {TOKEN}
    (row,) = audit_rows(store, 12)
    assert row["actor_login"] == "ana" and row["action"] == "approve_workflows"
    assert row["result"] == "ok" and "runs=11" in row["detail"]


def test_refused_when_the_pull_request_has_new_commits(signed, gh):
    gh.runs = [waiting(11)]
    r = approve(signed, {"sha": "old000"})
    assert r.status_code == 409 and "new commits" in r.json()["error"]
    assert approved(gh) == []


def test_needs_the_commit_it_approves(signed, gh):
    gh.runs = [waiting(11)]
    assert approve(signed, {}).status_code == 400
    assert approve(signed, {"sha": ""}).status_code == 400
    assert approve(signed, {"sha": 7}).status_code == 400
    assert approved(gh) == []


def test_nothing_waiting_on_this_commit_is_a_conflict(signed, gh):
    gh.runs = [waiting(11, sha="def456")]
    r = approve(signed)
    assert r.status_code == 409 and "No workflow is waiting" in r.json()["error"]
    assert approved(gh) == []


def test_approving_needs_the_maintain_role(store, gh, clock):
    gh.runs = [waiting(11)]
    c = as_role(store, gh, clock, "write")
    r = approve(c)
    assert r.status_code == 403 and "'merge_pr' needs at least 'maintain'" in r.json()["error"]
    assert approved(gh) == []
    (row,) = audit_rows(store, 12)
    assert row["result"] == "denied"


def test_a_github_refusal_names_the_app_permission_it_lacks(signed, gh):
    gh.runs = [waiting(11)]
    gh.fail["approve_run"] = GitHubError(403, "Resource not accessible by integration")
    r = approve(signed)
    assert r.status_code == 403 and "Actions: Read and write" in r.json()["error"]


def test_merge_waits_until_the_workflows_are_approved(signed, gh, store):
    gh.checks = [check("core-tests", "action_required"), *GREEN[1:]]
    r = merge(signed)
    assert r.status_code == 409 and "approved" in r.json()["error"]
    assert "failed" not in r.json()["error"]
    assert not [c for c in gh.calls if c[0] == "merge_pull"]
    assert audit_rows(store, 12)[0]["result"] == "failed"


def test_a_cla_waiting_for_approval_is_ignored_only_when_asked(signed, gh, store):
    gh.checks = [*GREEN[:2], check("cla", "action_required")]
    assert merge(signed).status_code == 409
    assert merge(signed, {"ignore_cla": True}).status_code == 200
    detail = [row["detail"] for row in audit_rows(store, 12) if row["result"] == "ok"][0]
    assert "ignore_cla" in detail
