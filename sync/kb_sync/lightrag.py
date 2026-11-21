"""LightRAG REST client for kb-sync (contracts/lightrag-client.md; FR-002, FR-010, SC-008).

Talks to the pinned v1.5.6 server only through the routes the contract lists. There is
deliberately no method for the bare collection delete, which clears the whole knowledge
base: removal goes through `/documents/delete_document`, one document ID at a time.

Every request builds `httpx.Client(transport=config.HTTP_TRANSPORT)`, read at call time so
tests can swap in an `httpx.MockTransport`.
"""

from __future__ import annotations

import time
from pathlib import PurePosixPath

import httpx

from kb_sync import KbSyncError, ServerDown, config

TERMINAL_STATES = frozenset({"processed", "failed"})
DELETE_ATTEMPTS = 5
PAGE_SIZE = 100

ROUTE_HEALTH = "/health"
ROUTE_QUERY = "/query"
ROUTE_SCAN = "/documents/scan"
ROUTE_TRACK = "/documents/track_status/{track_id}"
ROUTE_PAGINATED = "/documents/paginated"
ROUTE_DELETE_DOCUMENT = "/documents/delete_document"
ROUTE_PIPELINE_STATUS = "/documents/pipeline_status"


def _basename(path: str) -> str:
    return PurePosixPath(str(path).replace("\\", "/")).name


class LightRAG:
    """Thin client over the LightRAG REST API."""

    def __init__(self, base_url, *, sleep=time.sleep, poll_interval=2.0, timeout=3600):
        self.base_url = str(base_url).rstrip("/")
        self._sleep = sleep
        self.poll_interval = poll_interval
        self.timeout = timeout

    # --- transport ------------------------------------------------------------

    def _request(self, method: str, route: str, *, json=None, http_timeout=120.0) -> dict:
        try:
            with httpx.Client(base_url=self.base_url, transport=config.HTTP_TRANSPORT,
                              timeout=http_timeout) as http:
                resp = http.request(method, route, json=json)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ServerDown(self.base_url) from exc
        except httpx.HTTPError as exc:
            raise KbSyncError(f"LightRAG request {method} {route} failed: {exc}") from exc
        if resp.status_code >= 400:
            detail = resp.text[:300]
            raise KbSyncError(f"LightRAG {method} {route} returned HTTP {resp.status_code}: {detail}")
        try:
            return resp.json()
        except ValueError as exc:
            raise KbSyncError(f"LightRAG {method} {route} returned non-JSON content") from exc

    def _deadline_passed(self, started: float) -> bool:
        return time.monotonic() - started > self.timeout

    # --- calls ----------------------------------------------------------------

    def health(self) -> dict:
        return self._request("GET", ROUTE_HEALTH, http_timeout=10.0)

    def scan(self) -> str | None:
        body = self._request("POST", ROUTE_SCAN)
        return body.get("track_id") or None

    def track_status(self, track_id: str) -> dict:
        return self._request("GET", ROUTE_TRACK.format(track_id=track_id))

    def wait_track(self, track_id: str) -> list[dict]:
        """Poll until every document of the track is `processed` or `failed`."""
        started = time.monotonic()
        while True:
            docs = self.track_status(track_id).get("documents") or []
            if docs and all(d.get("status") in TERMINAL_STATES for d in docs):
                return docs
            if self._deadline_passed(started):
                raise KbSyncError(
                    f"LightRAG scan {track_id} did not finish within {self.timeout}s")
            self._sleep(self.poll_interval)

    def find_doc(self, file_path: str) -> dict | None:
        """Find a document whose `file_path` basename equals the given basename."""
        target = _basename(file_path)
        page = 1
        while True:
            body = self._request("POST", ROUTE_PAGINATED, json={
                "page": page, "page_size": PAGE_SIZE,
                "sort_field": "updated_at", "sort_direction": "desc",
            })
            for doc in body.get("documents") or []:
                if _basename(doc.get("file_path") or "") == target:
                    return doc
            if not (body.get("pagination") or {}).get("has_next"):
                return None
            page += 1

    def pipeline_status(self) -> dict:
        return self._request("GET", ROUTE_PIPELINE_STATUS)

    def wait_idle(self) -> None:
        """Poll `pipeline_status` until `busy` is false."""
        started = time.monotonic()
        while self.pipeline_status().get("busy"):
            if self._deadline_passed(started):
                raise KbSyncError(f"LightRAG pipeline still busy after {self.timeout}s")
            self._sleep(self.poll_interval)

    def delete(self, doc_id: str) -> dict:
        """Remove one document via `DELETE /documents/delete_document`.

        `busy` retries with backoff (5 attempts); `not_allowed` raises; after
        `deletion_started` waits for the pipeline to go idle.
        """
        if not doc_id:
            raise KbSyncError("refusing to delete: empty document id")
        payload = {"doc_ids": [doc_id], "delete_file": True, "delete_llm_cache": False}
        delay = max(self.poll_interval, 0.01)
        for attempt in range(1, DELETE_ATTEMPTS + 1):
            body = self._request("DELETE", ROUTE_DELETE_DOCUMENT, json=payload)
            status = body.get("status")
            if status == "deletion_started":
                self.wait_idle()
                return body
            if status == "not_allowed":
                raise KbSyncError(
                    f"LightRAG refused to delete {doc_id}: {body.get('message') or status}")
            if status != "busy":
                raise KbSyncError(f"LightRAG delete of {doc_id} returned status {status!r}")
            if attempt < DELETE_ATTEMPTS:
                self._sleep(delay)
                delay *= 2
        raise KbSyncError(
            f"LightRAG stayed busy; {doc_id} not deleted after {DELETE_ATTEMPTS} attempts")

    def query(self, question: str, *, enable_rerank: bool, mode: str = "mix") -> dict:
        return self._request("POST", ROUTE_QUERY, json={
            "query": question, "mode": mode, "only_need_context": True,
            "include_references": True, "enable_rerank": bool(enable_rerank),
        }, http_timeout=600.0)
