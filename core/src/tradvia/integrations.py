# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Anderson dos Santos Dutra
"""Thin clients for GitHub (REST) and Discord (bot REST API).

v0.1 talks to the REST APIs directly. The GitHub MCP server replaces this
client in a later version without changing the graph.
"""

from __future__ import annotations

import base64
import time
from typing import Protocol

import requests

TIMEOUT = 30


class GitHub(Protocol):
    def get_issue(self, number: int) -> dict: ...
    def create_branch(self, name: str) -> None: ...
    def put_file(self, branch: str, path: str, content: str, message: str) -> None: ...
    def open_pull_request(self, branch: str, title: str, body: str) -> str: ...
    def comment(self, number: int, body: str) -> None: ...


class Chat(Protocol):
    def post(self, text: str) -> str: ...
    def wait_for_reply(self, after_message_id: str, timeout_s: int) -> dict | None: ...


class GitHubREST:
    def __init__(self, token: str, repo: str, base_branch: str = "main"):
        self.repo = repo
        self.base = base_branch
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def _url(self, path: str) -> str:
        return f"https://api.github.com/repos/{self.repo}{path}"

    def _req(self, method: str, path: str, **kw) -> dict:
        r = self.s.request(method, self._url(path), timeout=TIMEOUT, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"GitHub {method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    def get_issue(self, number: int) -> dict:
        data = self._req("GET", f"/issues/{number}")
        return {"number": number, "title": data["title"], "body": data.get("body") or "",
                "url": data["html_url"]}

    def create_branch(self, name: str) -> None:
        sha = self._req("GET", f"/git/ref/heads/{self.base}")["object"]["sha"]
        try:
            self._req("POST", "/git/refs", json={"ref": f"refs/heads/{name}", "sha": sha})
        except RuntimeError as e:
            if "Reference already exists" not in str(e):
                raise

    def put_file(self, branch: str, path: str, content: str, message: str) -> None:
        payload = {"message": message, "branch": branch,
                   "content": base64.b64encode(content.encode()).decode()}
        r = self.s.get(self._url(f"/contents/{path}"), params={"ref": branch}, timeout=TIMEOUT)
        if r.status_code == 200:
            payload["sha"] = r.json()["sha"]
        self._req("PUT", f"/contents/{path}", json=payload)

    def open_pull_request(self, branch: str, title: str, body: str) -> str:
        pr = self._req("POST", "/pulls", json={"title": title, "head": branch,
                                               "base": self.base, "body": body})
        return pr["html_url"]

    def comment(self, number: int, body: str) -> None:
        self._req("POST", f"/issues/{number}/comments", json={"body": body})


class DiscordREST:
    """Posts to a channel and polls for the first human reply after a message."""

    API = "https://discord.com/api/v10"

    def __init__(self, bot_token: str, channel_id: str, poll_s: float = 3.0):
        self.channel = channel_id
        self.poll_s = poll_s
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bot {bot_token}"})

    def post(self, text: str) -> str:
        r = self.s.post(f"{self.API}/channels/{self.channel}/messages",
                        json={"content": text[:2000]}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()["id"]

    def wait_for_reply(self, after_message_id: str, timeout_s: int) -> dict | None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            r = self.s.get(f"{self.API}/channels/{self.channel}/messages",
                           params={"after": after_message_id, "limit": 20}, timeout=TIMEOUT)
            r.raise_for_status()
            humans = [m for m in r.json() if not m["author"].get("bot")]
            if humans:
                m = min(humans, key=lambda m: int(m["id"]))  # oldest reply first
                return {"text": m.get("content", ""), "by": m["author"]["username"]}
            time.sleep(self.poll_s)
        return None
