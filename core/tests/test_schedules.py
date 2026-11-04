# SPDX-License-Identifier: FSL-1.1-ALv2
"""Recurring cards (#133): created on time, once per slot, never started; set on Tradvia."""

from datetime import datetime, timezone

import pytest

from tradvia import schedules as S


def ts(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


WEEKLY = {"id": "deps", "title": "Auditoria de dependências", "every": "week", "weekday": 0, "hour": 9}
MONTHLY = {"id": "cost", "title": "Relatório de custo", "every": "month", "day": 1, "hour": 12}
DAILY = {"id": "d", "title": "Diário", "every": "day", "hour": 6}


def clean(*items):
    return S.validate_setting({"items": list(items)})["items"]


def test_slots_in_utc():
    w, m, d = clean(WEEKLY, MONTHLY, DAILY)
    # 2026-11-04 is a Wednesday
    assert S.slot_id(S.latest_slot(w, ts("2026-11-04T10:00"))) == "2026-11-02T09:00Z"
    assert S.slot_id(S.next_slot(w, ts("2026-11-04T10:00"))) == "2026-11-09T09:00Z"
    assert S.slot_id(S.latest_slot(w, ts("2026-11-02T08:59"))) == "2026-10-26T09:00Z"
    assert S.slot_id(S.latest_slot(m, ts("2026-11-04T10:00"))) == "2026-11-01T12:00Z"
    assert S.slot_id(S.latest_slot(m, ts("2026-01-01T11:00"))) == "2025-12-01T12:00Z"
    assert S.slot_id(S.next_slot(m, ts("2026-12-05T00:00"))) == "2027-01-01T12:00Z"
    assert S.slot_id(S.latest_slot(d, ts("2026-11-04T05:00"))) == "2026-11-03T06:00Z"


@pytest.mark.parametrize("bad", [
    {"items": [{**WEEKLY, "id": "Bad Id"}]},
    {"items": [{**WEEKLY, "every": "hour"}]},
    {"items": [{**WEEKLY, "hour": 24}]},
    {"items": [{**MONTHLY, "day": 31}]},
    {"items": [{**WEEKLY, "title": " "}]},
    {"items": [WEEKLY, WEEKLY]},
    {"items": [{**WEEKLY, "cron": "* * * * *"}]},
    {"other": 1},
])
def test_validation(bad):
    with pytest.raises(ValueError):
        S.validate_setting(bad)


def test_a_new_schedule_waits_for_its_next_slot():
    (w,) = clean(WEEKLY)
    now = ts("2026-11-04T10:00")
    state = S.baseline([w], [], {}, now)
    assert S.due([w], state, now) == []
    assert S.due([w], state, ts("2026-11-09T09:00")) == [(w, "2026-11-09T09:00Z")]


def test_an_unchanged_schedule_keeps_its_state_and_a_changed_one_restarts():
    (w,) = clean(WEEKLY)
    state = {"deps": "2026-10-26T09:00Z"}
    assert S.baseline([w], [w], state, ts("2026-11-04T10:00")) == state
    changed = {**w, "hour": 10}
    assert S.baseline([changed], [w], state, ts("2026-11-04T10:00")) == {"deps": "2026-11-02T10:00Z"}


def test_the_card_text_says_it_was_not_started_and_carries_the_slot():
    (w,) = clean({**WEEKLY, "body": "Rodar pip-audit"})
    title, body = S.card_text(w, "2026-11-09T09:00Z")
    assert title == "Auditoria de dependências — 2026-11-09"
    assert "Rodar pip-audit" in body and "não foi iniciado" in body
    assert "<!-- tradvia:schedule id=deps slot=2026-11-09T09:00Z -->" in body


# --- through the API, with a fake clock ---------------------------------------------------------

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_auth import (  # noqa: E402,F401
    A, B, SERVER_TOKEN, client, clock, csrf_headers, env, gh, login, make_client, store,
)
from tradvia.api import create_app  # noqa: E402


def url(repo=A):
    return f"/api/projects/{repo}/settings/schedules"


@pytest.fixture
def signed(store, gh, clock, monkeypatch):
    # The app reads the server token when it is created: set it first.
    monkeypatch.setenv("GITHUB_TOKEN", SERVER_TOKEN)
    c = make_client(store, gh, clock)
    login(c)  # ana: admin of A, read in B
    return c


def put(c, body, repo=A, csrf=True):
    return c.put(url(repo), json=body, headers=csrf_headers(c) if csrf else {})


def app_at(store, gh, monkeypatch, now):
    monkeypatch.setenv("GITHUB_TOKEN", SERVER_TOKEN)
    return create_app(store=store, identity=gh, clock=lambda: now)


def save(store, items, now, before=()):
    pid = store.ensure_project(A)
    value = S.validate_setting({"items": items})
    store.set_setting(pid, S.STATE_KEY, S.baseline(value["items"], list(before), {}, now), "ana", now)
    store.set_setting(pid, S.SETTING_KEY, value, "ana", now)


def test_the_slot_creates_one_card_not_started_even_after_a_restart(store, gh, monkeypatch):
    save(store, [WEEKLY], ts("2026-11-04T10:00"))
    app = app_at(store, gh, monkeypatch, 0)
    assert app.state.schedule_tick(ts("2026-11-05T10:00")) == []  # not due yet
    first = app.state.schedule_tick(ts("2026-11-09T09:00"))
    assert len(first) == 1
    assert app.state.schedule_tick(ts("2026-11-09T09:30")) == []  # same slot, same process
    again = app_at(store, gh, monkeypatch, 0)  # a restart: the slot is in the store
    assert again.state.schedule_tick(ts("2026-11-09T10:00")) == []
    (call,) = gh.writes("create_issue")
    assert call[1] == SERVER_TOKEN and call[3] == "Auditoria de dependências — 2026-11-09"
    assert not gh.writes("add_label")  # created, never started
    assert len(again.state.schedule_tick(ts("2026-11-16T09:00"))) == 1  # next week: one more


def test_a_disabled_schedule_creates_nothing(store, gh, monkeypatch):
    save(store, [{**WEEKLY, "enabled": False}], ts("2026-11-04T10:00"))
    app = app_at(store, gh, monkeypatch, 0)
    assert app.state.schedule_tick(ts("2026-11-09T09:00")) == []


def test_a_github_refusal_gives_the_slot_back(store, gh, monkeypatch):
    save(store, [DAILY], ts("2026-11-04T07:00"))
    app = app_at(store, gh, monkeypatch, 0)
    real = gh.create_issue
    gh.create_issue = lambda *a: (_ for _ in ()).throw(RuntimeError("GitHub down"))
    assert app.state.schedule_tick(ts("2026-11-05T06:30")) == []
    gh.create_issue = real
    assert len(app.state.schedule_tick(ts("2026-11-05T06:40"))) == 1


def test_a_refusal_is_shown_on_settings_until_a_card_is_created(store, gh, monkeypatch):
    from tradvia.actions import GitHubError

    save(store, [DAILY], ts("2026-11-04T07:00"))
    app = app_at(store, gh, monkeypatch, ts("2026-11-05T06:30"))
    real = gh.create_issue

    def refuse(*a):
        raise GitHubError(403)

    gh.create_issue = refuse
    app.state.schedule_tick(ts("2026-11-05T06:30"))
    from starlette.testclient import TestClient
    from test_auth import ORIGIN
    tc = TestClient(app, base_url=ORIGIN, follow_redirects=False)  # this app has the token
    login(tc)
    (item,) = tc.get(url()).json()["items"]
    assert "403" in item["last_error"] and "Issues: Read and write" in item["last_error"]
    gh.create_issue = real
    app.state.schedule_tick(ts("2026-11-05T06:40"))
    (item,) = tc.get(url()).json()["items"]
    assert item["last_error"] is None


def test_without_a_server_token_nothing_is_created(store, gh, monkeypatch):
    save(store, [DAILY], ts("2026-11-04T07:00"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    app = create_app(store=store, identity=gh, clock=lambda: 0)
    assert app.state.schedule_tick(ts("2026-11-05T06:30")) == []
    assert not gh.writes("create_issue")


def test_an_admin_saves_schedules_and_it_is_audited(signed, store):
    r = put(signed, {"items": [WEEKLY, MONTHLY]})
    assert r.status_code == 200
    body = r.json()
    assert [i["id"] for i in body["items"]] == ["deps", "cost"]
    assert all(i["next_slot"] and i["last_slot"] for i in body["items"])
    assert body["server_token"] is True and body["can_edit"] is True
    rows = [x for x in store.audit if x["action"] == "set_schedules"]
    assert rows and rows[-1]["result"] == "ok" and "deps:week" in rows[-1]["detail"]


def test_only_an_admin_changes_them_and_input_is_validated(signed, store):
    assert signed.get(url(B)).json()["can_edit"] is False  # ana is only 'read' in B
    assert put(signed, {"items": [WEEKLY]}, repo=B).status_code == 403
    assert put(signed, {"items": [{**WEEKLY, "every": "hour"}]}).status_code == 400
    assert put(signed, {"items": [WEEKLY]}, csrf=False).status_code == 403
    assert store.get_setting(store.ensure_project(A), S.SETTING_KEY) is None
    assert store.get_setting(store.ensure_project(B), S.SETTING_KEY) is None


def test_the_settings_screen_has_the_schedules_in_both_languages():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[2] / "dashboard" / "index.html").read_text(encoding="utf-8")
    pt, en = html[html.index("  pt: {"):html.index("  en: {")], html[html.index("  en: {"):]
    for key in ("sch.title", "sch.noToken", "sch.save", "sch.note", "sch.weekly", "sch.monthly"):
        assert f'"{key}"' in pt and f'"{key}"' in en, key
    assert '"/settings/schedules"' in html
