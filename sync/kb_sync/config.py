"""Configuration for kb-sync (T022, FR-004; contracts/kb-cli.md "Configuration keys").

Settings come from the repo's `.env` (bind-mounted at /app/.env), read with python-dotenv.
Secrets come from the secrets directory only, one file each, and are never held on the
Config object, so `repr(cfg)` cannot leak them (data-model.md "Entity: Secrets").

Override paths for tests and host runs:
  KB_SYNC_ENV_FILE     default /app/.env
  KB_SYNC_SECRETS_DIR  default /run/secrets/kb-sync
  KB_SYNC_DATA_DIR     default /data (holds inputs/, kb-sync/, packs/, research/)
"""

from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
from pathlib import Path

import httpx
from dotenv import dotenv_values

from kb_sync import ConfigError

# Tests monkeypatch this with an httpx.MockTransport. Every HTTP client in the package
# builds httpx.Client(transport=HTTP_TRANSPORT, ...).
HTTP_TRANSPORT: httpx.BaseTransport | None = None

DEFAULT_ENV_FILE = "/app/.env"
DEFAULT_SECRETS_DIR = "/run/secrets/kb-sync"
DEFAULT_DATA_DIR = "/data"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
FILTERS = ("reranker", "local", "none")

# .env.example defaults for the optional keys.
DEFAULTS = {
    "KB_SYNC_DRIVE_FOLDER_IDS": "",
    "KB_SYNC_APPFLOWY_BASE_URL": "",
    "KB_SYNC_APPFLOWY_WORKSPACE_ID": "",
    "KB_SYNC_APPFLOWY_NOTES_DATABASES": "",
    "KB_SYNC_RESEARCH_DIR": "",
    "KB_SYNC_CLAUDE_MODEL": "claude-sonnet-5-5",
    "KB_SYNC_FILTER": "reranker",
    "KB_SYNC_LOCAL_TOP_N": "8",
    "KB_SYNC_REMOVAL_LIMIT": "10",
    "KB_SYNC_NOTEBOOKLM_OUTPUT_PATTERN": (
        "(briefing doc|study guide|deep dive|faq|timeline|infographic|"
        "video overview|audio overview)"
    ),
    "KB_SYNC_NOTEBOOKLM_ENABLED": "false",
    "KB_SYNC_NOTEBOOKLM_SCOPE": "",
    "KB_SYNC_NOTEBOOK_SOURCE_LIMIT": "50",
    "KB_SYNC_REVIEW_STALE_MONTHS": "12",
}
REQUIRED = ("PORT", "LLM_MODEL")


def log(msg):
    """One line to stderr. Never pass secret values."""
    print(msg, file=sys.stderr, flush=True)


@dataclasses.dataclass(frozen=True)
class Config:
    """Non-secret settings. Holds paths to secrets, never their contents."""

    lightrag_url: str
    ollama_url: str
    llm_model: str
    drive_folder_ids: list[str]
    appflowy_base_url: str
    appflowy_workspace_id: str
    appflowy_notes_databases: list[str]
    research_enabled: bool
    claude_model: str
    filter: str
    local_top_n: int
    removal_limit: int
    notebooklm_output_pattern: str
    notebooklm_enabled: bool
    notebooklm_scope: str
    notebook_source_limit: int
    review_stale_months: int
    data_dir: Path
    inputs_dir: Path
    records_dir: Path
    packs_dir: Path
    research_dir: Path
    secrets_dir: Path


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _int(values: dict, key: str) -> int:
    raw = values[key].strip()
    try:
        n = int(raw)
    except ValueError:
        raise ConfigError(key, f"expected a whole number, got {raw!r}") from None
    if n < 0:
        raise ConfigError(key, f"expected a whole number of 0 or more, got {raw!r}")
    return n


def _bool(values: dict, key: str) -> bool:
    raw = values[key].strip().lower()
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off", ""):
        return False
    raise ConfigError(key, f"expected true or false, got {raw!r}")


def env_file() -> Path:
    return Path(os.environ.get("KB_SYNC_ENV_FILE") or DEFAULT_ENV_FILE)


