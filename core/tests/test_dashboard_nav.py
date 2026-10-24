# SPDX-License-Identifier: FSL-1.1-ALv2
"""The dashboard's navigation and preference logic (dashboard/index.html), run in Node when available."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parents[2] / "dashboard" / "index.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def block(name: str) -> str:
    text = HTML.read_text(encoding="utf-8")
    return text[text.index(f"// {name} (pure)"):text.index(f"// end {name}")]


def run(name: str, expr: str, arg=None):
    script = block(name) + f"\nconsole.log(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", script, json.dumps(arg)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def items(rights):
    return run("nav", "navItems(JSON.parse(process.argv[1]))", rights)


@pytest.mark.parametrize("rights", [None, {"role": None}, {"role": "read"}, {"role": "write"}, {"role": "admin"}])
def test_every_role_gets_panel_settings_and_help(rights):
    ids = {i["id"]: i for i in items(rights)}
    for key in ("panel", "settings", "help"):
        assert ids[key]["status"] == "ready"


def test_settings_is_limited_without_a_session():
    assert {i["id"]: i for i in items(None)}["settings"]["limited"] is True
    assert {i["id"]: i for i in items({"role": "read"})}["settings"]["limited"] is False


def test_connections_is_coming_soon():
    assert {i["id"]: i for i in items({"role": "admin"})}["connections"]["status"] == "soon"


@pytest.mark.parametrize("hash_,view", [
    ("", "panel"), ("#settings", "settings"), ("#/help", "help"), ("#panel", "panel"),
    ("#connections", "panel"), ("#nope", "panel"), ("#__proto__", "panel"), (None, "panel"),
])
def test_parse_view_whitelists(hash_, view):
    assert run("nav", "parseView(JSON.parse(process.argv[1]))", hash_) == view


def prefs(setup: str, expr: str):
    script = block("prefs") + f"\n{setup}\nconsole.log(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


FAKE = "const m = {}; const st = { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = v; } };"


def test_prefs_round_trip():
    got = prefs(FAKE + "prefsSave(st, { lang: 'en', theme: 'light' });", "prefsLoad(st, 'pt-BR')")
    assert got == {"lang": "en", "theme": "light"}


def test_prefs_defaults_follow_browser_language():
    assert prefs(FAKE, "prefsLoad(st, 'pt-BR')") == {"lang": "pt", "theme": "dark"}
    assert prefs(FAKE, "prefsLoad(st, 'en-US')") == {"lang": "en", "theme": "dark"}


@pytest.mark.parametrize("stored", ["{not json", '{"lang":"xx","theme":"neon"}', "null", "42"])
def test_prefs_fall_back_on_corrupt_or_invalid_values(stored):
    setup = FAKE + f"m['tradvia.prefs'] = {json.dumps(stored)};"
    assert prefs(setup, "prefsLoad(st, 'en')") == {"lang": "en", "theme": "dark"}


def test_prefs_keep_the_valid_half():
    setup = FAKE + "m['tradvia.prefs'] = '{\"lang\":\"xx\",\"theme\":\"light\"}';"
    assert prefs(setup, "prefsLoad(st, 'en')") == {"lang": "en", "theme": "light"}


def test_prefs_do_not_throw_without_storage():
    assert prefs("", "prefsLoad(null, 'pt')") == {"lang": "pt", "theme": "dark"}
    assert prefs("", "prefsSave(null, { lang: 'en', theme: 'dark' })") is False
    throwing = "const st = { getItem() { throw new Error('x'); }, setItem() { throw new Error('x'); } };"
    assert prefs(throwing, "[prefsLoad(st, 'en'), prefsSave(st, { lang: 'en', theme: 'dark' })]") == [
        {"lang": "en", "theme": "dark"}, False]
