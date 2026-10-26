# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""CLI: `python -m tradvia.evals run --label X [--out report.json]` and `... compare base head`."""

from __future__ import annotations

import argparse
import json
import sys

from . import load_tasks
from .compare import compare, render
from .runner import make_planner, run_suite


def _table(report: dict) -> str:
    head = f"{'task':<22} {'ok':<5} {'steps':>5} {'usd':>9} {'tok in':>7} {'tok out':>8}"
    lines = [f"suite {report['label']}", head, "-" * len(head)]
    for r in report["results"]:
        c = r["cost"]
        lines.append(f"{r['task_id']:<22} {'pass' if r['passed'] else 'FAIL':<5} {r['steps']:>5} "
                     f"{c['usd']:>9.4f} {c['tokens_in']:>7} {c['tokens_out']:>8}")
        lines.extend(f"    - {reason}" for reason in r["reasons"])
    t = report["totals"]
    lines.append(f"passed {t['passed']}/{t['tasks']} · steps {t['steps']} · usd {t['usd']:.4f} · "
                 f"tokens {t['tokens_in']} in / {t['tokens_out']} out")
    return "\n".join(lines)


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tradvia.evals")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the golden tasks and print a report")
    run.add_argument("--label", required=True, help="prompt/model version (scripted: script name)")
    run.add_argument("--planner", choices=("scripted", "live"), default="scripted",
                     help="live needs TRADVIA_EVALS_LIVE=1 and is never part of CI")
    run.add_argument("--out", help="write the JSON report here")
    cmp_ = sub.add_parser("compare", help="compare two JSON reports task by task")
    cmp_.add_argument("base")
    cmp_.add_argument("head")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "run":
            report = run_suite(load_tasks(), make_planner(args.planner, args.label), args.label)
            print(_table(report))
            if args.out:
                with open(args.out, "w", encoding="utf-8") as f:
                    json.dump(report, f, indent=2, ensure_ascii=False)
            # the run itself reports; gating on pass/fail is compare's job
            return 0
        diff = compare(_load(args.base), _load(args.head))
        print(render(diff))
        return 1 if diff["regressed"] else 0
    except (ValueError, RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
