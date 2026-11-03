# SPDX-License-Identifier: FSL-1.1-ALv2
"""Versioned prompts of the planner and executor (#125), with an optional canary (#128).

Each prompt is a text file next to this module, and `versions.json` holds its version and the
SHA-256 of its text. A test fails when a text changes without its version: bump the version,
update the hash and run the evals (`python -m tradvia.evals`) before merging. Every plan comment
and pull request says which version made it (`Prompt: plan@1`).

A prompt may also have a *candidate*: the next version, in `<name>@<version>.txt`, registered
under `candidate` in `versions.json`. It is used only for the cards a project's canary picks
(canary.py); everything else keeps the stable text. A card in the canary records it in the
reference: `Prompt: plan@2 (canary)`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMES = ("plan", "exec", "architect")

# What this process uses: the CLI runs one card per process and the canary is chosen per card.
_canary: dict = {"names": frozenset(), "label": ""}


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


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


@lru_cache(maxsize=None)
def load(name: str, candidate: bool = False) -> Prompt:
    if name not in NAMES:
        raise KeyError(f"unknown prompt {name!r}")
    entry = _registry()[name]
    if not candidate:
        return Prompt(name, int(entry["version"]), (HERE / f"{name}.txt").read_text(encoding="utf-8"))
    cand = entry.get("candidate")
    if not cand:
        raise KeyError(f"prompt {name!r} has no candidate version")
    version = int(cand["version"])
    return Prompt(name, version, (HERE / f"{name}@{version}.txt").read_text(encoding="utf-8"))


def candidates() -> dict[str, str]:
    """`{name: "name@version"}` of the prompts that have a candidate version."""
    reg = _registry()
    return {n: f"{n}@{int(reg[n]['candidate']['version'])}" for n in NAMES if reg[n].get("candidate")}


def text(name: str) -> str:
    return load(name).text


def ref(name: str) -> str:
    return load(name).ref


def refs() -> dict[str, str]:
    """`{name: "name@version"}` of every stable prompt, for eval reports."""
    return {n: ref(n) for n in NAMES}


# --- the canary of this process (#128) -------------------------------------------------------

def use_canary(names: list[str] | tuple[str, ...], model: str | None = None) -> None:
    """Use the candidate of each of `names` from now on in this process, and mark every reference
    as canary (with the model, when the canary also changes it)."""
    unknown = [n for n in names if n not in candidates()]
    if unknown:
        raise KeyError(f"no candidate version for: {', '.join(unknown)}")
    _canary["names"] = frozenset(names)
    _canary["label"] = "canary" + (f", model {model}" if model else "")


def reset_canary() -> None:
    _canary["names"], _canary["label"] = frozenset(), ""


def in_canary() -> bool:
    return bool(_canary["label"])


def active(name: str) -> Prompt:
    return load(name, candidate=name in _canary["names"])


def active_text(name: str) -> str:
    return active(name).text


def active_ref(name: str) -> str:
    """The reference a plan or run records: `plan@1`, or `plan@2 (canary)` inside the canary."""
    label = _canary["label"]
    return active(name).ref + (f" ({label})" if label else "")


def drift() -> list[str]:
    """Texts that no longer match the hash recorded with their version (`plan`, `plan@2`)."""
    reg, out = _registry(), []
    for n in NAMES:
        if _hash(HERE / f"{n}.txt") != reg[n]["sha256"]:
            out.append(n)
        cand = reg[n].get("candidate")
        if cand and _hash(HERE / f"{n}@{int(cand['version'])}.txt") != cand["sha256"]:
            out.append(f"{n}@{int(cand['version'])}")
    return out
