# SPDX-License-Identifier: FSL-1.1-ALv2
"""Versioned prompts of the planner and executor (#125).

Each prompt is a text file next to this module, and `versions.json` holds its version and the
SHA-256 of its text. A test fails when a text changes without its version: bump the version,
update the hash and run the evals (`python -m tradvia.evals`) before merging. Every plan comment
and pull request says which version made it (`Prompt: plan@1`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMES = ("plan", "exec", "architect")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    text: str

    @property
    def ref(self) -> str:
        """`plan@1`: what plan comments, pull requests and eval reports record."""
        return f"{self.name}@{self.version}"


def _registry() -> dict:
    return json.loads((HERE / "versions.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def load(name: str) -> Prompt:
    if name not in NAMES:
        raise KeyError(f"unknown prompt {name!r}")
    entry = _registry()[name]
    return Prompt(name, int(entry["version"]), (HERE / f"{name}.txt").read_text(encoding="utf-8"))


def text(name: str) -> str:
    return load(name).text


def ref(name: str) -> str:
    return load(name).ref


def refs() -> dict[str, str]:
    """`{name: "name@version"}` of every prompt, for eval reports."""
    return {n: ref(n) for n in NAMES}


def drift() -> list[str]:
    """Names whose text no longer matches the hash recorded with their version."""
    reg = _registry()
    return [n for n in NAMES
            if hashlib.sha256((HERE / f"{n}.txt").read_text(encoding="utf-8").encode("utf-8"))
            .hexdigest() != reg[n]["sha256"]]
