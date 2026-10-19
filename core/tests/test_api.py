# SPDX-License-Identifier: FSL-1.1-ALv2
import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")

from starlette.testclient import TestClient  # noqa: E402

from tradvia.api import Upstream, create_app  # noqa: E402

REPO = "Tradvia/tradvia"
TOKEN = "ghp_supersecret"


class FakeGitHub:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.etag = '"v1"'
        self.body = b'{"n": 1}'

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        if self.fail:
            raise RuntimeError(f"boom {headers}")
        if headers.get("If-None-Match") == self.etag:
            return Upstream(304, self.etag, "application/json", b"")
        return Upstream(200, self.etag, "application/json", self.body)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TRADVIA_OFFLINE", "1")
    monkeypatch.setenv("TRADVIA_REPO", REPO)
    monkeypatch.setenv("TRADVIA_API_CACHE_TTL", "60")
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)


@pytest.fixture
def gh():
    return FakeGitHub()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def client(gh, clock):
    return TestClient(create_app(fetch=gh, now=clock))


def test_healthz(client):
    r = client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.parametrize(
    "path",
    [
        f"repos/{REPO}/issues",
        f"repos/{REPO}/issues/12/timeline",
        f"repos/{REPO}/issues/12/comments",
        f"repos/{REPO}/pulls",
        f"repos/{REPO}/actions/runs",
        f"repos/{REPO}/actions/workflows/agent.yml/runs",
        f"repos/{REPO}/actions/workflows/claude.yml/runs",
    ],
)
def test_allowed_paths(client, gh, path):
    r = client.get(f"/api/github/{path}")
    assert r.status_code == 200
    assert gh.calls[0][0] == f"https://api.github.com/{path}"


@pytest.mark.parametrize(
    "path",
    [
        "repos/Other/repo/issues",
        f"repos/{REPO}/contents/README.md",
        f"repos/{REPO}/issues/abc/timeline",
        f"repos/{REPO}/actions/workflows/other.yml/runs",
        f"repos/{REPO}/actions/workflows/agentxyml/runs",
        f"repos/{REPO}/issues/",
        f"repos/{REPO}/issues/../../../user",
        f"repos/{REPO}/%2e%2e/%2e%2e/user",
        f"repos/{REPO}/issues/%2e%2e/pulls",
        f"repos/{REPO}//issues",
        "user",
    ],
)
def test_rejected_paths(client, gh, path):
    r = client.get(f"/api/github/{path}")
    assert r.status_code == 404
    assert gh.calls == []


def test_non_get_is_rejected(client):
    assert client.post(f"/api/github/repos/{REPO}/issues").status_code == 405


def test_only_whitelisted_query_params(client, gh):
    client.get(
        f"/api/github/repos/{REPO}/issues?state=all&per_page=50&evil=1&labels=a&page=2"
        "&sort=created&direction=desc"
    )
    url = gh.calls[0][0]
    assert url.endswith("?direction=desc&labels=a&page=2&per_page=50&sort=created&state=all")
    assert "evil" not in url


def test_cache_hit_within_ttl(client, gh, clock):
    p = f"/api/github/repos/{REPO}/pulls?state=all"
    assert client.get(p).json() == {"n": 1}
    clock.t += 59
    assert client.get(p).json() == {"n": 1}
    assert len(gh.calls) == 1


def test_expiry_revalidates_with_etag(client, gh, clock):
    p = f"/api/github/repos/{REPO}/pulls"
    client.get(p)
    clock.t += 61
    r = client.get(p)
    assert r.status_code == 200
    assert r.json() == {"n": 1}
    assert len(gh.calls) == 2
    assert gh.calls[1][1]["If-None-Match"] == '"v1"'
    # the 304 refreshed the TTL
    clock.t += 30
    client.get(p)
    assert len(gh.calls) == 2


def test_cache_capped_at_500(client, gh):
    for page in range(501):
        client.get(f"/api/github/repos/{REPO}/pulls?page={page}")
    assert len(gh.calls) == 501
    client.get(f"/api/github/repos/{REPO}/pulls?page=500")  # newest still cached
    assert len(gh.calls) == 501
    client.get(f"/api/github/repos/{REPO}/pulls?page=0")  # oldest was evicted
    assert len(gh.calls) == 502


def test_stale_on_error(client, gh, clock):
    p = f"/api/github/repos/{REPO}/pulls"
    client.get(p)
    clock.t += 61
    gh.fail = True
    r = client.get(p)
    assert r.status_code == 200
    assert r.json() == {"n": 1}
    assert r.headers["X-Tradvia-Stale"] == "1"


def test_stale_on_upstream_5xx(clock):
    down = {"on": False}

    def flaky(url, headers):
        if down["on"]:
            return Upstream(503, None, "text/plain", b"down")
        return Upstream(200, None, "application/json", b'{"n": 1}')

    c = TestClient(create_app(fetch=flaky, now=clock))
    p = f"/api/github/repos/{REPO}/pulls"
    c.get(p)
    clock.t += 61
    down["on"] = True
    r = c.get(p)
    assert r.json() == {"n": 1}
    assert r.headers["X-Tradvia-Stale"] == "1"


def test_error_without_cache_is_502(client, gh):
    gh.fail = True
    r = client.get(f"/api/github/repos/{REPO}/pulls")
    assert r.status_code == 502
    assert TOKEN not in r.text


def test_upstream_rate_limit_is_relayed(gh, clock):
    def limited(url, headers):
        return Upstream(403, None, "application/json", b'{"message": "rate limit"}')

    c = TestClient(create_app(fetch=limited, now=clock))
    r = c.get(f"/api/github/repos/{REPO}/pulls")
    assert r.status_code == 403
    assert TOKEN not in r.text


def test_token_sent_upstream_but_never_returned(client, gh):
    r = client.get(f"/api/github/repos/{REPO}/issues")
    assert gh.calls[0][1]["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in r.text
    assert all(TOKEN not in f"{k}{v}" for k, v in r.headers.items())


def test_no_token_means_no_authorization(monkeypatch, gh, clock):
    monkeypatch.delenv("GITHUB_TOKEN")
    c = TestClient(create_app(fetch=gh, now=clock))
    c.get(f"/api/github/repos/{REPO}/issues")
    assert "Authorization" not in gh.calls[0][1]
