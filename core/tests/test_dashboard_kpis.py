# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Runs the KPI filter and card cost functions (dashboard/index.html) in Node, when available."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parents[2] / "dashboard" / "index.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

NOW = "Date.parse('2026-03-31T12:00:00Z')"


def js(expr, **args):
    """Evaluates `expr` with the pure dashboard code loaded; `args` are available as JSON globals."""
    text = HTML.read_text(encoding="utf-8")
    code = text[text.index("const STATES"):text.index("// Up to `max` cards")]
    decls = "".join(f"const {k} = JSON.parse(process.argv[{i + 1}]);\n"
                    for i, k in enumerate(args))
    script = code + "\n" + decls + f"console.log(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", script, *[json.dumps(v) for v in args.values()]],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def issue(number, created, state="open", plans=None, title=None):
    i = {"number": number, "title": title or f"Card {number}", "state": state,
         "created_at": created, "closed_at": None}
    if plans is not None:
        i["tradvia_plan_cost"] = plans
    return i


def pr(number, created, branch, usd=None, seconds=285, tokens=None, merged=None, state="closed"):
    p = {"number": number, "state": state, "created_at": created, "merged_at": merged,
         "head": {"ref": branch}}
    if usd is not None:
        p["tradvia_cost"] = {"usd": usd, "seconds": seconds, **(tokens or {})}
    return p


def plan(created, usd, tin=0, tout=0, cache=0):
    return {"created_at": created, "usd": usd, "tokens_in": tin, "tokens_out": tout,
            "tokens_cache": cache}


def run(workflow, title, started):
    return {"workflowFile": workflow, "display_title": title, "run_started_at": started,
            "status": "completed", "conclusion": "success", "updated_at": started}


SAMPLE = {
    "issues": [
        issue(1, "2026-03-29T10:00:00Z", plans=[plan("2026-03-29T10:05:00Z", 0.2, 1000, 200, 5000)]),
        issue(2, "2026-03-10T10:00:00Z", state="closed",
              plans=[plan("2026-03-10T10:05:00Z", 0.1, 500, 100, 0),
                     plan("2026-03-10T11:00:00Z", 0.15, 600, 150, 100)]),
        issue(3, "2025-12-01T10:00:00Z", state="closed"),
    ],
    "prs": [
        pr(11, "2026-03-29T11:00:00Z", "claude/issue-1-x", usd=1.0, tokens={
            "tokens_in": 2000, "tokens_out": 300, "tokens_cache": 8000}),
        pr(12, "2026-03-10T12:00:00Z", "tradvia/issue-2", usd=0.5, merged="2026-03-11T12:00:00Z",
           tokens={"tokens_in": 1500, "tokens_out": 250, "tokens_cache": 0}),
        pr(13, "2025-12-01T12:00:00Z", "tradvia/issue-3", usd=0.3, merged="2025-12-02T12:00:00Z"),
    ],
    "runs": [
        run("claude.yml", "#1 fix", "2026-03-29T10:30:00Z"),
        run("agent.yml", "Card 2", "2026-03-10T10:01:00Z"),
        run("agent.yml", "Card 3", "2025-12-01T10:01:00Z"),
    ],
}
SAMPLE["agentRuns"] = [r for r in SAMPLE["runs"] if r["workflowFile"] == "agent.yml"]


def filtered(**filters):
    base = {"from": None, "to": None, "executor": "all", "card": None, "status": "all"}
    view = js(f"applyFilters(data, {{...base, ...f}}, {NOW})", data=SAMPLE, base=base, f=filters)
    return view


def numbers(items):
    return sorted(i["number"] for i in items)


def test_whole_dashboard_script_parses():
    script = ("const s = require('fs').readFileSync(process.argv[1], 'utf8');"
              "new Function(/<script>([\\s\\S]*)<\\/script>/.exec(s)[1]);")
    subprocess.run([NODE, "-e", script, str(HTML)], check=True, capture_output=True)


# --- token parser ---------------------------------------------------------------------------

