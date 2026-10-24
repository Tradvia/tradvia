# SPDX-License-Identifier: FSL-1.1-ALv2
import base64
import importlib.util
import json
import logging
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from starlette.testclient import TestClient  # noqa: E402

from tradvia import connections  # noqa: E402
from tradvia.actions import GitHubError  # noqa: E402
from tradvia.api import create_app  # noqa: E402
from tradvia.auth import Keys  # noqa: E402
from tradvia.policy import PolicyError, parse_policy  # noqa: E402
from tradvia.store import MemoryStore  # noqa: E402

A = "Tradvia/tradvia"
B = "Acme/widgets"
ORIGIN = "https://app.test"
TOKEN = "ghu_SECRET_user_token_0123456789"
CODE = "code-from-github"
API_KEY = "sk-ant-api03-SUPERSECRETVALUE0123456789"
NEW_KEY = "sk-ant-api03-REPLACEMENTVALUE9876543210"
SECRET_KEY = "k" * 43

# Only the tests that open a sealed box need PyNaCl (the `dev` extra installs it).
needs_nacl = pytest.mark.skipif(
    importlib.util.find_spec("nacl") is None, reason="PyNaCl not installed"
)


class FakeGitHub:
    """Identity, policy file and the Actions secrets API, all recorded."""

    def __init__(self):
        self.perm = {A: ("admin", "admin"), B: ("read", "read")}
        self.policy = None
        self.private = None  # the repository key pair, made on first use
        self.key_id = "key-123"
        self.secrets = {}  # (repo, name) -> (encrypted_value, key_id)
        self.calls = []
        self.fail_put = False

    def exchange_code(self, code, redirect_uri, code_verifier):
        return TOKEN

    def get_user(self, token):
        return {"id": 7, "login": "ana", "avatar_url": None}

    def get_permission(self, token, repo, login):
        return self.perm.get(repo, (None, None))

    def get_file(self, token, repo, path, ref):
        return self.policy

    def get_actions_public_key(self, token, repo):
        from nacl.public import PrivateKey

        self.calls.append(("public_key", token, repo))
        self.private = self.private or PrivateKey.generate()
        return self.key_id, base64.b64encode(bytes(self.private.public_key)).decode()

    def put_actions_secret(self, token, repo, name, encrypted_value, key_id):
        self.calls.append(("put", token, repo, name))
        if self.fail_put:
            raise GitHubError(403, "Resource not accessible by integration")
        self.secrets[(repo, name)] = (encrypted_value, key_id)

    def delete_actions_secret(self, token, repo, name):
        self.calls.append(("delete", token, repo, name))
        if (repo, name) not in self.secrets:
            raise GitHubError(404)
        del self.secrets[(repo, name)]

    def opened(self, repo, name):
        from nacl.public import SealedBox

        encrypted, key_id = self.secrets[(repo, name)]
        return SealedBox(self.private).decrypt(base64.b64decode(encrypted)).decode(), key_id


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TRADVIA_OFFLINE", "1")
    monkeypatch.setenv("TRADVIA_REPO", A)
    monkeypatch.setenv("TRADVIA_PROJECTS", f"{A}, {B}")
    monkeypatch.setenv("TRADVIA_DOMAIN", "app.test")
    monkeypatch.setenv("TRADVIA_GITHUB_APP_CLIENT_ID", "Iv1.client")
    monkeypatch.setenv("TRADVIA_GITHUB_APP_CLIENT_SECRET", "client-secret-value")
    monkeypatch.setenv("TRADVIA_SECRET_KEY", SECRET_KEY)
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def gh():
    return FakeGitHub()


def make_client(store, gh):
    client = TestClient(
        create_app(store=store, identity=gh, clock=lambda: 1_800_000_000.0),
        base_url=ORIGIN, follow_redirects=False,
    )
    r = client.get("/api/auth/login")
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    assert client.get("/api/auth/callback", params={"code": CODE, "state": state}).status_code == 302
    return client


@pytest.fixture
def client(store, gh):
    return make_client(store, gh)


def headers(client):
    return {"Origin": ORIGIN, "X-Tradvia-CSRF": client.get("/api/auth/me").json()["csrf"]}


def url(repo=A, tail=""):
    return f"/api/projects/{repo}/connections{tail}"


def put_secret(client, value=API_KEY, provider="anthropic", repo=A):
    return client.put(url(repo, f"/secrets/{provider}"), json={"value": value}, headers=headers(client))


def everything_stored(store):
    return json.dumps([store.audit, store.connection_roles, store.connection_secrets], default=str)


