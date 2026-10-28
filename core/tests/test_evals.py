# SPDX-License-Identifier: FSL-1.1-ALv2
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tradvia.evals import Expect, Task, load_tasks, parse_task, run_task
from tradvia.evals.__main__ import main
from tradvia.evals.compare import compare, render
from tradvia.evals.runner import LIVE_ENV, live_planner, run_suite, scripted_planner

REPO = Path(__file__).resolve().parents[2]


def _task(**expect):
    return Task("t", "title", "body", Expect(**expect))


def _plan(steps=("a", "b"), question="ok?", path="x.md", usd=0.01):
    return {"summary": "s", "plan": list(steps), "file_path": path, "question": question,
            "_cost": {"usd": usd, "tokens_in": 10, "tokens_out": 5, "tokens_cache": 1}}


def test_golden_tasks_load_and_are_valid():
    tasks = load_tasks()
    assert 5 <= len(tasks) <= 8
    assert len({t.id for t in tasks}) == len(tasks)


@pytest.mark.parametrize("data", [
    {"title": "t", "body": "b"},
    {"id": "x", "title": "t", "body": "b", "expect": {"min_steps": 5, "max_steps": 2}},
    {"id": "x", "title": "t", "body": "b", "expect": {"must_mention": "a"}},
    "nope",
])
def test_invalid_tasks_are_rejected(data):
    with pytest.raises(ValueError):
        parse_task(data)


def test_task_passes():
    res = run_task(_task(min_steps=1, max_steps=3, must_mention=("OK?",)), lambda t: _plan())
    assert res.passed and res.reasons == [] and res.steps == 2
    assert res.cost["usd"] == 0.01 and res.cost["tokens_in"] == 10 and res.cost["tokens_cache"] == 1


def test_task_fails_with_reasons():
    task = _task(max_steps=1, must_mention=("protected",), forbidden_paths=("LICENSE.md", ".github/"))
    res = run_task(task, lambda t: _plan(path="./LICENSE.md"))
    assert not res.passed
    assert len(res.reasons) == 3
    assert not run_task(task, lambda t: _plan(path=".github/ci.yml")).passed


def test_planner_error_is_a_failed_result():
    def boom(task):
        raise RuntimeError("down")

    res = run_task(_task(), boom)
    assert not res.passed and "down" in res.reasons[0]
    assert not run_task(_task(), lambda t: {"plan": [], "question": "q"}).passed


def test_report_has_steps_cost_and_tokens_per_task():
    report = run_suite(load_tasks(), scripted_planner("baseline"), "baseline")
    json.dumps(report)
    assert report["label"] == "baseline"
    for r in report["results"]:
        assert r["steps"] > 0
        assert {"usd", "tokens_in", "tokens_out", "tokens_cache"} <= r["cost"].keys()
    t = report["totals"]
    assert t["tasks"] == len(report["results"]) == t["passed"] + t["failed"]
    assert t["tokens_in"] == sum(r["cost"]["tokens_in"] for r in report["results"])


def test_compare_shows_task_by_task_difference():
    tasks = load_tasks()
    base = run_suite(tasks, scripted_planner("baseline"), "baseline")
    head = run_suite(tasks, scripted_planner("candidate"), "candidate")
    diff = compare(base, head)
    rows = {r["task_id"]: r for r in diff["rows"]}
    assert rows["ambiguous-request"]["status"] == "fixed"
    assert rows["protected-path"]["status"] == "fixed"
    assert rows["bugfix-off-by-one"]["steps"] == 1 and rows["bugfix-off-by-one"]["usd"] > 0
    assert rows["docs-typo"]["status"] == "same-ok" and rows["docs-typo"]["steps"] == 0
    assert not diff["regressed"]
    assert "ambiguous-request" in render(diff)


def test_compare_flags_regression_and_cli_exit_code(tmp_path, capsys):
    tasks = load_tasks()
    base = run_suite(tasks, scripted_planner("baseline"), "baseline")
    head = run_suite(tasks, scripted_planner("candidate"), "candidate")
    diff = compare(head, base)  # going back to baseline loses the fixes
    assert diff["regressed"] and diff["summary"]["regressions"] == 2
    paths = []
    for name, rep in (("head", head), ("base", base)):
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps(copy.deepcopy(rep)))
        paths.append(str(p))
    assert main(["compare", *paths]) == 1
    assert main(["compare", *reversed(paths)]) == 0
    assert "regressed" in capsys.readouterr().out


def test_compare_handles_new_and_removed_tasks():
    tasks = load_tasks()
    base = run_suite(tasks, scripted_planner("baseline"), "b")
    head = run_suite(tasks[1:], scripted_planner("baseline"), "h")
    statuses = {r["status"] for r in compare(base, head)["rows"]}
    assert "removed" in statuses and compare(base, head)["summary"]["removed"] == 1


def test_scripted_label_is_validated():
    for label in ("nope", "../baseline", ".hidden"):
        with pytest.raises(ValueError):
            scripted_planner(label)


def test_live_mode_refused_without_flag(monkeypatch):
    monkeypatch.delenv(LIVE_ENV, raising=False)
    with pytest.raises(RuntimeError, match=LIVE_ENV):
        live_planner()
    assert main(["run", "--label", "x", "--planner", "live"]) == 2
    monkeypatch.setenv(LIVE_ENV, "1")
    monkeypatch.setenv("TRADVIA_OFFLINE", "1")
    with pytest.raises(RuntimeError, match="OFFLINE"):
        live_planner()


def test_run_never_imports_litellm():
    code = ("import sys; from tradvia.evals.__main__ import main; "
            "main(['run', '--label', 'baseline']); sys.exit('litellm' in sys.modules)")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          cwd=REPO / "core", env={"PYTHONPATH": "src", "PATH": ""})
    assert proc.returncode == 0, proc.stderr


def test_make_evals_passes_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv(LIVE_ENV, raising=False)
    proc = subprocess.run(["make", "evals"], capture_output=True, text=True, cwd=REPO)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "compare baseline -> candidate" in proc.stdout
