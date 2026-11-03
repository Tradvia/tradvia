# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Guardrails: blocked patterns in what an agent plans, writes or ships (ADR 0015).

`risk.py` decides who approves an action; this module decides what the agent may generate or
run before that. Three kinds of pattern, all plain regular expressions (status: Partial):

- `destructive`: commands such as `rm -rf /`, `git push --force`, `DROP TABLE`;
- `credential`: cloud keys, tokens, private keys and hard-coded secrets;
- `pii`: personal data in code, cards and pull requests (CPF and CNPJ with check digits,
  personal e-mail addresses).

Scope is code, cards and pull requests only. Scanning WhatsApp and e-mail messages belongs
to the enterprise privacy module, which is still Planned.

False positives: add `tradvia: allow <rule>` on the same line, or use one of the well-known
documentation values. Allowed hits are not blocking but are still reported (`allowed=True`),
and a blocked card names the rule id so a human can cite it when asking for an adjustment.

Dependency: the browser driver with credentials (#119) may only be enabled after this
guardrail is active.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

Kind = str  # "destructive" | "credential" | "pii"

# Documentation and test values that are never real.
KNOWN_TEST_VALUES = frozenset({
    "AKIAIOSFODNN7EXAMPLE",
    "000.000.000-00",
    "00000000000",
    "00.000.000/0000-00",
})

_ALLOW = re.compile(r"tradvia:\s*allow\s+([\w,\s*-]+)", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    rule: str
    kind: Kind
    excerpt: str  # masked: never the whole secret
    file: str = ""
    allowed: bool = False


def _digits(s: str) -> list[int]:
    return [int(c) for c in s if c.isdigit()]


def valid_cpf(value: str) -> bool:
    d = _digits(value)
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for n in (9, 10):
        check = sum(x * w for x, w in zip(d[:n], range(n + 1, 1, -1))) * 10 % 11 % 10
        if d[n] != check:
            return False
    return True


def valid_cnpj(value: str) -> bool:
    d = _digits(value)
    if len(d) != 14 or len(set(d)) == 1:
        return False
    for n, weights in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
                       (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
        r = sum(x * w for x, w in zip(d[:n], weights)) % 11
        if d[n] != (0 if r < 2 else 11 - r):
            return False
    return True


@dataclass(frozen=True)
class Rule:
    id: str
    kind: Kind
    pattern: re.Pattern
    validate: object = None  # optional callable(match_text) -> bool


def _r(id: str, kind: Kind, pattern: str, flags: int = 0, validate=None) -> Rule:
    return Rule(id, kind, re.compile(pattern, flags), validate)


_END = r"(?=\s|$|\*|/\*|[\"';&|)])"
RULES: tuple[Rule, ...] = (
    _r("rm-rf-root", "destructive",
       r"\brm\s+(?:-\w+\s+)*-\w*(?:r\w*f|f\w*r)\w*\s+(?:--no-preserve-root\s+)?(?:/|~)" + _END),
    _r("git-force-push", "destructive",
       r"\bgit\s+push\b[^\n]*(?:--force\b(?!-with-lease)|\s-f\b)"),
    _r("git-reset-hard", "destructive", r"\bgit\s+reset\s+--hard\b"),
    _r("sql-drop", "destructive", r"\bDROP\s+(?:TABLE|DATABASE)\b", re.IGNORECASE),
    _r("sql-truncate", "destructive", r"\bTRUNCATE\s+TABLE\b", re.IGNORECASE),
    _r("mkfs", "destructive", r"\bmkfs(?:\.\w+)?\s+/dev/"),
    _r("dd-device", "destructive", r"\bdd\b[^\n]*\bof=/dev/"),
    _r("curl-pipe-shell", "destructive",
       r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b"),
    _r("aws-access-key", "credential", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    _r("github-token", "credential", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    _r("private-key", "credential", r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    _r("api-key-sk", "credential", r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    _r("hardcoded-secret", "credential",
       r"\b(?:password|passwd|secret|token|api_key)\b\s*[:=]\s*['\"][^'\"\s]{6,}['\"]",
       re.IGNORECASE),
    _r("cpf", "pii", r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b", validate=valid_cpf),
    _r("cnpj", "pii", r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b", validate=valid_cnpj),
    _r("personal-email", "pii",
       r"\b[A-Za-z0-9._%+-]+@(?:gmail|hotmail|outlook|yahoo|icloud|live)\.com(?:\.br)?\b",
       re.IGNORECASE),
)


def mask(value: str) -> str:
    """Keep only a short prefix so the report never carries the whole secret."""
    keep = 4 if len(value) > 8 else 2
    return value[:keep] + "*" * min(len(value) - keep, 8)


def _allowed_rules(line: str) -> set[str]:
    m = _ALLOW.search(line)
    return {r.strip().lower() for r in re.split(r"[,\s]+", m.group(1)) if r.strip()} if m else set()


def scan_text(text: str, file: str = "", *, with_allowed: bool = False) -> list[Finding]:
    """Findings in `text`; allowed ones (marker or known test value) only with `with_allowed`."""
    found: list[Finding] = []
    for line in text.splitlines():
        marked = _allowed_rules(line)
        for rule in RULES:
            for m in rule.pattern.finditer(line):
                value = m.group(0)
                if rule.validate and not rule.validate(value):
                    continue
                allowed = (rule.id in marked or "*" in marked or value in KNOWN_TEST_VALUES)
                if allowed and not with_allowed:
                    continue
                excerpt = value.strip() if rule.kind == "destructive" else mask(value)
                found.append(Finding(rule.id, rule.kind, excerpt[:80], file, allowed))
    return found


def scan_files(files: dict[str, str], *, with_allowed: bool = False) -> list[Finding]:
    """Scan `{path: content}`."""
    out: list[Finding] = []
    for path, content in files.items():
        out += scan_text(content, path, with_allowed=with_allowed)
    return out


def added_lines(new: str, old: str | None) -> str:
    """The lines of `new` that `old` does not already have (a multiset difference, in order).

    The pre-PR review scans only what a change adds: a fake secret that already sits in a test
    file on the base branch must not block every later change to that file. A line repeated in
    `new` more often than in `old` counts as added. `old` None (a new file) keeps everything.
    """
    if old is None:
        return new
    from collections import Counter

    left = Counter(old.splitlines())
    out = []
    for line in new.splitlines():
        if left[line] > 0:
            left[line] -= 1
        else:
            out.append(line)
    return "\n".join(out)


def review(files: dict[str, str]) -> dict:
    """The `guardrail` card field (minus `phase`): state, blocking findings, allowed rules."""
    found = scan_files(files, with_allowed=True)
    blocking = [asdict(f) for f in found if not f.allowed]
    return {"state": "blocked" if blocking else "ok",
            "findings": blocking,
            "suppressed": sorted({f.rule for f in found if f.allowed})}


def block_message(card_id: str, guardrail: dict) -> str:
    rules = sorted({f["rule"] for f in guardrail["findings"]})
    lines = "\n".join(f"- `{f['rule']}` ({f['kind']}) em `{f['file'] or 'card'}`: `{f['excerpt']}`"
                      for f in guardrail["findings"][:10])
    return (f"⛔ **{card_id}** — bloqueado por guardrail: {', '.join(rules)} "
            f"(fase `{guardrail['phase']}`). Nada foi executado nem aberto.\n{lines}\n"
            "Para seguir: responda com um ajuste do plano (replan) ou, se for falso positivo, "
            "cite a regra e marque a linha com `tradvia: allow <regra>`. "
            "O driver de navegador com credenciais (#119) só pode ser habilitado com este "
            "guardrail ativo.")