def load() -> Config:
    """Read .env and return the frozen Config. Raises ConfigError (exit 3) naming the key."""
    path = env_file()
    raw = dotenv_values(path) if path.is_file() else {}
    values = dict(DEFAULTS)
    for key, val in raw.items():
        if val is not None:
            values[key] = val

    for key in REQUIRED:
        if not str(values.get(key) or "").strip():
            raise ConfigError(key)

    port = _int(values, "PORT")
    if not 0 < port < 65536:
        raise ConfigError("PORT", f"expected a TCP port, got {port}")

    flt = values["KB_SYNC_FILTER"].strip()
    if flt not in FILTERS:
        raise ConfigError("KB_SYNC_FILTER", f"expected one of {'|'.join(FILTERS)}, got {flt!r}")

    data_dir = Path(os.environ.get("KB_SYNC_DATA_DIR") or DEFAULT_DATA_DIR)
    secrets_dir = Path(os.environ.get("KB_SYNC_SECRETS_DIR") or DEFAULT_SECRETS_DIR)
    ollama_url = (values.get("LLM_BINDING_HOST") or "").strip() or DEFAULT_OLLAMA_URL

    return Config(
        lightrag_url=f"http://127.0.0.1:{port}",
        ollama_url=ollama_url.rstrip("/"),
        llm_model=values["LLM_MODEL"].strip(),
        drive_folder_ids=_csv(values["KB_SYNC_DRIVE_FOLDER_IDS"]),
        appflowy_base_url=values["KB_SYNC_APPFLOWY_BASE_URL"].strip().rstrip("/"),
        appflowy_workspace_id=values["KB_SYNC_APPFLOWY_WORKSPACE_ID"].strip(),
        appflowy_notes_databases=_csv(values["KB_SYNC_APPFLOWY_NOTES_DATABASES"]),
        # The host path is mounted at <data_dir>/research; the key only switches the source on.
        research_enabled=bool(values["KB_SYNC_RESEARCH_DIR"].strip()),
        claude_model=values["KB_SYNC_CLAUDE_MODEL"].strip() or DEFAULTS["KB_SYNC_CLAUDE_MODEL"],
        filter=flt,
        local_top_n=_int(values, "KB_SYNC_LOCAL_TOP_N"),
        removal_limit=_int(values, "KB_SYNC_REMOVAL_LIMIT"),
        notebooklm_output_pattern=values["KB_SYNC_NOTEBOOKLM_OUTPUT_PATTERN"],
        notebooklm_enabled=_bool(values, "KB_SYNC_NOTEBOOKLM_ENABLED"),
        notebooklm_scope=values["KB_SYNC_NOTEBOOKLM_SCOPE"].strip(),
        notebook_source_limit=_int(values, "KB_SYNC_NOTEBOOK_SOURCE_LIMIT"),
        review_stale_months=_int(values, "KB_SYNC_REVIEW_STALE_MONTHS"),
        data_dir=data_dir,
        inputs_dir=data_dir / "inputs",
        records_dir=data_dir / "kb-sync",
        packs_dir=data_dir / "packs",
        research_dir=data_dir / "research",
        secrets_dir=secrets_dir,
    )


def _secret_path(cfg: Config, name: str) -> Path:
    if not name or "/" in name or name in (".", ".."):
        raise ValueError(f"bad secret name {name!r}")
    return cfg.secrets_dir / name


def read_secret(cfg: Config, name: str) -> str | None:
    """Return the secret file's text, or None when it is absent."""
    try:
        return _secret_path(cfg, name).read_text()
    except FileNotFoundError:
        return None


def write_secret(cfg: Config, name: str, text: str) -> Path:
    """Write a secret atomically with mode 0600."""
    path = _secret_path(cfg, name)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{name}.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return path


def google_scopes(cfg: Config) -> list[str]:
    """drive.readonly, plus the NotebookLM scope when NotebookLM is enabled and set (R8, R13)."""
    scopes = [DRIVE_SCOPE]
    if cfg.notebooklm_enabled and cfg.notebooklm_scope:
        scopes.append(cfg.notebooklm_scope)
    return scopes
