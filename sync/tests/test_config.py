"""T012 — FR-004: configuration from .env, secrets from secrets/.

Secrets load from the secrets directory and never appear in log output,
exception text, ``repr(cfg)`` or ``--help``. A missing required ``.env`` key
exits 3 and names the key.

Test-first (tasks.md Phase 2, Wave 1): written before ``kb_sync.config`` exists.
Modules are imported inside each test, so this file collects cleanly and every
test fails with ``ModuleNotFoundError`` until T022 lands.

Surfaces: kb-sync interfaces pin sheet → "Package layout and error types" and
"Paths and configuration"; contracts/kb-cli.md → "Configuration keys";
data-model.md → "Entity: Secrets".
"""

from __future__ import annotations

import importlib
import json
import logging
import stat

import httpx
import pytest

SECRET_REFRESH = "1//SECRET-refresh-token-do-not-leak-7f3a9c"
SECRET_CLIENT = "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leak-91bd"
SECRET_APPFLOWY = "SECRET-appflowy-refresh-do-not-leak-55e2"
SECRET_ANTHROPIC = "sk-ant-SECRET-api-key-do-not-leak-0c4d"
ALL_SECRETS = (SECRET_REFRESH, SECRET_CLIENT, SECRET_APPFLOWY, SECRET_ANTHROPIC)

BASE_ENV = {
    "HOST": "127.0.0.1",
    "PORT": "9621",
    "LLM_MODEL": "qwen2.5:3b-instruct",
    "KB_SYNC_DRIVE_FOLDER_IDS": "folderA,folderB",
    "KB_SYNC_APPFLOWY_BASE_URL": "https://appflowy.example.test",
    "KB_SYNC_APPFLOWY_WORKSPACE_ID": "ws-1",
    "KB_SYNC_APPFLOWY_NOTES_DATABASES": "db1,db2",
    "KB_SYNC_RESEARCH_DIR": "",
    "KB_SYNC_CLAUDE_MODEL": "claude-sonnet-5-5",
    "KB_SYNC_FILTER": "reranker",
    "KB_SYNC_LOCAL_TOP_N": "8",
    "KB_SYNC_REMOVAL_LIMIT": "10",
    "KB_SYNC_NOTEBOOKLM_ENABLED": "false",
    "KB_SYNC_NOTEBOOK_SOURCE_LIMIT": "50",
    "KB_SYNC_REVIEW_STALE_MONTHS": "12",
}

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
NOTEBOOKLM_SCOPE = "https://www.googleapis.com/auth/notebooklm.example"


# --- fixtures (kept local: conftest.py arrives with T019) -------------------


