"""T015 — FR-002 and FR-010: the LightRAG REST client.

- The client never sends ``DELETE /documents`` without a path (that wipes the store).
- ``DELETE /documents/delete_document`` retries on ``busy`` and returns only once
  ``pipeline_status`` is idle.
- Scan polling stops at a terminal state (``processed`` or ``failed``).
- ``find_doc`` matches on the basename ``file_path``.

Test-first (tasks.md Phase 2, Wave 1): written before ``kb_sync.lightrag`` exists.
Modules are imported inside each test, so this file collects cleanly and every
test fails with ``ModuleNotFoundError`` until T025 lands.

Surfaces: kb-sync interfaces pin sheet → "LightRAG client"; contracts/lightrag-client.md.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import httpx
import pytest

BASE = "http://127.0.0.1:9621"
SYNC_ROOT = Path(__file__).resolve().parents[1]


# --- fake LightRAG (kept local: conftest.py arrives with T019) --------------


class FakeLightRAG:
    """Scripted v1.5.6 responses keyed by (method, path). Each route pops from a queue;
    the last entry repeats. Every request is recorded."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], list] = {}

    def on(self, method, path, *responses):
        self.routes[(method, path)] = list(responses)
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        # Hard guard: a bare collection delete is never acceptable, whatever the test.
        assert key != ("DELETE", "/documents"), "client sent DELETE /documents (wipes the KB)"
        if key not in self.routes:
            return httpx.Response(404, json={"detail": "Not Found"})
        queue = self.routes[key]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(item):
            item = item(request)
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(200, json=item)

    def calls(self, method, path):
        return [r for r in self.requests if r.method == method and r.url.path == path]

    def sequence(self):
        return [(r.method, r.url.path) for r in self.requests]


class Sleeps:
    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds):
        self.calls.append(seconds)


@pytest.fixture
def fake(monkeypatch):
    config = importlib.import_module("kb_sync.config")
    server = FakeLightRAG()
    monkeypatch.setattr(config, "HTTP_TRANSPORT", httpx.MockTransport(server))
    yield server
    # Whatever the test did, no request ever hit the bare collection.
    assert ("DELETE", "/documents") not in server.sequence()


@pytest.fixture
def sleeps():
    return Sleeps()


@pytest.fixture
def client(fake, sleeps):
    mod = importlib.import_module("kb_sync.lightrag")
    return mod.LightRAG(BASE, sleep=sleeps, poll_interval=0.01, timeout=60)


def _pkg():
    return importlib.import_module("kb_sync")


def _body(request: httpx.Request):
    return json.loads(request.content.decode() or "null")


def _doc(doc_id, file_path, status="processed"):
    return {
        "id": doc_id,
        "content_summary": "summary",
        "content_length": 100,
        "status": status,
        "created_at": "2026-11-21T10:00:00+00:00",
        "updated_at": "2026-11-21T10:01:00+00:00",
        "track_id": "scan_20261121_100000_abcd1234",
        "chunks_count": 1,
        "error_msg": None,
        "metadata": {},
        "file_path": file_path,
    }


IDLE = {"busy": False, "job_name": "Default Job", "docs": 0, "batchs": 0, "cur_batch": 0,
        "request_pending": False, "latest_message": "", "history_messages": []}
BUSY = dict(IDLE, busy=True, job_name="deleting 1 document")


# --- health and server down ----------------------------------------------------


def test_health_returns_body(fake, client):
    fake.on("GET", "/health", {"status": "healthy", "core_version": "v1.5.6"})
    assert client.health()["status"] == "healthy"


def test_connection_error_raises_server_down(monkeypatch):
    config = importlib.import_module("kb_sync.config")
    mod = importlib.import_module("kb_sync.lightrag")

    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(config, "HTTP_TRANSPORT", httpx.MockTransport(refuse))
    c = mod.LightRAG(BASE, sleep=Sleeps(), poll_interval=0.01, timeout=60)
    with pytest.raises(_pkg().ServerDown) as exc:
        c.health()
    assert exc.value.exit_code == 3
    assert f"LightRAG server is not running at {BASE}" in str(exc.value)
    assert "./scripts/start.sh" in str(exc.value)


# --- scan and track polling ----------------------------------------------------


def test_scan_posts_to_documents_scan_and_returns_track_id(fake, client):
    fake.on("POST", "/documents/scan",
            {"status": "scanning_started", "message": "Scanning process has been initiated",
             "track_id": "scan_20261121_100000_abcd1234"})
    assert client.scan() == "scan_20261121_100000_abcd1234"
    assert len(fake.calls("POST", "/documents/scan")) == 1