@pytest.mark.parametrize("body, expected", [
    ("Tokens: 12 345 in · 3 210 out · 80 000 cache", {"in": 12345, "out": 3210, "cache": 80000}),
    ("Tokens: 12 345 in · 3 210 out · 80 000 cache", {"in": 12345, "out": 3210, "cache": 80000}),
    ("tokens: 12.345 in · 3,210 out · 80,000 cache", {"in": 12345, "out": 3210, "cache": 80000}),
    ("Tokens: 12345 in · 3210 out · 80000 cache", {"in": 12345, "out": 3210, "cache": 80000}),
    ("Tokens: 0 in · 0 out · 0 cache", {"in": 0, "out": 0, "cache": 0}),
    ("no tokens here", None),
])
def test_parse_tokens(body, expected):
    assert js("parseTokens(body)", body=body) == expected


def test_parse_tokens_ignores_non_strings():
    assert js("parseTokens(null)") is None


# --- filters in the URL ---------------------------------------------------------------------

def test_parse_filters_defaults_and_values():
    assert js("parseFilters('')") == {"from": None, "to": None, "executor": "all",
                                      "card": None, "status": "all"}
    got = js("parseFilters(s)", s="?repo=A/b&from=2026-01-01&to=2026-02-01&executor=claude"
                                  "&card=7&status=DONE")
    assert got == {"from": "2026-01-01", "to": "2026-02-01", "executor": "claude",
                   "card": 7, "status": "DONE"}
    assert js("parseFilters(s)", s="?from=30d")["from"] == "30d"


def test_parse_filters_rejects_garbage():
    got = js("parseFilters(s)", s="?from=yesterday&to=x&executor=other&card=-3&status=NOPE")
    assert got == {"from": None, "to": None, "executor": "all", "card": None, "status": "all"}


def test_url_keeps_repo_and_roundtrips_the_filter():
    f = {"from": "7d", "to": None, "executor": "agent", "card": 4, "status": "DONE"}
    search = js("filtersToSearch(f, base)", f=f, base="?repo=Tradvia/tradvia&from=90d&card=9")
    assert "repo=Tradvia%2Ftradvia" in search
    assert js("parseFilters(s)", s=search) == f
    assert search.count("from=") == 1 and "card=4" in search


def test_url_has_no_filter_params_by_default():
    base = {"from": None, "to": None, "executor": "all", "card": None, "status": "all"}
    assert js("filtersToSearch(f, '?repo=a/b&executor=claude')", f=base) == "?repo=a%2Fb"
    assert js("filtersToSearch(f, '')", f=base) == ""


# --- period, executor, card, status ---------------------------------------------------------

def test_no_filter_keeps_everything():
    view = filtered()
    assert numbers(view["issues"]) == [1, 2, 3] and numbers(view["prs"]) == [11, 12, 13]
    assert len(view["runs"]) == 3


def test_period_presets():
    assert numbers(filtered(**{"from": "7d"})["issues"]) == [1]
    assert numbers(filtered(**{"from": "30d"})["issues"]) == [1, 2]
    assert numbers(filtered(**{"from": "90d"})["issues"]) == [1, 2]
    assert numbers(filtered(**{"from": "7d"})["prs"]) == [11]
    assert len(filtered(**{"from": "7d"})["runs"]) == 1


def test_custom_period_includes_the_last_day():
    view = filtered(**{"from": "2026-03-10", "to": "2026-03-10"})
    assert numbers(view["issues"]) == [2] and numbers(view["prs"]) == [12]
    assert filtered(**{"from": "2026-04-01"})["issues"] == []


def test_executor_filter():
    claude, agent = filtered(executor="claude"), filtered(executor="agent")
    assert numbers(claude["prs"]) == [11] and numbers(claude["issues"]) == [1]
    assert [r["workflowFile"] for r in claude["runs"]] == ["claude.yml"]
    assert numbers(agent["prs"]) == [12, 13] and numbers(agent["issues"]) == [2, 3]
    assert {r["workflowFile"] for r in agent["runs"]} == {"agent.yml"}


def test_card_filter_applies_to_cards_prs_and_runs():
    view = filtered(card=2)
    assert numbers(view["issues"]) == [2] and numbers(view["prs"]) == [12]
    assert [r["display_title"] for r in view["runs"]] == ["Card 2"]
    claude = filtered(card=1)
    assert [r["display_title"] for r in claude["runs"]] == ["#1 fix"]


