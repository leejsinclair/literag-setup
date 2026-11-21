"""Fake Google Drive (REST v3), fake OAuth token endpoint and fake LightRAG for the Story 1 tests.

One `httpx.MockTransport` serves all three; `Harness` patches it into
`kb_sync.config.HTTP_TRANSPORT` and points kb-sync's env, secrets and data dirs at a tmp dir.

The Drive responses are synthesized from the resource shapes in Google's Drive v3 reference
(files.list, files.get, files.export, changes.getStartPageToken, changes.list) and the
OAuth 2.0 token endpoint. No live account was recorded. The starting corpus and the canned
bodies live in `tests/fixtures/drive/`.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "drive"
DRIVE = "www.googleapis.com"
OAUTH = "oauth2.googleapis.com"
LIGHTRAG_PORT = 9621
FOLDER = "application/vnd.google-apps.folder"
GDOC = "application/vnd.google-apps.document"
GSHEET = "application/vnd.google-apps.spreadsheet"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

SECRET_REFRESH = "1//SECRET-refresh-token-story1-do-not-leak"
SECRET_CLIENT = "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leak"
ACCESS_TOKEN = json.loads((FIXTURES / "token_ok.json").read_text())["access_token"]


class Killed(BaseException):
    """Simulates the process being killed mid-run (not an Exception, so nothing catches it)."""


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _json_fixture(name: str, **subs) -> dict:
    text = (FIXTURES / name).read_text()
    for key, val in subs.items():
        text = text.replace("{" + key + "}", val)
    return json.loads(text)


def workbook_bytes(sheets: dict) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- Drive


class FakeDrive:
    """In-memory Drive v3: a folder tree, file contents, exports and a changes feed."""

    def __init__(self, page_size: int = 3):
        tree = json.loads((FIXTURES / "tree.json").read_text())
        workbooks = json.loads((FIXTURES / "workbooks.json").read_text())
        self.page_size = page_size
        self.clock = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
        self.files: dict[str, dict] = {}
        self.content: dict[str, bytes] = {}
        self.exports: dict[str, dict[str, bytes | str]] = {}
        self.changes: list[dict] = []
        self.requests: list[httpx.Request] = []
        self.revoked = False
        for f in tree["files"]:
            fid = f["id"]
            meta = {k: v for k, v in f.items() if not k.startswith("_")}
            meta.setdefault("trashed", False)
            self.files[fid] = meta
            if "_content" in f:
                self.content[fid] = _fixture(f["_content"])
            if "_workbook" in f:
                data = workbook_bytes(workbooks[f["_workbook"]])
                if meta["mimeType"] == GSHEET:
                    self.exports[fid] = {XLSX: data}
                else:
                    self.content[fid] = data
            if "_export" in f:
                self.exports[fid] = {
                    mime: (src if src.startswith("error:") else _fixture(src))
                    for mime, src in f["_export"].items()
                }
            self._stamp(fid)

    # --- state helpers ---------------------------------------------------------

    def _tick(self) -> str:
        self.clock += timedelta(minutes=1)
        return self.clock.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _stamp(self, fid: str) -> None:
        meta = self.files[fid]
        meta["modifiedTime"] = self._tick()
        if fid in self.content and meta["mimeType"] != FOLDER:
            data = self.content[fid]
            meta["md5Checksum"] = hashlib.md5(data).hexdigest()
            meta["size"] = str(len(data))
        if meta["mimeType"] == GDOC:
            meta["webViewLink"] = f"https://docs.google.com/document/d/{fid}/edit"
        elif meta["mimeType"] == GSHEET:
            meta["webViewLink"] = f"https://docs.google.com/spreadsheets/d/{fid}/edit"
        elif meta["mimeType"] == FOLDER:
            meta["webViewLink"] = f"https://drive.google.com/drive/folders/{fid}"
        else:
            meta["webViewLink"] = f"https://drive.google.com/file/d/{fid}/view"

    def _change(self, fid: str, removed: bool = False) -> None:
        change = {"kind": "drive#change", "changeType": "file", "time": self._tick(),
                  "removed": removed, "fileId": fid}
        if not removed:
            change["file"] = copy.deepcopy(self.files[fid])
        self.changes.append(change)

    def add_file(self, fid, name, mime, parents, *, content: bytes | None = None,
                 export: dict | None = None, size: str | None = None) -> None:
        meta = {"id": fid, "name": name, "mimeType": mime, "parents": list(parents), "trashed": False}
        if size:
            meta["size"] = size
        self.files[fid] = meta
        if content is not None:
            self.content[fid] = content
        if export is not None:
            self.exports[fid] = export
        self._stamp(fid)
        self._change(fid)

    def add_doc(self, fid, name, parents, text: str) -> None:
        self.add_file(fid, name, GDOC, parents, export={"text/markdown": text.encode()}, size=str(len(text)))

    def edit_doc(self, fid, text: str) -> None:
        self.exports[fid] = {"text/markdown": text.encode()}
        self._stamp(fid)
        self._change(fid)

    def edit_content(self, fid, data: bytes) -> None:
        self.content[fid] = data
        self._stamp(fid)
        self._change(fid)

    def rename(self, fid, name) -> None:
        self.files[fid]["name"] = name
        self._stamp(fid)  # Drive bumps modifiedTime on a rename
        self._change(fid)

    def move(self, fid, parents) -> None:
        self.files[fid]["parents"] = list(parents)
        self._change(fid)

    def trash(self, fid) -> None:
        self.files[fid]["trashed"] = True
        self._change(fid)

    def delete(self, fid) -> None:
        self.files.pop(fid)
        self.content.pop(fid, None)
        self.exports.pop(fid, None)
        self._change(fid, removed=True)

    # --- request accounting ----------------------------------------------------

    def downloads(self) -> list[str]:
        """File IDs whose content was fetched (alt=media or export)."""
        out = []
        for r in self.requests:
            m = re.match(r"^/drive/v3/files/([^/]+)(/export)?$", r.url.path)
            if m and (m.group(2) or r.url.params.get("alt") == "media"):
                out.append(m.group(1))
        return out

    def calls(self, path_prefix: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.startswith(path_prefix)]

    # --- HTTP ------------------------------------------------------------------

    @staticmethod
    def _err(status: int, body: dict) -> httpx.Response:
        return httpx.Response(status, json=body)

    def handle_token(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        form = parse_qs(request.content.decode())
        if self.revoked or form.get("refresh_token", [""])[0] != SECRET_REFRESH:
            return httpx.Response(400, json=_json_fixture("token_invalid_grant.json"))
        return httpx.Response(200, json=_json_fixture("token_ok.json"))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != f"Bearer {ACCESS_TOKEN}":
            return self._err(401, {"error": {"code": 401, "message": "Invalid Credentials",
                                             "errors": [{"reason": "authError"}]}})
        path, params = request.url.path, request.url.params
        if path == "/drive/v3/changes/startPageToken":
            return httpx.Response(200, json={"kind": "drive#startPageToken",
                                             "startPageToken": str(len(self.changes) + 1)})
        if path == "/drive/v3/changes":
            return self._changes(params)
        if path == "/drive/v3/files":
            return self._list(params)
        if path == "/drive/v3/about":
            return httpx.Response(200, json={"user": {"emailAddress": "owner@example.test"}})
        m = re.match(r"^/drive/v3/files/([^/]+)(/export)?$", path)
        if not m:
            return self._err(404, {"error": {"code": 404, "message": f"no route {path}"}})
        fid, export = m.group(1), m.group(2)
        if fid not in self.files:
            return self._err(404, _json_fixture("not_found.json", id=fid))
        if export:
            return self._export(fid, params.get("mimeType", ""))
        if params.get("alt") == "media":
            if fid not in self.content:
                return self._err(403, {"error": {"code": 403, "message": "Only files with binary content can be downloaded.",
                                                 "errors": [{"reason": "fileNotDownloadable"}]}})
            return httpx.Response(200, content=self.content[fid])
        return httpx.Response(200, json=copy.deepcopy(self.files[fid]))

    def _list(self, params) -> httpx.Response:
        q = params.get("q", "")
        m = re.fullmatch(r"'([^']+)' in parents and trashed\s*=\s*false", q.strip())
        if not m:
            return self._err(400, {"error": {"code": 400, "message": f"unsupported q {q!r}"}})
        parent = m.group(1)
        kids = [copy.deepcopy(f) for f in self.files.values()
                if parent in f.get("parents", []) and not f.get("trashed")]
        start = int(params.get("pageToken") or 0)
        page = kids[start:start + self.page_size]
        body = {"kind": "drive#fileList", "incompleteSearch": False, "files": page}
        if start + self.page_size < len(kids):
            body["nextPageToken"] = str(start + self.page_size)
        return httpx.Response(200, json=body)

    def _changes(self, params) -> httpx.Response:
        if params.get("includeRemoved") not in ("true", "True"):
            return self._err(400, {"error": {"code": 400, "message": "test fake requires includeRemoved=true"}})
        token = int(params.get("pageToken"))
        start = token - 1
        page = self.changes[start:start + self.page_size]
        body = {"kind": "drive#changeList", "changes": copy.deepcopy(page)}
        if start + self.page_size < len(self.changes):
            body["nextPageToken"] = str(token + self.page_size)
        else:
            body["newStartPageToken"] = str(len(self.changes) + 1)
        return httpx.Response(200, json=body)

    def _export(self, fid: str, mime: str) -> httpx.Response:
        table = self.exports.get(fid) or {}
        src = table.get(mime)
        if src is None:
            return self._err(400, _json_fixture("export_bad_request.json"))
        if isinstance(src, str) and src == "error:exportSizeLimitExceeded":
            return self._err(403, _json_fixture("export_size_error.json"))
        if isinstance(src, str) and src == "error:badRequest":
            return self._err(400, _json_fixture("export_bad_request.json"))
        return httpx.Response(200, content=src)


# --------------------------------------------------------------------------- LightRAG

IDLE = {"busy": False, "job_name": "Default Job", "docs": 0, "batchs": 0, "cur_batch": 0,
        "request_pending": False, "latest_message": "", "history_messages": []}
FAIL_MARKER = b"SCANNED-IMAGE-ONLY"


class FakeLightRAG:
    """Just enough of LightRAG v1.5.6's document routes, acting on the real inputs dir."""

    def __init__(self, inputs_dir: Path):
        self.inputs_dir = Path(inputs_dir)
        self.docs: dict[str, dict] = {}
        self.tracks: dict[str, list[str]] = {}
        self.sequence: list[tuple[str, str]] = []
        self.before: dict[str, object] = {}  # route key -> callable run before handling
        self.busy_deletes = 0
        self.down = False
        self._n = 0

    def doc_for(self, name: str) -> dict | None:
        for d in self.docs.values():
            if d["file_path"] == name:
                return d
        return None

    def docs_named(self, name: str) -> list[dict]:
        return [d for d in self.docs.values() if d["file_path"] == name]

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        method, path = request.method, request.url.path
        key = path if not path.startswith("/documents/track_status/") else "/documents/track_status"
        self.sequence.append((method, key))
        hook = self.before.get(key)
        if hook:
            hook()
        if (method, path) == ("DELETE", "/documents"):
            raise AssertionError("client sent DELETE /documents (wipes the KB)")
        if path == "/health":
            return httpx.Response(200, json={"status": "healthy"})
        if (method, path) == ("POST", "/documents/scan"):
            return self._scan()
        if key == "/documents/track_status":
            tid = path.rsplit("/", 1)[1]
            docs = [self._public(self.docs[i]) for i in self.tracks.get(tid, []) if i in self.docs]
            return httpx.Response(200, json={"track_id": tid, "documents": docs,
                                             "total_count": len(docs), "status_summary": {}})
        if (method, path) == ("POST", "/documents/paginated"):
            docs = [self._public(d) for d in self.docs.values()]
            return httpx.Response(200, json={"documents": docs, "pagination": {
                "page": 1, "page_size": 100, "total_count": len(docs), "total_pages": 1,
                "has_next": False, "has_prev": False}, "status_counts": {}})
        if (method, path) == ("DELETE", "/documents/delete_document"):
            return self._delete(json.loads(request.content))
        if (method, path) == ("GET", "/documents/pipeline_status"):
            return httpx.Response(200, json=IDLE)
        return httpx.Response(404, json={"detail": "Not Found"})

    @staticmethod
    def _public(d: dict) -> dict:
        return {k: v for k, v in d.items() if k != "content"}

    def _scan(self) -> httpx.Response:
        self._n += 1
        tid = f"scan_20261121_{self._n:06d}"
        ids = []
        parsed = self.inputs_dir / "__parsed__"
        for entry in sorted(self.inputs_dir.iterdir()):
            if not entry.is_file() or entry.name.startswith("."):
                continue
            if self.doc_for(entry.name):
                continue  # LightRAG skips a file_path it already holds
            data = entry.read_bytes()
            self._n += 1
            doc_id = f"doc-{self._n:04d}"
            failed = FAIL_MARKER in data
            self.docs[doc_id] = {
                "id": doc_id, "file_path": entry.name, "track_id": tid,
                "status": "failed" if failed else "processed",
                "error_msg": "PDF has no extractable text (image-only)" if failed else None,
                "content_summary": data[:60].decode("utf-8", "replace"),
                "content_length": len(data), "chunks_count": 0 if failed else 1,
                "created_at": "2026-11-21T10:00:00+00:00", "updated_at": "2026-11-21T10:00:00+00:00",
                "metadata": {}, "content": data.decode("utf-8", "replace"),
            }
            ids.append(doc_id)
            parsed.mkdir(exist_ok=True)
            shutil.move(str(entry), parsed / entry.name)
        self.tracks[tid] = ids
        return httpx.Response(200, json={"status": "scanning_started",
                                         "message": "Scanning process has been initiated in the background",
                                         "track_id": tid})

    def _delete(self, body: dict) -> httpx.Response:
        if self.busy_deletes > 0:
            self.busy_deletes -= 1
            return httpx.Response(200, json={"status": "busy", "message": "pipeline busy", "doc_id": ""})
        for doc_id in body["doc_ids"]:
            doc = self.docs.pop(doc_id, None)
            if doc and body.get("delete_file"):
                for p in (self.inputs_dir / doc["file_path"], self.inputs_dir / "__parsed__" / doc["file_path"]):
                    p.unlink(missing_ok=True)
        return httpx.Response(200, json={"status": "deletion_started", "message": "ok",
                                         "doc_id": ",".join(body["doc_ids"])})

    def knows(self, text: str) -> bool:
        """True if any document LightRAG holds contains `text`."""
        return any(text in d["content"] for d in self.docs.values())


