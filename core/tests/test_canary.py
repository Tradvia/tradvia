# SPDX-License-Identifier: FSL-1.1-ALv2
"""Canary of a new prompt or model on a share of the cards (#128)."""

import hashlib
import json

import pytest

from tradvia import canary, cli, prompts

CANDIDATE = "You are @Agent-Architect, v2. Issue #{number}: {title}\n\n{body}"


@pytest.fixture
def with_candidate(tmp_path, monkeypatch):
    """A copy of the prompts folder where plan has a candidate version 2."""
    reg = json.loads((prompts.HERE / "versions.json").read_text(encoding="utf-8"))
    for name in prompts.NAMES:
        (tmp_path / f"{name}.txt").write_text(prompts.text(name), encoding="utf-8")
    (tmp_path / "plan@2.txt").write_text(CANDIDATE, encoding="utf-8")
    reg["plan"]["candidate"] = {"version": 2,
                                "sha256": hashlib.sha256(CANDIDATE.encode()).hexdigest()}
    (tmp_path / "versions.json").write_text(json.dumps(reg), encoding="utf-8")
    monkeypatch.setattr(prompts, "HERE", tmp_path)
    prompts.load.cache_clear()
    prompts.reset_canary()
    yield tmp_path
    prompts.load.cache_clear()
    prompts.reset_canary()


def test_the_percent_is_respected_and_stable_per_card():
    for percent in (0, 10, 25, 50, 100):
        picked = sum(canary.chosen("Tradvia/tradvia", n, percent) for n in range(1, 10_001))
        assert abs(picked - percent * 100) <= 250, (percent, picked)  # within 2.5 points
    assert all(canary.chosen("o/r", n, 30) == canary.chosen("o/r", n, 30) for n in range(500))
    # a card in at 10% stays in at a higher percent: raising the share only adds cards
    assert all(canary.chosen("o/r", n, 40) for n in range(2000) if canary.chosen("o/r", n, 10))


def test_validation(with_candidate):
    assert canary.validate_setting({"percent": 20, "prompts": ["plan"]}) == {
        "percent": 20, "prompts": ["plan"], "model": None}
    assert canary.validate_setting({"percent": 5, "model": "openai/gpt-5"})["model"] == "openai/gpt-5"
    for bad in ({"percent": 101, "prompts": ["plan"]}, {"percent": "10"}, {"percent": 10},
                {"percent": 10, "prompts": ["exec"]}, {"percent": 10, "model": "a b"},
                {"percent": 10, "prompts": "plan"}, {"other": 1}, ["x"]):
        with pytest.raises(ValueError):
            canary.validate_setting(bad)
    assert canary.read_setting({"percent": 0, "prompts": ["plan"]}) is None  # reverted
    assert canary.read_setting({"percent": 10, "prompts": ["gone"]}) is None  # broken: no canary