# encryption ------------------------------------------------------------------------


def test_secret_cipher_round_trip_and_wrong_key_fails():
    keys = Keys(SECRET_KEY)
    cipher = keys.encrypt_secret(API_KEY)
    assert API_KEY not in cipher and keys.decrypt_secret(cipher) == API_KEY
    assert Keys("x" * 43).decrypt_secret(cipher) is None
    # independent from the key that protects user tokens
    assert keys.decrypt(cipher) is None and keys.decrypt_secret(keys.encrypt("t")) is None


def test_database_holds_only_ciphertext(client, store):
    assert put_secret(client).status_code == 200
    pid = store.ensure_project(A)
    stored = store.get_connection_secret(pid, "anthropic")
    assert stored != API_KEY and API_KEY not in stored
    assert Keys(SECRET_KEY).decrypt_secret(stored) == API_KEY
    assert Keys("x" * 43).decrypt_secret(stored) is None


@needs_nacl
def test_seal_is_a_sealed_box_github_can_open():
    from nacl.public import PrivateKey, SealedBox

    private = PrivateKey.generate()
    sealed = connections.seal(base64.b64encode(bytes(private.public_key)).decode(), "value")
    assert SealedBox(private).decrypt(base64.b64decode(sealed)) == b"value"


# the secret never comes back, never reaches a log or the audit -----------------------------


def test_secret_never_in_responses_logs_or_audit(client, store, caplog):
    caplog.set_level(logging.DEBUG)
    bodies = [put_secret(client).text, client.get(url()).text]
    bodies.append(put_secret(client, NEW_KEY).text)
    bodies.append(client.delete(url(tail="/secrets/anthropic"), headers=headers(client)).text)
    bodies.append(put_secret(client, "bad\nvalue").text)
    for text in bodies + [caplog.text, everything_stored(store)]:
        assert API_KEY not in text and NEW_KEY not in text
    assert "SUPERSECRET" not in caplog.text


def test_status_shows_when_and_by_whom_only(client):
    put_secret(client)
    body = client.get(url()).json()["connections"]
    anthropic = next(p for p in body["providers"] if p["key"] == "anthropic")
    assert anthropic["secret"]["configured"] is True and anthropic["secret"]["set_by"] == "ana"
    assert anthropic["secret"]["set_at"]
    assert set(anthropic["secret"]) == {"configured", "set_at", "set_by"}
    gemini = next(p for p in body["providers"] if p["key"] == "gemini")
    assert gemini["secret"] == {"configured": False}


def test_replace_and_remove_are_audited(client, store):
    put_secret(client)
    put_secret(client, NEW_KEY)
    client.delete(url(tail="/secrets/anthropic"), headers=headers(client))
    pid = store.ensure_project(A)
    assert [r["action"] for r in store.audit if r["project_id"] == pid] == [
        "secret_set", "secret_replace", "secret_remove"]
    assert all(r["actor_login"] == "ana" and r["result"] == "ok" for r in store.audit)
    assert "provider=anthropic" in store.audit[0]["detail"]
    assert store.get_connection_secret(pid, "anthropic") is None


def test_role_assignment_is_audited(client, store):
    r = client.put(url(tail="/roles/planner"), headers=headers(client),
                   json={"provider": "gemini", "model": "gemini-2.5-pro"})
    assert r.status_code == 200
    assert store.audit[-1]["action"] == "set_connection"
    assert "gemini-2.5-pro" in store.audit[-1]["detail"]


# roles ----------------------------------------------------------------------------------


def test_admin_is_required(store, gh):
    gh.perm[A] = ("maintain", "maintain")
    client = make_client(store, gh)
    assert client.get(url()).status_code == 403
    assert put_secret(client).status_code == 403
    assert client.delete(url(tail="/secrets/anthropic"), headers=headers(client)).status_code == 403
    assert client.put(url(tail="/roles/planner"), headers=headers(client),
                      json={"provider": "openai", "model": "gpt-5"}).status_code == 403
    assert not store.connection_secrets and not store.connection_roles
    assert [r["result"] for r in store.audit] == ["denied"] * 4
    assert client.get("/api/auth/me").json()["projects"][0]["can_manage_connections"] is False


def test_yml_cannot_lower_the_requirement(store, gh):
    gh.policy = "interactions:\n  manage_connections: write\n"
    client = make_client(store, gh)
    assert client.get(url()).status_code == 403  # invalid policy: fail closed


def test_read_role_and_other_project(store, gh):
    client = make_client(store, gh)
    assert put_secret(client, repo=B).status_code == 403
    assert client.get(url("Other/repo")).status_code == 404


