# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Task-by-task comparison of two eval reports (ADR 0016)."""

from __future__ import annotations


def _status(base: dict | None, head: dict | None) -> str:
    if base is None:
        return "new"
    if head is None:
        return "removed"
    if base["passed"] == head["passed"]:
        return "same-ok" if head["passed"] else "same-fail"
    return "fixed" if head["passed"] else "regressed"


def compare(base: dict, head: dict) -> dict:
    """Align tasks by id. Rows carry the pass transition and the head-minus-base deltas."""
    b = {r["task_id"]: r for r in base["results"]}
    h = {r["task_id"]: r for r in head["results"]}
    rows = []
    for tid in sorted(b.keys() | h.keys()):
        rb, rh = b.get(tid), h.get(tid)
        row = {"task_id": tid, "status": _status(rb, rh),
               "reasons": (rh or {}).get("reasons", [])}
        if rb and rh:
            row["steps"] = rh["steps"] - rb["steps"]
            for key in ("usd", "tokens_in", "tokens_out"):
                row[key] = rh["cost"][key] - rb["cost"][key]
            row["usd"] = round(row["usd"], 6)
        rows.append(row)
    count = lambda s: sum(r["status"] == s for r in rows)  # noqa: E731
    return {"base": base["label"], "head": head["label"], "rows": rows,
            "summary": {"regressions": count("regressed"), "fixed": count("fixed"),
                        "new": count("new"), "removed": count("removed")},
            "regressed": count("regressed") > 0}


def _signed(value: float, fmt: str = "{:+d}") -> str:
    return fmt.format(value) if value else "="


def render(diff: dict) -> str:
    """Text table of a `compare` result."""
    head = f"{'task':<22} {'result':<10} {'steps':>6} {'usd':>10} {'tok in':>8} {'tok out':>8}"
    lines = [f"compare {diff['base']} -> {diff['head']}", head, "-" * len(head)]
    for r in diff["rows"]:
        if "steps" in r:
            nums = (f"{_signed(r['steps']):>6} {_signed(r['usd'], '{:+.4f}'):>10} "
                    f"{_signed(r['tokens_in']):>8} {_signed(r['tokens_out']):>8}")
        else:
            nums = f"{'-':>6} {'-':>10} {'-':>8} {'-':>8}"
        lines.append(f"{r['task_id']:<22} {r['status']:<10} {nums}")
        if r["status"] in ("regressed", "same-fail") and r["reasons"]:
            lines.extend(f"    - {reason}" for reason in r["reasons"])
    s = diff["summary"]
    lines.append(f"regressions: {s['regressions']}, fixed: {s['fixed']}, "
                 f"new: {s['new']}, removed: {s['removed']}")
    return "\n".join(lines)