def test_a_card_in_the_canary_uses_the_candidate_and_says_so(with_candidate, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("TRADVIA_PLANNER_MODEL", raising=False)
    setting = {"percent": 100, "prompts": ["plan"], "model": "openai/gpt-5"}
    assert cli.apply_canary("o/r", 7, setting) is True
    assert prompts.active_text("plan") == CANDIDATE
    assert prompts.active_ref("plan") == "plan@2 (canary, model openai/gpt-5)"
    assert prompts.active_ref("exec") == "exec@1 (canary, model openai/gpt-5)"  # no exec candidate
    import os
    assert os.environ["ANTHROPIC_MODEL"] == os.environ["TRADVIA_PLANNER_MODEL"] == "openai/gpt-5"


def test_reverting_sends_every_card_back_to_the_stable_version(with_candidate):
    on = {"percent": 100, "prompts": ["plan"], "model": None}
    assert all(cli.apply_canary("o/r", n, on) for n in range(1, 50))
    reverted = {**on, "percent": 0}
    for n in range(1, 50):
        assert cli.apply_canary("o/r", n, reverted) is False
        assert prompts.active_text("plan") == prompts.text("plan")
        assert prompts.active_ref("plan") == prompts.ref("plan")


def test_a_card_outside_the_canary_keeps_the_stable_prompt(with_candidate):
    setting = {"percent": 30, "prompts": ["plan"], "model": None}
    outside = next(n for n in range(1, 1000) if not canary.chosen("o/r", n, 30))
    assert cli.apply_canary("o/r", outside, setting) is False
    assert prompts.active_ref("plan") == prompts.ref("plan")


def test_the_candidate_text_is_hash_checked_too(with_candidate):
    assert prompts.drift() == []
    (with_candidate / "plan@2.txt").write_text(CANDIDATE + "!", encoding="utf-8")
    assert prompts.drift() == ["plan@2"]


def test_compare_puts_both_sides_next_to_each_other():
    def plan(ref, usd):
        return {"body": f"🟡 plan\nPrompt: {ref}\n", "cost_usd": usd}

    cards = [
        {"number": 1, "comments": [plan("plan@1", 0.10)]},
        {"number": 2, "comments": [plan("plan@1", 0.30)]},
        {"number": 3, "comments": [plan("plan@2 (canary)", 0.05)]},
        {"number": 4, "comments": [{"body": "no prompt line"}]},
        {"number": 5, "comments": [plan("plan@1", 0.2), plan("plan@2 (canary, model x)", 0.4)]},
    ]
    out = canary.compare(cards, [{"number": 2, "merged": True}, {"number": 3, "merged": True}])
    # card 5 counts as canary (its latest plan), but its first plan's cost stays on stable
    assert out["stable"] == {"cards": 2, "merged_prs": 1, "median_plan_usd": 0.2}
    assert out["canary"] == {"cards": 2, "merged_prs": 1, "median_plan_usd": 0.225}


# --- the settings API ----------------------------------------------------------------------------

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from test_auth import (  # noqa: E402,F401
    A, B, client, clock, csrf_headers, env, gh, login, make_client, store,
)

URL = f"/api/projects/{A}/settings/canary"


@pytest.fixture
def signed(client):
    login(client)  # ana: admin of A, read in B
    return client


def test_an_admin_starts_and_reverts_the_canary_and_both_are_audited(signed, store, with_candidate):
    body = signed.get(URL).json()
    assert body["percent"] == 0 and body["candidates"] == {"plan": "plan@2"} and body["can_edit"]
    r = signed.put(URL, json={"percent": 25, "prompts": ["plan"]}, headers=csrf_headers(signed))
    assert r.status_code == 200 and r.json()["percent"] == 25 and r.json()["updated_by"] == "ana"
    r = signed.post(URL + "/revert", headers=csrf_headers(signed))
    assert r.status_code == 200
    out = r.json()
    assert out["percent"] == 0 and out["prompts"] == ["plan"]  # the record stays
    actions = [x["action"] for x in store.audit if x["action"].endswith("canary")]
    assert actions == ["set_canary", "revert_canary"]
    assert set(out["compare"]) == {"stable", "canary"}


def test_only_an_admin_changes_it_and_input_is_validated(signed, store, with_candidate):
    r = signed.put(f"/api/projects/{B}/settings/canary", json={"percent": 10, "prompts": ["plan"]},
                   headers=csrf_headers(signed))
    assert r.status_code == 403  # ana is only 'read' in B
    assert signed.post(f"/api/projects/{B}/settings/canary/revert",
                       headers=csrf_headers(signed)).status_code == 403
    for bad in ({"percent": 10}, {"percent": 10, "prompts": ["exec"]}, {"percent": 150}):
        assert signed.put(URL, json=bad, headers=csrf_headers(signed)).status_code == 400
    assert signed.put(URL, json={"percent": 10, "prompts": ["plan"]}).status_code == 403  # no CSRF
    assert store.get_setting(store.ensure_project(A), "canary") is None


def test_the_settings_screen_has_the_canary_in_both_languages():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[2] / "dashboard" / "index.html").read_text(encoding="utf-8")
    for key in ("can.title", "can.percent", "can.revert", "can.revertConfirm", "can.sideCanary"):
        assert html.count(f'"{key}":') == 2, key  # pt and en
    assert '"/settings/canary/revert"' in html and '"/settings/canary"' in html
