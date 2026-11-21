"""T035 — FR-014.

A file matching the NotebookLM output pattern gets source `notebook-output`. A text output
is stored and staged. An audio or video output is recorded but never staged.
"""

from __future__ import annotations

import pytest

from drive_fakes import Harness
from kb_sync import store


@pytest.fixture
def h(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def test_text_output_is_tagged_stored_and_staged(h):
    h.pull()
    rec = h.records()
    assert rec.get_item("drive", "nbtext") is None
    item = rec.get_item("notebook-output", "nbtext")
    assert item is not None and item.disposition == "ingest"
    path = h.store_file("notebook-output", "nbtext")
    meta, body = store.read_markdown(path)
    assert meta["source"] == "notebook-output"
    assert meta["title"] == "Leadership Briefing Doc"
    assert "NotebookLM briefing" in body

    h.plan()
    assert "notebook-output:nbtext" in [s["ref"] for s in h.plan_json()["stages"]]
    h.ingest()
    assert h.lightrag.doc_for("notebook-output__nbtext.md") is not None


def test_audio_output_is_recorded_never_staged(h):
    r = h.pull()
    item = h.records().get_item("notebook-output", "nbaudio")
    assert item is not None
    assert item.disposition == "record_only"
    assert item.url == "https://drive.google.com/file/d/nbaudio/view"
    assert "nbaudio" not in h.drive.downloads()
    assert not list(h.inputs_dir.rglob("nbaudio*"))
    assert "Leadership Audio Overview.wav" in r.err
    h.plan()
    assert "notebook-output:nbaudio" not in [s["ref"] for s in h.plan_json()["stages"]]


def test_pattern_match_is_case_insensitive_and_configurable(h):
    h.set_env(KB_SYNC_NOTEBOOKLM_OUTPUT_PATTERN="(weekly digest)")
    h.drive.files["doc1"]["name"] = "WEEKLY DIGEST 12"
    h.pull()
    rec = h.records()
    assert rec.get_item("notebook-output", "doc1") is not None
    # With a custom pattern the default names are ordinary Drive files.
    assert rec.get_item("drive", "nbtext") is not None


def test_video_output_added_later_is_record_only(h):
    h.pull()
    h.drive.add_file("nbvideo", "Hiring Video Overview.mp4", "video/mp4", ["hiring"], content=b"vid")
    h.pull()
    item = h.records().get_item("notebook-output", "nbvideo")
    assert item.disposition == "record_only"
    assert "nbvideo" not in h.drive.downloads()