def test_anonymous_is_401_and_csrf_is_required(client, store, gh):
    anonymous = TestClient(create_app(store=store, identity=gh), base_url=ORIGIN)
    assert anonymous.get(url()).status_code == 401
    assert anonymous.put(url(tail="/secrets/anthropic"), json={"value": API_KEY}).status_code == 401
    no_csrf = client.put(url(tail="/secrets/anthropic"), json={"value": API_KEY},
                         headers={"Origin": ORIGIN})
    assert no_csrf.status_code == 403
    assert not store.connection_secrets


@pytest.mark.parametrize("path, body", [
    ("/secrets/unknown", {"value": "x"}),
    ("/secrets/github", {"value": "x"}),
    ("/secrets/anthropic", {"value": ""}),
    ("/secrets/anthropic", {"value": 5}),
    ("/secrets/anthropic", {}),
    ("/roles/boss", {"provider": "openai", "model": "gpt-5"}),
    ("/roles/planner", {"provider": "gitlab", "model": "x"}),
    ("/roles/planner", {"provider": "openai", "model": "claude-opus-5-5"}),
    ("/roles/planner", {}),
])
def test_invalid_input_is_400(client, path, body):
    r = client.put(url(tail=path), json=body, headers=headers(client))
    assert r.status_code == 400 and "error" in r.json()


# delivery to GitHub Actions -----------------------------------------------------------------


@needs_nacl
def test_secret_is_written_to_github_actions_sealed(client, gh):
    body = put_secret(client).json()
    assert body["github_actions"] == "ok"
    value, key_id = gh.opened(A, "ANTHROPIC_API_KEY")
    assert value == API_KEY and key_id == "key-123"
    assert [c[0] for c in gh.calls] == ["public_key", "put"]
    assert all(c[1] == TOKEN and c[2] == A for c in gh.calls)  # the user's own token


@needs_nacl
def test_replace_overwrites_and_remove_deletes_the_github_secret(client, gh):
    put_secret(client)
    put_secret(client, NEW_KEY)
    assert gh.opened(A, "ANTHROPIC_API_KEY")[0] == NEW_KEY
    body = client.delete(url(tail="/secrets/anthropic"), headers=headers(client)).json()
    assert body["github_actions"] == "ok" and body["removed"] is True
    assert (A, "ANTHROPIC_API_KEY") not in gh.secrets and gh.calls[-1][0] == "delete"


@needs_nacl
def test_each_provider_uses_its_own_secret_name(client, gh):
    for provider, name in [("gemini", "GEMINI_API_KEY"), ("openai", "OPENAI_API_KEY"),
                           ("claude-code", "CLAUDE_CODE_OAUTH_TOKEN")]:
        assert put_secret(client, "v-" + provider, provider).status_code == 200
        assert gh.opened(A, name)[0] == "v-" + provider


def test_failed_github_delivery_keeps_the_secret_and_says_so(client, gh, store):
    gh.fail_put = True
    r = put_secret(client)
    assert r.status_code == 200 and r.json()["github_actions"] == "failed"
    assert store.get_connection_secret(store.ensure_project(A), "anthropic")
    assert "github_actions=failed" in store.audit[-1]["detail"]
    assert "Resource not accessible" not in r.text


def test_delete_of_a_missing_github_secret_is_ok(client, gh):
    gh.fail_put = True
    put_secret(client)
    r = client.delete(url(tail="/secrets/anthropic"), headers=headers(client))
    assert r.status_code == 200 and r.json()["github_actions"] == "ok"


# self-hosted runner -----------------------------------------------------------------------


def test_runner_env_has_the_decrypted_secrets_and_does_not_print(store):
    keys = Keys(SECRET_KEY)
    service = connections.Connections(store, keys, None, lambda: 1.0)
    pid = store.ensure_project(A)
    service.set_secret(pid, "ana", A, None, "anthropic", API_KEY)
    service.set_secret(pid, "ana", A, None, "openai", "sk-openai-0123456789abcdef")
    env = service.runner_env(pid)
    assert env == {"ANTHROPIC_API_KEY": API_KEY, "OPENAI_API_KEY": "sk-openai-0123456789abcdef"}
    assert API_KEY not in repr(env) and API_KEY not in str(env)
    assert service.runner_env(store.ensure_project(B)) == {}
    other = connections.Connections(store, Keys("z" * 43), None, lambda: 1.0)
    assert other.runner_env(pid) == {}


