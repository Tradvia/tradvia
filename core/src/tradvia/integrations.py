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

from .policy import Policy, role_of

TIMEOUT = 30


class GitHub(Protocol):
    def get_issue(self, number: int) -> dict: ...
    def create_branch(self, name: str) -> None: ...
    def put_file(self, branch: str, path: str, content: str, message: str) -> None: ...
    def open_pull_request(self, branch: str, title: str, body: str) -> str: ...
    def comment(self, number: int, body: str) -> None: ...
    def get_file(self, path: str, ref: str) -> str | None: ...
    def get_collaborator_role(self, login: str) -> str | None: ...


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

    def get_file(self, path: str, ref: str) -> str | None:
        """Text of a file at `ref`, or None when it does not exist."""
        r = self.s.get(self._url(f"/contents/{path}"), params={"ref": ref}, timeout=TIMEOUT)
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise RuntimeError(f"GitHub GET /contents/{path} -> {r.status_code}: {r.text[:300]}")
        return base64.b64decode(r.json()["content"]).decode("utf-8")

    def get_collaborator_role(self, login: str) -> str | None:
        """role_name (admin, maintain, write, triage, read), or None for a non-collaborator."""
        r = self.s.get(self._url(f"/collaborators/{login}/permission"), timeout=TIMEOUT)
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise RuntimeError(f"GitHub GET /collaborators/{login}/permission -> "
                               f"{r.status_code}: {r.text[:300]}")
        return r.json().get("role_name")


class GitHubIssueChat:
    """Uses the issue's own comment thread as the approval channel.

    Only replies from people whose GitHub role meets the policy's approve_plan
    minimum count (write by default) and bots are ignored, so an outsider
    commenting on a public issue cannot approve a plan. The role comes from the
    collaborator permission endpoint (role_name): author_association cannot
    tell triage from write.
    """

    name = "GitHub"

    def __init__(self, token: str, repo: str, issue: int, poll_s: float = 15.0, session=None,
                 policy: Policy | None = None):
        self.repo, self.issue, self.poll_s = repo, issue, poll_s
        self.policy = policy or Policy()
        self.s = session or requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        self._created_at: dict[str, str] = {}
        self._roles: dict[str, str | None] = {}

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
                        and self.policy.allows("approve_plan",
                                               role_of(self, user.get("login", "")))):
                    return {"text": c.get("body") or "", "by": user["login"]}
            if time.monotonic() >= deadline:
                return None
            time.sleep(self.poll_s)
    def get_collaborator_role(self, login: str) -> str | None:
        """Cached role_name of `login`; None when not a collaborator."""
        if login not in self._roles:
            r = self.s.get(f"https://api.github.com/repos/{self.repo}/collaborators/{login}/permission",
                           timeout=TIMEOUT)
            self._roles[login] = r.json().get("role_name") if r.status_code == 200 else None
        return self._roles[login]


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
