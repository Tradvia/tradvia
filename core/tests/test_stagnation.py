# SPDX-License-Identifier: FSL-1.1-ALv2
"""Stagnation monitor with synthetic call sequences: offline, no CLI."""

import pytest

from tradvia.stagnation import StagnationMonitor, normalize_call, reflective_message


def run(monitor, calls):
    return [monitor.record_call(tool, params) for tool, params in calls]


A = ("Bash", {"command": "make check"})
B = ("Read", {"file_path": "a.py"})


def test_three_identical_calls_warn_then_fourth_stops():
    assert run(StagnationMonitor(), [A, A, A]) == ["ok", "ok", "warn"]
    m = StagnationMonitor()
    assert run(m, [A, A, A, A]) == ["ok", "ok", "warn", "stop"]
    assert m.repeated_tool == "Bash" and m.repeat_count == 4


def test_equivalent_calls_count_as_the_same():
    calls = [("Bash", {"command": "make check", "timestamp": "10:00"}),
             ("bash", {"command": "  make   check "}),
             ("BASH", {"timestamp": 3, "command": "make\tcheck"})]
    assert run(StagnationMonitor(), calls)[-1] == "warn"
    assert normalize_call("Edit", {"a": 1, "b": 2}) == normalize_call("edit", {"b": 2, "a": 1})


def test_different_or_interrupted_calls_never_trigger():
    assert set(run(StagnationMonitor(), [A, B, A, B, A, B])) == {"ok"}
    assert set(run(StagnationMonitor(), [A, A, B, A, A])) == {"ok"}
    other = ("Bash", {"command": "make test"})
    assert set(run(StagnationMonitor(), [A, other, A])) == {"ok"}


@pytest.mark.parametrize("limit", [2, 5])
def test_custom_repeat_limit(limit):
    signals = run(StagnationMonitor(repeat_limit=limit), [A] * limit)
    assert signals == ["ok"] * (limit - 1) + ["warn"]


def test_monitors_do_not_share_state_across_projects():
    m1, m2 = StagnationMonitor(project_id=1), StagnationMonitor(project_id=2)
    run(m1, [A, A])
    assert m2.record_call(*A) == "ok" and m1.record_call(*A) == "warn"
    assert {c["project_id"] for c in m1.calls} == {1} and {c["project_id"] for c in m2.calls} == {2}


def test_output_is_summarized_and_hashed():
    m = StagnationMonitor(project_id=7)
    entry = m.record_output("x" * 1000)
    assert len(entry["summary"]) == 200 and len(entry["hash"]) == 64
    assert entry["project_id"] == 7 and m.outputs == [entry]


def test_reflective_message():
    assert reflective_message("Bash", 3) == (
        "You repeated Bash 3 times; change your approach or ask for help.")
