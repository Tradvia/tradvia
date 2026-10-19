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
    name: str

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


class GitHubIssueChat:
    """Uses the issue's own comment thread as the approval channel.

    Only replies from people with write access to the repository count and
    bots are ignored, so an outsider commenting on a public issue cannot
    approve a plan. author_association is not enough on its own: members with
    private organization membership show up as CONTRIBUTOR or NONE to the
    Actions token, so the collaborator permission endpoint is checked too.
    """

    name = "GitHub"
    TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")
    WRITE = ("admin", "maintain", "write")

    def __init__(self, token: str, repo: str, issue: int, poll_s: float = 15.0, session=None):
        self.repo, self.issue, self.poll_s = repo, issue, poll_s
        self.s = session or requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        self._created_at: dict[str, str] = {}
        self._can_write: dict[str, bool] = {}

    def _url(self) -> str:
        return f"https://api.github.com/repos/{self.repo}/issues/{self.issue}/comments"

    def post(self, text: str) -> str:
        r = self.s.post(self._url(), json={"body": text}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        self._created_at[str(data["id"])] = data["created_at"]
        return str(data["id"])

    def wait_for_reply(self, after_message_id: str, timeout_s: int) -> dict | None:
        deadline = time.monotonic() + timeout_s
        params = {"per_page": 100}
        if after_message_id in self._created_at:
            params["since"] = self._created_at[after_message_id]
        while True:
            r = self.s.get(self._url(), params=params, timeout=TIMEOUT)
            r.raise_for_status()
            for c in sorted(r.json(), key=lambda c: c["id"]):
                user = c.get("user") or {}
                if (c["id"] > int(after_message_id) and user.get("type") != "Bot"
                        and (c.get("author_association") in self.TRUSTED
                             or self.can_write(user.get("login", "")))):
                    return {"text": c.get("body") or "", "by": user["login"]}
            if time.monotonic() >= deadline:
                return None
            time.sleep(self.poll_s)


    def can_write(self, login: str) -> bool:
        """True when the user has write, maintain or admin permission on the repo."""
        if not login:
            return False
        if login not in self._can_write:
            r = self.s.get(f"https://api.github.com/repos/{self.repo}/collaborators/{login}/permission",
                           timeout=TIMEOUT)
            ok = r.status_code == 200 and r.json().get("permission") in self.WRITE
            self._can_write[login] = ok
        return self._can_write[login]


class DiscordREST:
    """Posts to a channel and polls for the first human reply after a message."""

    API = "https://discord.com/api/v10"
    name = "Discord"

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