def _track(track_id, *docs):
    summary: dict[str, int] = {}
    for d in docs:
        summary[d["status"]] = summary.get(d["status"], 0) + 1
    return {"track_id": track_id, "documents": list(docs), "total_count": len(docs),
            "status_summary": summary}


def test_wait_track_polls_until_every_document_is_terminal(fake, client, sleeps):
    tid = "scan_1"
    path = f"/documents/track_status/{tid}"
    fake.on("GET", path,
            _track(tid, _doc("doc-a", "drive__a.md", "pending"), _doc("doc-b", "drive__b.md", "pending")),
            _track(tid, _doc("doc-a", "drive__a.md", "processing"), _doc("doc-b", "drive__b.md", "pending")),
            _track(tid, _doc("doc-a", "drive__a.md", "processed"), _doc("doc-b", "drive__b.md", "failed")),
            # Anything after the terminal state must never be fetched.
            httpx.Response(500, json={"detail": "polled past terminal state"}))
    docs = client.wait_track(tid)
    assert len(fake.calls("GET", path)) == 3
    assert {d["id"]: d["status"] for d in docs} == {"doc-a": "processed", "doc-b": "failed"}
    assert len(sleeps.calls) >= 2  # waited between polls, through the injected sleep


def test_wait_track_returns_at_once_when_already_terminal(fake, client, sleeps):
    tid = "scan_2"
    path = f"/documents/track_status/{tid}"
    fake.on("GET", path, _track(tid, _doc("doc-a", "drive__a.md", "processed")),
            httpx.Response(500, json={"detail": "polled past terminal state"}))
    docs = client.wait_track(tid)
    assert [d["id"] for d in docs] == ["doc-a"]
    assert len(fake.calls("GET", path)) == 1


def test_wait_track_treats_failed_as_terminal(fake, client):
    tid = "scan_3"
    path = f"/documents/track_status/{tid}"
    fake.on("GET", path, _track(tid, _doc("doc-x", "drive__x.md", "failed")),
            httpx.Response(500, json={"detail": "polled past terminal state"}))
    docs = client.wait_track(tid)
    assert docs[0]["status"] == "failed"
    assert len(fake.calls("GET", path)) == 1


# --- find_doc ------------------------------------------------------------------


def _page(docs, page, total_pages):
    return {"documents": docs,
            "pagination": {"page": page, "page_size": 2, "total_count": 2 * total_pages,
                           "total_pages": total_pages, "has_next": page < total_pages,
                           "has_prev": page > 1},
            "status_counts": {"processed": 2 * total_pages}}


def test_find_doc_matches_basename_file_path(fake, client):
    fake.on("POST", "/documents/paginated", _page(
        [_doc("doc-1", "drive__abc.md"), _doc("doc-2", "drive__abcdef.md")], 1, 1))
    found = client.find_doc("drive__abcdef.md")
    assert found is not None and found["id"] == "doc-2"


def test_find_doc_accepts_a_full_path_and_matches_on_basename(fake, client):
    fake.on("POST", "/documents/paginated", _page([_doc("doc-1", "drive__abc.md")], 1, 1))
    found = client.find_doc("/data/inputs/drive__abc.md")
    assert found is not None and found["id"] == "doc-1"


def test_find_doc_matches_a_server_side_path_by_basename(fake, client):
    fake.on("POST", "/documents/paginated",
            _page([_doc("doc-1", "/app/data/inputs/drive__abc.md")], 1, 1))
    found = client.find_doc("drive__abc.md")
    assert found is not None and found["id"] == "doc-1"


def test_find_doc_does_not_match_a_prefix_or_substring(fake, client):
    fake.on("POST", "/documents/paginated", _page(
        [_doc("doc-1", "drive__abc.md.bak"), _doc("doc-2", "xdrive__abc.md")], 1, 1))
    assert client.find_doc("drive__abc.md") is None


def test_find_doc_walks_every_page(fake, client):
    def respond(request):
        page = (_body(request) or {}).get("page", 1)
        if page == 1:
            return _page([_doc("doc-1", "drive__a.md"), _doc("doc-2", "drive__b.md")], 1, 2)
        return _page([_doc("doc-3", "drive__c.md"), _doc("doc-4", "drive__target.md")], 2, 2)

    fake.on("POST", "/documents/paginated", respond)
    found = client.find_doc("drive__target.md")
    assert found is not None and found["id"] == "doc-4"
    assert len(fake.calls("POST", "/documents/paginated")) == 2


def test_find_doc_returns_none_when_absent(fake, client):
    fake.on("POST", "/documents/paginated", _page([], 1, 1))
    assert client.find_doc("drive__missing.md") is None


# --- delete_document -----------------------------------------------------------