def _write_env(path, values, comments=True):
    lines = []
    for key, val in values.items():
        if comments:
            # Every comment on its own line, as the repo's two-parser rule demands.
            lines.append(f"# {key}")
        lines.append(f"{key}={val}")
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fake /app/.env, secrets dir and data root, wired through the override vars."""
    env_file = tmp_path / "app.env"
    secrets_dir = tmp_path / "secrets"
    data_dir = tmp_path / "data"
    secrets_dir.mkdir(mode=0o700)
    for sub in ("inputs", "kb-sync", "packs", "research"):
        (data_dir / sub).mkdir(parents=True)
    _write_env(env_file, BASE_ENV)

    monkeypatch.setenv("KB_SYNC_ENV_FILE", str(env_file))
    monkeypatch.setenv("KB_SYNC_SECRETS_DIR", str(secrets_dir))
    monkeypatch.setenv("KB_SYNC_DATA_DIR", str(data_dir))
    # The process environment must not mask a key missing from the file.
    for key in BASE_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("KB_SYNC_NOTEBOOKLM_SCOPE", raising=False)

    class Env:
        pass

    e = Env()
    e.env_file, e.secrets_dir, e.data_dir = env_file, secrets_dir, data_dir

    def rewrite(**changes):
        values = dict(BASE_ENV)
        for key, val in changes.items():
            if val is None:
                values.pop(key, None)
            else:
                values[key] = val
        _write_env(env_file, values)

    e.rewrite = rewrite
    return e


def _secret_files(secrets_dir):
    files = {
        "google-client.json": json.dumps(
            {"installed": {"client_id": "cid.apps.example", "client_secret": SECRET_CLIENT}}
        ),
        "google-token.json": json.dumps(
            {"refresh_token": SECRET_REFRESH, "token": SECRET_REFRESH + "-access"}
        ),
        "appflowy.json": json.dumps({"refresh_token": SECRET_APPFLOWY}),
        "anthropic-key": SECRET_ANTHROPIC,
    }
    for name, text in files.items():
        p = secrets_dir / name
        p.write_text(text)
        p.chmod(0o600)
    return files


def _config():
    return importlib.import_module("kb_sync.config")


def _pkg():
    return importlib.import_module("kb_sync")


def _cli():
    return importlib.import_module("kb_sync.cli")


# --- loading .env ------------------------------------------------------------


def test_load_reads_env_file_and_derives_paths(env):
    config = _config()
    cfg = config.load()
    assert cfg.lightrag_url == "http://127.0.0.1:9621"
    assert cfg.ollama_url == "http://127.0.0.1:11434"
    assert cfg.llm_model == "qwen2.5:3b-instruct"
    assert cfg.drive_folder_ids == ["folderA", "folderB"]
    assert cfg.appflowy_workspace_id == "ws-1"
    assert cfg.appflowy_notes_databases == ["db1", "db2"]
    assert cfg.filter == "reranker"
    assert cfg.local_top_n == 8
    assert cfg.removal_limit == 10
    assert cfg.ingest_timeout == 43200
    assert cfg.notebook_source_limit == 50
    assert cfg.review_stale_months == 12
    assert cfg.notebooklm_enabled is False
    assert str(cfg.data_dir) == str(env.data_dir)
    assert str(cfg.inputs_dir) == str(env.data_dir / "inputs")
    assert str(cfg.records_dir) == str(env.data_dir / "kb-sync")
    assert str(cfg.packs_dir) == str(env.data_dir / "packs")
    assert str(cfg.secrets_dir) == str(env.secrets_dir)


def test_config_is_frozen(env):
    import dataclasses

    cfg = _config().load()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.llm_model = "other"  # type: ignore[misc]


def test_optional_keys_take_env_example_defaults(env):
    env.rewrite(
        KB_SYNC_FILTER=None,
        KB_SYNC_LOCAL_TOP_N=None,
        KB_SYNC_REMOVAL_LIMIT=None,
        KB_SYNC_INGEST_TIMEOUT=None,
        KB_SYNC_NOTEBOOK_SOURCE_LIMIT=None,
        KB_SYNC_REVIEW_STALE_MONTHS=None,
        KB_SYNC_CLAUDE_MODEL=None,
        KB_SYNC_NOTEBOOKLM_ENABLED=None,
    )
    cfg = _config().load()
    assert cfg.filter == "reranker"
    assert cfg.local_top_n == 8
    assert cfg.removal_limit == 10
    assert cfg.ingest_timeout == 43200
    assert cfg.notebook_source_limit == 50
    assert cfg.review_stale_months == 12
    assert cfg.claude_model == "claude-sonnet-5-5"
    assert cfg.notebooklm_enabled is False


@pytest.mark.parametrize("key", ["PORT", "LLM_MODEL"])
def test_missing_required_key_raises_config_error_exit_3_naming_key(env, key):
    env.rewrite(**{key: None})
    pkg, config = _pkg(), _config()
    with pytest.raises(pkg.ConfigError) as exc:
        config.load()
    assert exc.value.exit_code == 3
    assert key in str(exc.value)
    assert f"missing .env key {key}" in str(exc.value)


@pytest.mark.parametrize(
    "key,value",
    [
        ("KB_SYNC_FILTER", "bogus"),
        ("KB_SYNC_LOCAL_TOP_N", "eight"),
        ("KB_SYNC_REMOVAL_LIMIT", "ten"),
        ("KB_SYNC_NOTEBOOK_SOURCE_LIMIT", "1.5"),
        ("KB_SYNC_INGEST_TIMEOUT", "0"),
        ("KB_SYNC_INGEST_TIMEOUT", "12h"),
    ],
)
def test_invalid_value_raises_config_error_naming_key(env, key, value):
    env.rewrite(**{key: value})
    pkg, config = _pkg(), _config()
    with pytest.raises(pkg.ConfigError) as exc:
        config.load()
    assert exc.value.exit_code == 3
    assert f"invalid .env key {key}" in str(exc.value)


def test_cli_missing_required_key_exits_3_and_names_key(env, capsys):
    env.rewrite(PORT=None)
    rc = _cli().main(["pull"])
    out, err = capsys.readouterr()
    assert rc == 3
    assert "PORT" in err
    assert err.startswith("ERROR: ") or "\nERROR: " in err
    assert out == ""  # stdout carries results only


# --- secrets -----------------------------------------------------------------


def test_read_secret_returns_text_or_none(env):
    files = _secret_files(env.secrets_dir)
    config = _config()
    cfg = config.load()
    assert config.read_secret(cfg, "anthropic-key") == files["anthropic-key"]
    assert config.read_secret(cfg, "google-token.json") == files["google-token.json"]
    assert config.read_secret(cfg, "does-not-exist.json") is None


def test_write_secret_is_owner_only(env):
    config = _config()
    cfg = config.load()
    config.write_secret(cfg, "appflowy.json", json.dumps({"refresh_token": SECRET_APPFLOWY}))
    path = env.secrets_dir / "appflowy.json"
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert config.read_secret(cfg, "appflowy.json") is not None


def test_repr_and_str_of_config_hold_no_secret(env):
    _secret_files(env.secrets_dir)
    config = _config()
    cfg = config.load()
    # Touch every secret so a loader that caches them on cfg is caught too.
    for name in ("google-client.json", "google-token.json", "appflowy.json", "anthropic-key"):
        config.read_secret(cfg, name)
    text = repr(cfg) + str(cfg)
    for secret in ALL_SECRETS:
        assert secret not in text


def test_secrets_are_not_read_from_env_file(env):
    # A secret pasted into .env must not become part of the config surface.
    values = dict(BASE_ENV)
    values["ANTHROPIC_API_KEY"] = SECRET_ANTHROPIC
    _write_env(env.env_file, values)
    cfg = _config().load()
    assert SECRET_ANTHROPIC not in repr(cfg)


def test_exception_text_holds_no_secret(env):
    _secret_files(env.secrets_dir)
    pkg = _pkg()
    errors = [
        pkg.AuthExpired("google", "google"),
        pkg.ServerDown("http://127.0.0.1:9621"),
        pkg.ConfigError("PORT"),
    ]
    for e in errors:
        assert e.exit_code == 3
        for secret in ALL_SECRETS:
            assert secret not in str(e)
            assert secret not in repr(e)
    assert "scripts/kb.sh auth google" in str(errors[0])
    assert "./scripts/start.sh" in str(errors[1])


def test_help_output_holds_no_secret(env, capsys):
    _secret_files(env.secrets_dir)
    cli = _cli()
    try:
        rc = cli.main(["--help"])
    except SystemExit as e:  # argparse exits after printing help
        rc = e.code
    out, err = capsys.readouterr()
    assert rc in (0, None)
    assert out.strip(), "--help printed nothing"
    for secret in ALL_SECRETS:
        assert secret not in out
        assert secret not in err


def test_failed_run_logs_hold_no_secret(env, monkeypatch, capsys, caplog):
    """A pull whose every upstream call is refused must not echo a credential."""
    _secret_files(env.secrets_dir)
    config = _config()
    seen = []

    def refuse(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(401, json={"error": "invalid_grant"})

    monkeypatch.setattr(config, "HTTP_TRANSPORT", httpx.MockTransport(refuse))
    caplog.set_level(logging.DEBUG)
    rc = _cli().main(["pull"])
    out, err = capsys.readouterr()
    assert rc in (1, 3)
    logged = out + err + caplog.text
    for secret in ALL_SECRETS:
        assert secret not in logged
    records = env.data_dir / "kb-sync"
    for path in records.rglob("*"):
        if path.is_file():
            blob = path.read_bytes()
            for secret in ALL_SECRETS:
                assert secret.encode() not in blob, f"secret leaked into {path}"


def test_log_writes_one_line_to_stderr(env, capsys):
    _config().log("hello from kb-sync")
    out, err = capsys.readouterr()
    assert out == ""
    assert err == "hello from kb-sync\n"


# --- Google scopes -------------------------------------------------------------


def test_google_scopes_default_is_drive_readonly_only(env):
    config = _config()
    assert config.google_scopes(config.load()) == [DRIVE_SCOPE]


def test_google_scopes_ignore_notebooklm_scope_when_disabled(env):
    env.rewrite(KB_SYNC_NOTEBOOKLM_ENABLED="false", KB_SYNC_NOTEBOOKLM_SCOPE=NOTEBOOKLM_SCOPE)
    config = _config()
    assert config.google_scopes(config.load()) == [DRIVE_SCOPE]


def test_google_scopes_add_notebooklm_scope_when_enabled(env):
    env.rewrite(KB_SYNC_NOTEBOOKLM_ENABLED="true", KB_SYNC_NOTEBOOKLM_SCOPE=NOTEBOOKLM_SCOPE)
    config = _config()
    assert config.google_scopes(config.load()) == [DRIVE_SCOPE, NOTEBOOKLM_SCOPE]


def test_google_scopes_enabled_without_scope_stays_drive_only(env):
    env.rewrite(KB_SYNC_NOTEBOOKLM_ENABLED="true")
    config = _config()
    assert config.google_scopes(config.load()) == [DRIVE_SCOPE]
