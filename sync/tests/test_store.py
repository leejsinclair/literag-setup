"""T014 - FR-007: store files.

Spec 002 FR-007 and contracts/store-formats.md -> "Store file" and "Locations":
- a store file sits at data/inputs/<source>/<source_id>.md
- its YAML front-matter keys are exactly title, source, url, modified, in that order
- it is written atomically (temp file, then rename)
- the staged name <source>__<source_id>.md maps back to its item

Written before kb_sync.store exists (test-first). Fixtures stay in this file until
sync/tests/conftest.py lands (T019).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from kb_sync import store

FRONT_MATTER_KEYS = ["title", "source", "url", "modified"]
SOURCES = ["drive", "appflowy", "notebook-output", "web", "research"]


@pytest.fixture
def inputs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "inputs"
    d.mkdir()
    return d


def _write(inputs_dir: Path, source: str = "drive", source_id: str = "1AbCdEf", **over) -> Path:
    fields = {
        "title": "Team topologies notes",
        "url": "https://docs.google.com/document/d/1AbCdEf/edit",
        "modified": "2026-11-02T09:14:00Z",
        "body": "# Team topologies notes\n\nStream-aligned teams own a flow of change.\n",
    }
    fields.update(over)
    return store.write_markdown(inputs_dir, source, source_id, **fields)


def _split_front_matter(text: str) -> tuple[str, str]:
    assert text.startswith("---\n"), "store file must open with YAML front-matter"
    end = text.index("\n---\n", 4)
    return text[4:end], text[end + len("\n---\n"):]


# --- Paths --------------------------------------------------------------------


@pytest.mark.parametrize("source", SOURCES)
def test_store_path_is_inputs_source_id_md(inputs_dir: Path, source: str) -> None:
    assert store.store_path(inputs_dir, source, "abc123") == inputs_dir / source / "abc123.md"


def test_store_path_honours_extension(inputs_dir: Path) -> None:
    assert store.store_path(inputs_dir, "drive", "1PdF", ext="pdf") == inputs_dir / "drive" / "1PdF.pdf"


def test_write_markdown_lands_at_the_store_path(inputs_dir: Path) -> None:
    path = _write(inputs_dir)
    assert path == inputs_dir / "drive" / "1AbCdEf.md"
    assert path.is_file()


def test_path_is_stable_when_title_and_url_change(inputs_dir: Path) -> None:
    first = _write(inputs_dir, title="Old title", url="https://example.org/old")
    second = _write(inputs_dir, title="Renamed upstream", url="https://example.org/new")
    assert first == second
    meta, _ = store.read_markdown(second)
    assert meta["title"] == "Renamed upstream"
    assert meta["url"] == "https://example.org/new"
    assert sorted(p.name for p in (inputs_dir / "drive").iterdir()) == ["1AbCdEf.md"]


# --- Front-matter -------------------------------------------------------------


def test_front_matter_keys_are_exactly_the_four_in_order(inputs_dir: Path) -> None:
    path = _write(inputs_dir)
    raw_meta, _ = _split_front_matter(path.read_text(encoding="utf-8"))
    meta = yaml.safe_load(raw_meta)
    assert list(meta.keys()) == FRONT_MATTER_KEYS


def test_front_matter_values_and_body_round_trip(inputs_dir: Path) -> None:
    body = "# Heading\n\nSome *converted* body.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    path = _write(inputs_dir, source="appflowy", source_id="9f2e-row", body=body)
    meta, read_body = store.read_markdown(path)
    assert meta == {
        "title": "Team topologies notes",
        "source": "appflowy",
        "url": "https://docs.google.com/document/d/1AbCdEf/edit",
        "modified": "2026-11-02T09:14:00Z",
    }
    assert read_body.strip() == body.strip()


def test_awkward_title_survives_yaml(inputs_dir: Path) -> None:
    title = 'Q3: "plans" & notes # not a comment --- really'
    path = _write(inputs_dir, title=title)
    raw_meta, _ = _split_front_matter(path.read_text(encoding="utf-8"))
    assert yaml.safe_load(raw_meta)["title"] == title
    meta, _ = store.read_markdown(path)
    assert meta["title"] == title
    assert list(meta.keys()) == FRONT_MATTER_KEYS


def test_modified_stays_an_iso_string(inputs_dir: Path) -> None:
    path = _write(inputs_dir)
    meta, _ = store.read_markdown(path)
    assert meta["modified"] == "2026-11-02T09:14:00Z"
    assert isinstance(meta["modified"], str)


def test_store_file_is_utf8(inputs_dir: Path) -> None:
    path = _write(inputs_dir, title="Café naïveté", body="Grüße — ünïcödé\n")
    text = path.read_bytes().decode("utf-8")
    assert "Café naïveté" in text
    assert "Grüße — ünïcödé" in text


# --- Atomic writes ------------------------------------------------------------


def _patch_renames(monkeypatch: pytest.MonkeyPatch, hook) -> None:
    real_replace, real_rename = os.replace, os.rename

    def replace(src, dst, *a, **kw):
        hook(Path(src), Path(dst))
        return real_replace(src, dst, *a, **kw)

    def rename(src, dst, *a, **kw):
        hook(Path(src), Path(dst))
        return real_rename(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(os, "rename", rename)


def test_write_goes_through_a_complete_temp_file_then_rename(
    inputs_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _write(inputs_dir, body="old body\n")
    old_text = old.read_text(encoding="utf-8")
    seen: list[tuple[Path, Path]] = []

    def hook(src: Path, dst: Path) -> None:
        if dst == old:
            # At the moment of the rename the target still holds the old content and
            # the temp file already holds the complete new content.
            assert old.read_text(encoding="utf-8") == old_text
            assert "new body" in src.read_text(encoding="utf-8")
        seen.append((src, dst))

    _patch_renames(monkeypatch, hook)
    _write(inputs_dir, body="new body\n")
    assert any(dst == old for _, dst in seen), "store file must be put in place by a rename"
    assert "new body" in old.read_text(encoding="utf-8")


def test_failed_write_leaves_the_old_file_intact(
    inputs_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _write(inputs_dir, body="old body\n")
    old_text = old.read_text(encoding="utf-8")

    def hook(src: Path, dst: Path) -> None:
        raise OSError("simulated crash before rename")

    _patch_renames(monkeypatch, hook)
    with pytest.raises(OSError):
        _write(inputs_dir, body="new body\n")
    assert old.read_text(encoding="utf-8") == old_text


def test_failed_first_write_leaves_no_store_file(
    inputs_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def hook(src: Path, dst: Path) -> None:
        raise OSError("simulated crash before rename")

    _patch_renames(monkeypatch, hook)
    with pytest.raises(OSError):
        _write(inputs_dir, source_id="never")
    assert not store.store_path(inputs_dir, "drive", "never").exists()


def test_successful_write_leaves_no_temp_files(inputs_dir: Path) -> None:
    _write(inputs_dir)
    _write(inputs_dir, body="rewritten\n")
    assert [p.name for p in (inputs_dir / "drive").iterdir()] == ["1AbCdEf.md"]


def test_write_binary_is_atomic_and_at_store_path(
    inputs_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nfake\n%%EOF\n"
    renamed: list[Path] = []
    _patch_renames(monkeypatch, lambda src, dst: renamed.append(dst))
    path = store.write_binary(inputs_dir, "drive", "1PdF", "pdf", data)
    assert path == inputs_dir / "drive" / "1PdF.pdf"
    assert path.read_bytes() == data
    assert path in renamed
    assert [p.name for p in (inputs_dir / "drive").iterdir()] == ["1PdF.pdf"]


# --- Staged names -------------------------------------------------------------


def test_staged_name_format() -> None:
    assert store.staged_name("drive", "1AbCdEf") == "drive__1AbCdEf.md"
    assert store.staged_name("drive", "1PdF", ext="pdf") == "drive__1PdF.pdf"


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("source_id", ["1AbC-dE_f", "9f2e4c1a-0b7d", "notes-2026-q3"])
def test_staged_name_round_trips(source: str, source_id: str) -> None:
    for ext in ("md", "pdf"):
        name = store.staged_name(source, source_id, ext=ext)
        assert store.parse_staged_name(name) == (source, source_id, ext)


@pytest.mark.parametrize(
    "bad",
    [
        "no-separator.md",
        "drive__.md",
        "__1AbC.md",
        "drive__1AbC",
        "drive__a__b.md",
        "",
    ],
)
def test_parse_staged_name_rejects_bad_names(bad: str) -> None:
    with pytest.raises(ValueError):
        store.parse_staged_name(bad)


def test_stage_copies_to_flat_staged_name(inputs_dir: Path) -> None:
    path = _write(inputs_dir)
    staged = store.stage(inputs_dir, path)
    assert staged == inputs_dir / "drive__1AbCdEf.md"
    assert staged.read_bytes() == path.read_bytes()
    assert path.is_file(), "staging copies; the store file stays put"


def test_staged_name_maps_back_to_its_item(inputs_dir: Path, tmp_path: Path) -> None:
    from kb_sync.records import Records

    path = _write(inputs_dir, source="appflowy", source_id="9f2e-row")
    staged = store.stage(inputs_dir, path)

    records = Records(tmp_path / "kb-sync.db")
    item = records.upsert_item(
        "appflowy",
        "9f2e-row",
        title="Team topologies notes",
        url="https://docs.google.com/document/d/1AbCdEf/edit",
        modified="2026-11-02T09:14:00Z",
        store_path=str(path),
        staged_name=staged.name,
        status="active",
    )

    source, source_id, _ = store.parse_staged_name(staged.name)
    assert (source, source_id) == ("appflowy", "9f2e-row")
    found = records.get_by_staged_name(staged.name)
    assert found is not None
    assert found.id == item.id
    assert (found.source, found.source_id) == ("appflowy", "9f2e-row")


# --- Removal ------------------------------------------------------------------


def test_move_to_removed_moves_not_deletes(inputs_dir: Path) -> None:
    path = _write(inputs_dir)
    content = path.read_bytes()
    moved = store.move_to_removed(path)
    assert moved == inputs_dir / "drive" / ".removed" / "1AbCdEf.md"
    assert moved.read_bytes() == content
    assert not path.exists()