DELETE_PATH = "/documents/delete_document"
STATUS_PATH = "/documents/pipeline_status"
EXPECTED_DELETE_BODY = {"doc_ids": ["doc-42"], "delete_file": True, "delete_llm_cache": False}


def _del(status):
    return {"status": status, "message": status, "doc_id": "doc-42"}


def test_delete_sends_exact_body_to_delete_document(fake, client):
    fake.on("DELETE", DELETE_PATH, _del("deletion_started"))
    fake.on("GET", STATUS_PATH, IDLE)
    client.delete("doc-42")
    calls = fake.calls("DELETE", DELETE_PATH)
    assert len(calls) == 1
    assert _body(calls[0]) == EXPECTED_DELETE_BODY


def test_delete_retries_on_busy_with_backoff(fake, client, sleeps):
    fake.on("DELETE", DELETE_PATH, _del("busy"), _del("busy"), _del("deletion_started"))
    fake.on("GET", STATUS_PATH, IDLE)
    client.delete("doc-42")
    calls = fake.calls("DELETE", DELETE_PATH)
    assert len(calls) == 3
    assert all(_body(c) == EXPECTED_DELETE_BODY for c in calls)
    assert len(sleeps.calls) >= 2
    assert all(s > 0 for s in sleeps.calls)


def test_delete_gives_up_after_five_busy_answers(fake, client):
    fake.on("DELETE", DELETE_PATH, _del("busy"))
    fake.on("GET", STATUS_PATH, IDLE)
    with pytest.raises(_pkg().KbSyncError):
        client.delete("doc-42")
    assert len(fake.calls("DELETE", DELETE_PATH)) == 5


def test_delete_raises_on_not_allowed(fake, client):
    fake.on("DELETE", DELETE_PATH, _del("not_allowed"))
    fake.on("GET", STATUS_PATH, IDLE)
    with pytest.raises(_pkg().KbSyncError):
        client.delete("doc-42")
    assert len(fake.calls("DELETE", DELETE_PATH)) == 1


def test_delete_returns_only_once_pipeline_is_idle(fake, client, sleeps):
    fake.on("DELETE", DELETE_PATH, _del("deletion_started"))
    fake.on("GET", STATUS_PATH, BUSY, BUSY, IDLE,
            httpx.Response(500, json={"detail": "polled past idle"}))
    client.delete("doc-42")
    seq = fake.sequence()
    first_delete = seq.index(("DELETE", DELETE_PATH))
    after = seq[first_delete + 1:]
    assert after == [("GET", STATUS_PATH)] * 3, "must poll pipeline_status until idle, then stop"
    assert len(sleeps.calls) >= 2


def test_wait_idle_polls_until_busy_is_false(fake, client):
    fake.on("GET", STATUS_PATH, BUSY, IDLE,
            httpx.Response(500, json={"detail": "polled past idle"}))
    client.wait_idle()
    assert len(fake.calls("GET", STATUS_PATH)) == 2


# --- query ---------------------------------------------------------------------


@pytest.mark.parametrize("rerank", [True, False])
def test_query_requests_context_and_references(fake, client, rerank):
    fake.on("POST", "/query", {"response": "-----Document Chunks-----", "references": []})
    result = client.query("What is the plan?", enable_rerank=rerank)
    assert "references" in result
    body = _body(fake.calls("POST", "/query")[0])
    assert body["query"] == "What is the plan?"
    assert body["mode"] == "mix"
    assert body["only_need_context"] is True
    assert body["include_references"] is True
    assert body["enable_rerank"] is rerank


# --- the forbidden bare-collection delete ---------------------------------------


def test_client_exposes_no_store_wiping_method(fake):
    mod = importlib.import_module("kb_sync.lightrag")
    names = [n for n in dir(mod.LightRAG) if not n.startswith("__")]
    forbidden = [n for n in names if re.search(r"clear|wipe|purge|drop|delete_all|reset", n, re.I)]
    assert forbidden == [], f"store-wiping method(s) on LightRAG: {forbidden}"


def test_no_code_path_builds_a_bare_documents_delete(fake):
    """Static guard over the whole package: no request targets the bare `/documents`
    collection. Every allowed route has a suffix (contracts/lightrag-client.md)."""
    importlib.import_module("kb_sync.lightrag")  # the package must exist for this to mean anything
    pkg_dir = SYNC_ROOT / "kb_sync"
    bare = re.compile(r"""["'](?:[^"'\s]*?)/documents/?["']""")
    offenders = []
    for path in sorted(pkg_dir.rglob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if bare.search(line):
                offenders.append(f"{path.relative_to(SYNC_ROOT)}:{lineno}: {line.strip()}")
    assert offenders == [], "bare /documents route referenced:\n" + "\n".join(offenders)