def test_status_filter():
    view = filtered(status="DONE")
    assert numbers(view["issues"]) == [2, 3] and numbers(view["prs"]) == [12, 13]


def test_filters_combine():
    view = filtered(**{"from": "30d", "executor": "agent", "status": "DONE"})
    assert numbers(view["issues"]) == [2] and numbers(view["prs"]) == [12]


# --- board state (deriveCardState) -----------------------------------------------------------


def _state(run_):
    card = issue(99, "2026-03-29T10:00:00Z", title="Card 99")
    return js("deriveCardState(card, [], [run_])", card=card, run_=run_)


def test_a_successful_completed_run_waits_for_a_human():
    # The planner posted a plan and is waiting for an approve/reject/adjust reply.
    assert _state(run("agent.yml", "Card 99", "2026-03-29T11:00:00Z")) == "WAITING_HUMAN"


def test_a_failed_completed_run_is_blocked_not_waiting_for_a_human():
    # Planning failed (e.g. hit the weekly usage limit): nothing was posted for a
    # human to act on, so this must not be shown as WAITING_HUMAN.
    failed = dict(run("agent.yml", "Card 99", "2026-03-29T11:00:00Z"), conclusion="failure")
    assert _state(failed) == "BLOCKED"


def test_a_cancelled_or_timed_out_run_is_also_blocked():
    for conclusion in ("timed_out", "cancelled"):
        run_ = dict(run("agent.yml", "Card 99", "2026-03-29T11:00:00Z"), conclusion=conclusion)
        assert _state(run_) == "BLOCKED"


def test_a_run_still_in_progress_is_agent_working_even_after_an_earlier_failure():
    in_progress = dict(
        run("agent.yml", "Card 99", "2026-03-29T12:00:00Z"), status="in_progress", conclusion=None
    )
    older_failure = dict(
        run("agent.yml", "Card 99", "2026-03-29T11:00:00Z"), conclusion="failure"
    )
    card = issue(99, "2026-03-29T10:00:00Z", title="Card 99")
    assert js(
        "deriveCardState(card, [], [a, b])", card=card, a=in_progress, b=older_failure
    ) == "AGENT_WORKING"


# --- cost per card --------------------------------------------------------------------------

def test_card_cost_sums_every_plan_plus_the_executor():
    i = SAMPLE["issues"][1]
    got = js("cardCosts(i, i.tradvia_plan_cost, prs)", i=i, prs=SAMPLE["prs"])
    assert got["usd"] == pytest.approx(0.1 + 0.15 + 0.5)
    assert (got["in"], got["out"], got["cache"]) == (500 + 600 + 1500, 100 + 150 + 250, 100)
    assert got["plans"] == 2


def test_card_cost_does_not_add_the_cost_so_far():
    # The store gives only the executor line when it is present, never "cost so far" on top.
    prs = [pr(11, "2026-03-29T11:00:00Z", "tradvia/issue-5", usd=1.0)]
    got = js("cardCosts(i, [p1, p2], prs)", i=issue(5, "2026-03-29T10:00:00Z"), prs=prs,
             p1=plan("2026-03-29T10:05:00Z", 0.2), p2=plan("2026-03-29T10:30:00Z", 0.3))
    assert got["usd"] == pytest.approx(1.5)


def test_cost_so_far_only_stands_in_when_there_are_no_plan_comments():
    so_far = [pr(11, "2026-03-29T11:00:00Z", "tradvia/issue-5", usd=1.2, seconds=None)]
    i = issue(5, "2026-03-29T10:00:00Z")
    assert js("cardCosts(i, [], prs)", i=i, prs=so_far)["usd"] == pytest.approx(1.2)
    got = js("cardCosts(i, [p], prs)", i=i, prs=so_far, p=plan("2026-03-29T10:05:00Z", 0.2))
    assert got["usd"] == pytest.approx(0.2)  # the plans already are inside the 1.2