def test_executor_output_is_redacted(monkeypatch):
    from tradvia.claude_code import run_claude

    class Proc:
        returncode = 1
        stdout = json.dumps({"is_error": True, "result": f"auth failed with {API_KEY}"})
        stderr = ""

    seen = {}

    def runner(cmd, **kw):
        seen.update(kw)
        return Proc()

    env = connections.SecretEnv(ANTHROPIC_API_KEY=API_KEY)
    with pytest.raises(RuntimeError) as e:
        run_claude("p", ".", "Read", runner=runner, env=env)
    assert API_KEY not in str(e.value) and connections.MASK in str(e.value)
    assert seen["env"]["ANTHROPIC_API_KEY"] == API_KEY  # the subprocess still gets it


def test_redact_known_values_and_key_shapes():
    assert connections.redact(f"x {API_KEY} y", [API_KEY]) == f"x {connections.MASK} y"
    assert API_KEY not in connections.redact(f"x {API_KEY}")
    assert "ghp_" not in connections.redact("t=ghp_0123456789abcdefghij")
    assert connections.redact("nothing here") == "nothing here"


# precedence -------------------------------------------------------------------------------


def test_resolve_prefers_tradvia_over_yml_and_names_the_origin():
    yml = {"planner": {"provider": "anthropic", "model": "claude-sonnet-5-5"},
           "executor": {"provider": "anthropic", "model": "claude-opus-5-5"}}
    db = {"planner": {"provider": "openai", "model": "gpt-5", "updated_by": "ana"}}
    out = connections.resolve(yml, db)
    assert (out["planner"]["provider"], out["planner"]["source"]) == ("openai", "tradvia")
    assert (out["executor"]["model"], out["executor"]["source"]) == ("claude-opus-5-5", "tradvia.yml")
    assert out["reviewer"]["source"] == "unset" and out["reviewer"]["provider"] is None
    assert list(out) == list(connections.FLOW_ROLES)


def test_yml_default_then_tradvia_override_through_the_api(store, gh):
    gh.policy = ("providers:\n  planner:\n    provider: anthropic\n    model: claude-sonnet-5-5\n"
                 "  reviewer:\n    provider: gemini\n    model: gemini-2.5-flash\n")
    client = make_client(store, gh)
    roles = client.get(url()).json()["connections"]["roles"]
    assert roles["planner"]["source"] == "tradvia.yml" and roles["planner"]["model"] == "claude-sonnet-5-5"
    assert roles["executor"]["source"] == "unset"

    r = client.put(url(tail="/roles/planner"), headers=headers(client),
                   json={"provider": "openai", "model": "gpt-5"})
    roles = r.json()["connections"]["roles"]
    assert roles["planner"] == {"provider": "openai", "model": "gpt-5", "source": "tradvia",
                                "updated_by": "ana", "updated_at": roles["planner"]["updated_at"]}
    assert roles["reviewer"]["source"] == "tradvia.yml"


# .tradvia.yml ---------------------------------------------------------------------------------


def test_parse_policy_accepts_providers():
    p = parse_policy("providers:\n  planner: {provider: openai, model: gpt-5}\n")
    assert p.providers == {"planner": {"provider": "openai", "model": "gpt-5"}}
    assert parse_policy(None).providers == {} and parse_policy("access: {}\n").providers == {}
    assert p.interactions.manage_connections == "admin"


@pytest.mark.parametrize("text", [
    "providers: [a]\n",
    "providers:\n  boss: {provider: openai, model: gpt-5}\n",
    "providers:\n  planner: {provider: nope, model: gpt-5}\n",
    "providers:\n  planner: {provider: openai, model: claude-opus-5-5}\n",
    "providers:\n  planner: {provider: openai}\n",
    "providers:\n  planner: {provider: openai, model: gpt-5, key: x}\n",
    "providers:\n  planner: openai\n",
    "interactions:\n  manage_connections: maintain\n",
])
def test_parse_policy_rejects_invalid_providers(text):
    with pytest.raises(PolicyError):
        parse_policy(text)


def test_manage_connections_may_be_restated_as_admin():
    assert parse_policy("interactions:\n  manage_connections: admin\n").interactions.manage_connections == "admin"


# catalog ---------------------------------------------------------------------------------------


def test_catalog_states_real_status():
    status = {p.key: p.status for p in connections.PROVIDERS}
    assert status["gitlab"] == "Planned" and status["github"] == "Shipped"
    assert status["gemini"] == "In progress" and status["openai"] == "In progress"
    assert set(status.values()) <= {"Shipped", "In progress", "Partial", "Planned"}
