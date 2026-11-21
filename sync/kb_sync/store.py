"""Store files (spec 002 FR-007, contracts/store-formats.md -> "Store file", "Locations").

A store file sits at `inputs/<source>/<source_id>.<ext>`. Markdown store files carry YAML
front-matter with exactly `title, source, url, modified`, in that order. Every write is
atomic: the content goes to a temp file in the same directory, then `os.replace` puts it in
place, so a crash leaves either the old file or the new one, never half of either.

The staged copy LightRAG scans is the flat `inputs/<source>__<source_id>.<ext>`.
A removed item's store file is moved (never deleted) to `inputs/<source>/.removed/`.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

import yaml

FRONT_MATTER_KEYS = ("title", "source", "url", "modified")
SEP = "__"
_EXT_RE = re.compile(r"^[A-Za-z0-9]+$")


def store_path(inputs_dir: Path, source: str, source_id: str, ext: str = "md") -> Path:
    """`inputs/<source>/<source_id>.<ext>`: stable for the item's life."""
    return Path(inputs_dir) / source / f"{source_id}.{ext}"


def staged_name(source: str, source_id: str, ext: str = "md") -> str:
    """`<source>__<source_id>.<ext>`: the flat name LightRAG scans."""
    return f"{source}{SEP}{source_id}.{ext}"


def parse_staged_name(name: str) -> tuple[str, str, str]:
    """Inverse of `staged_name`. Raises ValueError on a name it did not produce."""
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem or not _EXT_RE.match(ext):
        raise ValueError(f"not a staged name: {name!r}")
    parts = stem.split(SEP)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"not a staged name: {name!r}")
    return parts[0], parts[1], ext


def _atomic_write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
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


def _front_matter(meta: dict) -> str:
    return yaml.safe_dump(
        meta, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1_000_000
    )


def write_markdown(
    inputs_dir: Path,
    source: str,
    source_id: str,
    *,
    title: str,
    url: str,
    modified: str,
    body: str,
) -> Path:
    """Write a Markdown store file with the four front-matter keys, atomically."""
    meta = {"title": str(title), "source": str(source), "url": str(url), "modified": str(modified)}
    text = f"---\n{_front_matter(meta)}---\n\n{body}"
    if not text.endswith("\n"):
        text += "\n"
    return _atomic_write(store_path(inputs_dir, source, source_id), text.encode("utf-8"))


def write_binary(inputs_dir: Path, source: str, source_id: str, ext: str, data: bytes) -> Path:
    """Write a binary store file (a PDF) atomically."""
    return _atomic_write(store_path(inputs_dir, source, source_id, ext=ext), bytes(data))


def read_markdown(path: Path) -> tuple[dict, str]:
    """Return (front-matter dict, body). A file without front-matter gives ({}, text)."""
    text = Path(path).read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 3)
    if end == -1:
        return {}, text
    meta = yaml.safe_load(text[4:end + 1]) or {}
    body = text[end + len("\n---\n"):]
    if body.startswith("\n"):
        body = body[1:]
    return meta, body


def stage(inputs_dir: Path, path: Path) -> Path:
    """Copy a store file to its flat staged name in `inputs/`; the store file stays put."""
    path = Path(path)
    source = path.parent.name
    source_id, ext = path.stem, path.suffix.lstrip(".")
    target = Path(inputs_dir) / staged_name(source, source_id, ext=ext)
    _atomic_write(target, path.read_bytes())
    shutil.copystat(path, target)
    return target


def move_to_removed(path: Path) -> Path:
    """Move a store file to `inputs/<source>/.removed/<name>`. Never deletes."""
    path = Path(path)
    target = path.parent / ".removed" / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, target)
    return target