def test_store_pr_with_cost_so_far_covers_old_cards_without_plan_costs():
    # PRs written before plan costs were recorded: the store keeps the executor line and the
    # "cost so far" (planning + execution). Without stored plans the latter is the card total.
    old = [pr(11, "2026-03-29T11:00:00Z", "tradvia/issue-5", usd=0.41,
              tokens={"so_far_usd": 0.52, "tokens_in": 9000})]
    i = issue(5, "2026-03-29T10:00:00Z")
    got = js("cardCosts(i, [], prs)", i=i, prs=old)
    assert got["usd"] == pytest.approx(0.52) and got["in"] == 9000
    with_plans = js("cardCosts(i, [p], prs)", i=i, prs=old, p=plan("2026-03-29T10:05:00Z", 0.1))
    assert with_plans["usd"] == pytest.approx(0.1 + 0.41)


def test_card_without_any_cost_is_null():
    assert js("cardCosts(i, [], [])", i=issue(5, "2026-03-29T10:00:00Z")) is None


def test_card_cost_reads_github_plan_comments():
    body = ("🟡 **CARD-5 — T**\nResumo\n1. passo\n\n❓ Posso?\n"
            "Estimated cost (API-equivalent): $0.2500\nTokens: 1 000 in · 200 out · 5 000 cache\n"
            "Responda **Aprovado**, **Rejeitado** ou escreva um ajuste.")
    comments = [{"user": {"login": "github-actions[bot]"}, "body": body,
                 "created_at": "2026-03-29T10:05:00Z"},
                {"user": {"login": "ana"}, "body": body}]  # a human quoting it does not count
    got = js("cardCosts(i, c, [])", i=issue(5, "2026-03-29T10:00:00Z"), c=comments)
    assert got["usd"] == pytest.approx(0.25)
    assert (got["in"], got["out"], got["cache"]) == (1000, 200, 5000)


# --- indicators, chart and the empty case ---------------------------------------------------

def test_kpis_over_the_sample():
    view = filtered()
    got = js("filteredCostKpis(v)", v=view)
    # card 1: 0.2 + 1.0, card 2: 0.1 + 0.15 + 0.5, card 3: PR 0.3 with no plan comment
    assert got["cards"] == 3 and got["withCost"] == 3
    assert got["totalUsd"] == pytest.approx(1.2 + 0.75 + 0.3)
    assert got["medianUsd"] == pytest.approx(0.75)
    assert got["tokens"]["in"] == 1000 + 2000 + 500 + 600 + 1500
    assert got["mergedPrs"] == 2
    assert got["perMergedPrUsd"] == pytest.approx((0.75 + 0.3) / 2)


def test_kpis_follow_the_filter():
    got = js("filteredCostKpis(v)", v=filtered(**{"from": "7d"}))
    assert got["cards"] == 1 and got["totalUsd"] == pytest.approx(1.2)
    assert got["perMergedPrUsd"] is None


def test_period_without_data_has_no_numbers():
    got = js("filteredCostKpis(v)", v=filtered(**{"from": "2026-04-01"}))
    assert got["cards"] == 0 and got["totalUsd"] is None and got["medianUsd"] is None
    assert got["tokens"] is None and got["medianTokens"] is None
    assert got["perMergedPrUsd"] is None


def test_daily_series_covers_the_period():
    f = {"from": "2026-03-28", "to": "2026-03-31", "executor": "all", "card": None, "status": "all"}
    view = js(f"applyFilters(data, f, {NOW})", data=SAMPLE, f=f)
    series = js(f"dailySeries(v, f, {NOW})", v=view, f=f)
    assert [s["day"] for s in series] == ["2026-03-28", "2026-03-29", "2026-03-30", "2026-03-31"]
    assert series[1]["usd"] == pytest.approx(1.2) and series[0]["usd"] == 0
    assert series[1]["tokens"] == 1000 + 200 + 5000 + 2000 + 300 + 8000


def test_bar_chart_model_scales_to_the_largest_day():
    bars = js("barChartModel(s, 'usd', 100, 50)",
              s=[{"day": "a", "usd": 1}, {"day": "b", "usd": 2}, {"day": "c", "usd": 0}])
    assert [b["h"] for b in bars] == [25, 50, 0]
    assert all(b["x"] >= 0 and b["x"] + b["w"] <= 100 for b in bars)
    assert js("barChartModel([], 'usd', 100, 50)") == []
