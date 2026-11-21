"""KB_SYNC_INGEST_TIMEOUT: how long `kb-sync ingest` waits for LightRAG to finish a scan.

Driven through the CLI against a scan that never reaches a terminal state, with a fake
clock that advances 1000 s per reading, so the configured limit decides when it gives up.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from drive_fakes import Harness

STEP = 1000  # fake seconds per clock reading


def _never_finishing(h: Harness, monkeypatch) -> None:
    import kb_sync.lightrag as lr

    clock = SimpleNamespace(now=0.0)

    def monotonic() -> float:
        clock.now += STEP
        return clock.now

    monkeypatch.setattr(lr, "time", SimpleNamespace(monotonic=monotonic, sleep=lambda _s: None))
    real = h.lightrag.handle

    def handle(request: httpx.Request) -> httpx.Response:
        resp = real(request)
        if request.url.path.startswith("/documents/track_status/"):
            body = json.loads(resp.content)
            for d in body["documents"]:
                d["status"] = "processing"
            return httpx.Response(200, json=body)
        return resp

    monkeypatch.setattr(h.lightrag, "handle", handle)


def _polls(h: Harness) -> int:
    return sum(1 for _m, key in h.lightrag.sequence if key == "/documents/track_status")


@pytest.mark.parametrize("env,limit", [({"KB_SYNC_INGEST_TIMEOUT": "3000"}, 3000),
                                       ({"KB_SYNC_INGEST_TIMEOUT": None}, 43200)])
def test_ingest_gives_up_after_the_configured_timeout(tmp_path, monkeypatch, env, limit):
    h = Harness(tmp_path, monkeypatch, **env)
    h.pull()
    _never_finishing(h, monkeypatch)

    _planned, applied = h.ingest()

    assert applied.rc == 1
    assert f"did not finish within {limit}s" in applied.err
    # The wait lasted the configured limit, not a hard-coded one: one poll per STEP.
    assert _polls(h) == limit // STEP + 1
