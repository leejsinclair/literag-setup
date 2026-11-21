"""T036 — FR-018, SC-010.

With a revoked Google refresh token, `pull` exits 3 with
`ERROR: drive sign-in expired. Renew it with scripts/kb.sh auth google`.
`auth google` runs the loopback installed-app flow and saves the token with mode 0600.
"""

from __future__ import annotations

import json
import stat

import pytest

from drive_fakes import ACCESS_TOKEN, SECRET_CLIENT, SECRET_REFRESH, Harness

EXPIRED = "ERROR: drive sign-in expired. Renew it with scripts/kb.sh auth google"


@pytest.fixture
def h(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def _no_secrets(*texts):
    for text in texts:
        for secret in (SECRET_REFRESH, SECRET_CLIENT, ACCESS_TOKEN):
            assert secret not in text


def test_revoked_refresh_token_exits_3_naming_the_renew_command(h):
    h.drive.revoked = True
    r = h.pull()
    assert r.rc == 3
    assert EXPIRED in r.err.splitlines()
    _no_secrets(r.out, r.err)
    # No Drive API call was made with a dead token.
    assert not h.drive.calls("/drive/v3/")
    entry = h.last_log("pull")["per_source"][0]
    assert entry["outcome"] == "auth_expired"
    assert entry["renew_command"] == "scripts/kb.sh auth google"


def test_missing_token_file_is_an_expired_sign_in(h):
    (h.secrets_dir / "google-token.json").unlink()
    r = h.pull()
    assert r.rc == 3
    assert EXPIRED in r.err.splitlines()


def test_401_after_refresh_is_an_expired_sign_in(h, monkeypatch):
    original = h.drive.handle

    def always_401(request):
        h.drive.requests.append(request)
        import httpx

        return httpx.Response(401, json={"error": {"code": 401, "message": "Invalid Credentials"}})

    monkeypatch.setattr(h.drive, "handle", always_401)
    r = h.pull()
    assert r.rc == 3
    assert EXPIRED in r.err.splitlines()
    monkeypatch.setattr(h.drive, "handle", original)


def test_records_hold_no_secret_after_a_pull(h):
    h.pull()
    for path in h.records_dir.rglob("*"):
        if path.is_file():
            blob = path.read_bytes()
            for secret in (SECRET_REFRESH, SECRET_CLIENT, ACCESS_TOKEN):
                assert secret.encode() not in blob, path


class _FakeCreds:
    token = ACCESS_TOKEN

    def to_json(self):
        return json.dumps({"token": ACCESS_TOKEN, "refresh_token": "1//new-refresh",
                           "client_id": "cid", "client_secret": SECRET_CLIENT})


class _FakeFlow:
    seen: dict = {}

    @classmethod
    def from_client_secrets_file(cls, path, scopes):
        cls.seen = {"path": str(path), "scopes": list(scopes)}
        return cls()

    def run_local_server(self, **kwargs):
        _FakeFlow.seen["kwargs"] = kwargs
        print("Please visit this URL to authorize: https://accounts.google.com/o/oauth2/auth?x=1")
        return _FakeCreds()


def test_auth_google_saves_token_0600_and_prints_account(h, monkeypatch):
    from google_auth_oauthlib import flow

    monkeypatch.setattr(flow, "InstalledAppFlow", _FakeFlow)
    (h.secrets_dir / "google-token.json").unlink()
    r = h.run("auth", "google")
    assert r.rc == 0, r
    assert r.out.strip().splitlines() == ["signed in: google owner@example.test"]
    assert "https://accounts.google.com/o/oauth2/auth" in r.err  # the URL goes to stderr
    _no_secrets(r.out, r.err)
    seen = _FakeFlow.seen
    assert seen["path"].endswith("google-client.json")
    assert seen["scopes"] == ["https://www.googleapis.com/auth/drive.readonly"]
    assert seen["kwargs"]["host"] == "127.0.0.1"
    assert seen["kwargs"]["port"] == 0
    assert seen["kwargs"]["open_browser"] is False
    token = h.secrets_dir / "google-token.json"
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert json.loads(token.read_text())["refresh_token"] == "1//new-refresh"


def test_auth_google_without_client_file_exits_3(h, monkeypatch):
    from google_auth_oauthlib import flow

    monkeypatch.setattr(flow, "InstalledAppFlow", _FakeFlow)
    (h.secrets_dir / "google-client.json").unlink()
    r = h.run("auth", "google")
    assert r.rc == 3
    assert "google-client.json" in r.err