# --------------------------------------------------------------------------- harness


class Result:
    def __init__(self, rc: int, out: str, err: str):
        self.rc, self.out, self.err = rc, out, err
        lines = [line for line in out.splitlines() if line.strip()]
        self.last = lines[-1] if lines else ""

    def fields(self) -> dict:
        """Parse `k=v k=v` from the last stdout line."""
        return dict(part.split("=", 1) for part in self.last.split())

    def __repr__(self):
        return f"Result(rc={self.rc}, out={self.out!r}, err={self.err!r})"


BASE_ENV = {
    "HOST": "127.0.0.1",
    "PORT": str(LIGHTRAG_PORT),
    "LLM_MODEL": "qwen2.5:3b",
    "KB_SYNC_DRIVE_FOLDER_IDS": "lead",
    "KB_SYNC_REMOVAL_LIMIT": "10",
}


class Harness:
    """A tmp kb-sync install wired to FakeDrive and FakeLightRAG."""

    def __init__(self, tmp_path: Path, monkeypatch, **env):
        from kb_sync import config

        self.tmp = tmp_path
        self.env_file = tmp_path / "app.env"
        self.secrets_dir = tmp_path / "secrets"
        self.data_dir = tmp_path / "data"
        self.inputs_dir = self.data_dir / "inputs"
        self.records_dir = self.data_dir / "kb-sync"
        self.secrets_dir.mkdir(mode=0o700)
        for sub in ("inputs", "kb-sync", "packs", "research"):
            (self.data_dir / sub).mkdir(parents=True)
        self.env = dict(BASE_ENV)
        self.set_env(**env)
        monkeypatch.setenv("KB_SYNC_ENV_FILE", str(self.env_file))
        monkeypatch.setenv("KB_SYNC_SECRETS_DIR", str(self.secrets_dir))
        monkeypatch.setenv("KB_SYNC_DATA_DIR", str(self.data_dir))
        (self.secrets_dir / "google-client.json").write_text(json.dumps({"installed": {
            "client_id": "cid.apps.googleusercontent.com", "client_secret": SECRET_CLIENT,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"]}}))
        (self.secrets_dir / "google-token.json").write_text(json.dumps({
            "token": "ya29.stale-access", "refresh_token": SECRET_REFRESH,
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "cid.apps.googleusercontent.com", "client_secret": SECRET_CLIENT,
            "scopes": ["https://www.googleapis.com/auth/drive.readonly"]}))
        for p in self.secrets_dir.iterdir():
            p.chmod(0o600)
        self.drive = FakeDrive()
        self.lightrag = FakeLightRAG(self.inputs_dir)
        monkeypatch.setattr(config, "HTTP_TRANSPORT", httpx.MockTransport(self._route))
        # LightRAG client polls with time.sleep by default; keep the tests instant.
        import kb_sync.lightrag as lr

        monkeypatch.setattr(lr.time, "sleep", lambda _s: None)

    def set_env(self, **changes) -> None:
        for k, v in changes.items():
            if v is None:
                self.env.pop(k, None)
            else:
                self.env[k] = str(v)
        self.env_file.write_text("".join(f"{k}={v}\n" for k, v in self.env.items()))

    def _route(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == DRIVE:
            return self.drive.handle(request)
        if host == OAUTH:
            return self.drive.handle_token(request)
        if host == "127.0.0.1" and request.url.port == LIGHTRAG_PORT:
            return self.lightrag.handle(request)
        raise AssertionError(f"unexpected request to {request.url}")

    # --- running the CLI -------------------------------------------------------

    def run(self, *argv: str) -> Result:
        from kb_sync import cli

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return Result(rc, out.getvalue(), err.getvalue())

    def pull(self) -> Result:
        return self.run("pull", "--source", "drive")

    def plan(self, *, confirm: bool = False) -> Result:
        return self.run("ingest", "--plan", *(["--confirm-removals"] if confirm else []))

    def plan_json(self) -> dict:
        return json.loads((self.records_dir / "plan.json").read_text())

    def backup_after_plan(self, seconds: int = 5) -> str:
        """An archive name in backup.sh's format, timestamped after the plan's created_at."""
        created = datetime.strptime(self.plan_json()["created_at"], "%Y-%m-%dT%H:%M:%SZ")
        stamp = (created + timedelta(seconds=seconds)).strftime("%Y%m%dT%H%M%SZ")
        return f"kb-{stamp}.tar.gz"

    def apply(self, plan_id: str, backup: str | None = None, *, confirm: bool = False) -> Result:
        argv = ["ingest", "--apply", plan_id]
        if backup:
            argv += ["--backup", backup]
        if confirm:
            argv.append("--confirm-removals")
        return self.run(*argv)

    def ingest(self, *, confirm: bool = False) -> tuple[Result, Result]:
        """What scripts/kb.sh ingest does: plan, back up if anything is removed, apply."""
        planned = self.plan(confirm=confirm)
        assert planned.rc == 0, planned
        f = planned.fields()
        backup = self.backup_after_plan() if int(f["removals"]) > 0 else None
        return planned, self.apply(f["plan_id"], backup, confirm=confirm)

    # --- reading results -------------------------------------------------------

    def records(self):
        from kb_sync.records import Records

        return Records(self.records_dir / "kb-sync.db")

    def store_file(self, source: str, source_id: str, ext: str = "md") -> Path:
        return self.inputs_dir / source / f"{source_id}.{ext}"

    def run_logs(self) -> list[dict]:
        runs = self.records_dir / "runs"
        if not runs.is_dir():
            return []
        paths = sorted(runs.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
        return [json.loads(p.read_text()) for p in paths]

    def last_log(self, kind: str) -> dict:
        logs = [log for log in self.run_logs() if log["kind"] == kind]
        assert logs, f"no {kind} run log"
        return logs[-1]


def pull_counts(result: Result, source: str = "drive") -> dict:
    """Parse the `drive new=.. changed=..` line of `pull`'s stdout."""
    for line in result.out.splitlines():
        parts = line.split()
        if parts and parts[0] == source:
            return {k: int(v) for k, v in (p.split("=", 1) for p in parts[1:])}
    raise AssertionError(f"no {source} line in {result.out!r}")
