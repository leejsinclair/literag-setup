"""Google Drive source (T043; FR-006, FR-009, FR-012 to FR-014, FR-018; research.md R8).

`auth(cfg, args)` runs the installed-app OAuth flow on a loopback redirect and saves
`secrets/google-token.json` (mode 0600). `pull(cfg, records, run_id)` brings the store up to
date with the allow-listed folders, over Drive's REST v3 JSON API with httpx.

- First pull, or a changed `KB_SYNC_DRIVE_FOLDER_IDS`: walk every allow-listed folder
  recursively, then save `changes.getStartPageToken` (taken before the walk) as the cursor.
  Records no longer reachable become removals: `deleted_upstream`, `trashed_upstream`, or
  `out_of_allow_list` (always held at ingest).
- Later pulls: `changes.list` with `includeRemoved=true` from the cursor. A change is kept
  only if one of the file's parent folders descends from an allow-listed folder (folder tree
  cached in the cursor, unknown folders looked up by metadata). Out-of-tree changes are
  dropped before any download (Invariant 9).
- Docs export as `text/markdown`, falling back to `text/plain`. Sheets export as xlsx and,
  like `.xlsx` uploads, become Markdown tables (`sheets.py`). PDFs are kept whole. `.md` and
  `.txt` are stored as they are. Audio, video and images are recorded and listed as skipped.
- A name matching `KB_SYNC_NOTEBOOKLM_OUTPUT_PATTERN` gets source `notebook-output` for life;
  its audio and video are recorded only.
- Files sharing an `md5Checksum` are stored and staged once: the lowest item ID is canonical,
  the rest get `duplicate_of` and keep their own folder locations (FR-013).
- An unchanged `modifiedTime` skips the download, so a no-change pull reads only the feed.

The cursor moves only after every item it covers has been committed, so an interrupted pull
replays from the old cursor; each per-item upsert is idempotent.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sys
from datetime import UTC, datetime

import httpx

from kb_sync import AuthExpired, KbSyncError, config, store
from kb_sync.config import log
from kb_sync.ingest import rel_store_path, resolve_store_path
from kb_sync.sources import SourceResult, sheets

API = "https://www.googleapis.com/drive/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CLIENT_FILE = "google-client.json"
TOKEN_FILE = "google-token.json"
CURSOR_KEY = "drive"
SOURCES = ("drive", "notebook-output")

FOLDER = "application/vnd.google-apps.folder"
GDOC = "application/vnd.google-apps.document"
GSHEET = "application/vnd.google-apps.spreadsheet"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF = "application/pdf"
TEXT = {"text/plain", "text/markdown", "text/x-markdown"}
MEDIA = ("audio/", "video/", "image/")
EXPORT_LIMIT = 10_000_000  # Drive's export cap for Docs, in bytes

FILE_FIELDS = "id,name,mimeType,parents,trashed,md5Checksum,size,modifiedTime,webViewLink"
FOLDER_FIELDS = "id,name,mimeType,parents,trashed"


# --------------------------------------------------------------------------- auth


def auth(cfg, args) -> int:
    """`kb-sync auth google`: loopback OAuth flow; re-run to renew (FR-018)."""
    from google_auth_oauthlib import flow as oauth_flow

    client = cfg.secrets_dir / CLIENT_FILE
    if not client.is_file():
        raise KbSyncError(f"missing secrets/{CLIENT_FILE}: create the Google OAuth desktop client "
                          "first (specs/002-kb-sync-knowledge-pipeline/checks/drive-oauth.md)",
                          exit_code=3)
    flow = oauth_flow.InstalledAppFlow.from_client_secrets_file(
        str(client), scopes=config.google_scopes(cfg))
    try:
        # The library prints the sign-in URL on stdout; stdout carries results only.
        with contextlib.redirect_stdout(sys.stderr):
            creds = flow.run_local_server(
                host="127.0.0.1", port=0, open_browser=False,
                authorization_prompt_message="Open this URL in a browser to sign in to Google:\n{url}")
    except Exception as e:  # the provider refused, or the user closed the flow
        raise KbSyncError(f"Google sign-in failed ({type(e).__name__})") from None
    config.write_secret(cfg, TOKEN_FILE, creds.to_json())
    email = "unknown"
    try:
        with httpx.Client(transport=config.HTTP_TRANSPORT, timeout=30) as http:
            resp = http.get(f"{API}/about", params={"fields": "user(emailAddress)"},
                            headers={"Authorization": f"Bearer {creds.token}"})
        if resp.status_code == 200:
            email = resp.json().get("user", {}).get("emailAddress") or email
    except httpx.HTTPError:
        pass
    print(f"signed in: google {email}")
    return 0


# --------------------------------------------------------------------------- client


class DriveError(KbSyncError):
    """A Drive API call failed. Exit 1. Carries the HTTP status and Google's reason."""

    def __init__(self, method, path, resp: httpx.Response):
        self.status = resp.status_code
        self.reason = _reason(resp)
        super().__init__(f"Drive {method} {path} returned HTTP {self.status}"
                         + (f" ({self.reason})" if self.reason else ""))


def _reason(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error")
    except ValueError:
        return ""
    if isinstance(err, dict):
        errors = err.get("errors") or []
        if errors and errors[0].get("reason"):
            return errors[0]["reason"]
        return err.get("status") or ""
    return err if isinstance(err, str) else ""


class DriveClient:
    """Bearer-token httpx client. Refreshes the access token from the stored refresh token."""

    def __init__(self, cfg):
        self.cfg = cfg
        raw = config.read_secret(cfg, TOKEN_FILE)
        if not raw:
            raise AuthExpired("drive", "google")
        try:
            token = json.loads(raw)
        except ValueError:
            raise AuthExpired("drive", "google") from None
        self._refresh_token = token.get("refresh_token")
        if not self._refresh_token:
            raise AuthExpired("drive", "google")
        self._client_id = token.get("client_id")
        self._client_secret = token.get("client_secret")
        self._token_uri = token.get("token_uri") or TOKEN_URL
        if not (self._client_id and self._client_secret):
            self._client_from_file()
        self.http = httpx.Client(transport=config.HTTP_TRANSPORT, timeout=120)
        self._access = self._refresh()

    def _client_from_file(self):
        raw = config.read_secret(self.cfg, CLIENT_FILE)
        if not raw:
            raise KbSyncError(f"missing secrets/{CLIENT_FILE}", exit_code=3)
        data = json.loads(raw)
        block = data.get("installed") or data.get("web") or {}
        self._client_id = block.get("client_id")
        self._client_secret = block.get("client_secret")

    def _refresh(self) -> str:
        try:
            resp = self.http.post(self._token_uri, data={
                "grant_type": "refresh_token", "refresh_token": self._refresh_token,
                "client_id": self._client_id or "", "client_secret": self._client_secret or ""})
        except httpx.HTTPError as e:
            raise KbSyncError(f"Google token endpoint unreachable ({type(e).__name__})") from None
        if resp.status_code in (400, 401):
            if resp.status_code == 401 or _reason(resp) == "invalid_grant":
                raise AuthExpired("drive", "google")
        if resp.status_code >= 400:
            raise KbSyncError(f"Google token refresh returned HTTP {resp.status_code}"
                              + (f" ({_reason(resp)})" if _reason(resp) else ""))
        access = resp.json().get("access_token")
        if not access:
            raise KbSyncError("Google token refresh returned no access token")
        return access

    def get(self, path: str, params: dict | None = None) -> httpx.Response:
        for attempt in (1, 2):
            try:
                resp = self.http.get(f"{API}{path}", params=params,
                                     headers={"Authorization": f"Bearer {self._access}"})
            except httpx.HTTPError as e:
                raise KbSyncError(f"Drive GET {path} failed ({type(e).__name__})") from None
            if resp.status_code != 401:
                return resp
            if attempt == 1:
                self._access = self._refresh()
        raise AuthExpired("drive", "google")

    def json(self, path: str, params: dict | None = None) -> dict:
        resp = self.get(path, params)
        if resp.status_code >= 400:
            raise DriveError("GET", path, resp)
        return resp.json()

    def close(self):
        self.http.close()


# --------------------------------------------------------------------------- pull


class _ItemFailed(Exception):
    def __init__(self, stage: str, reason: str):
        super().__init__(reason)
        self.stage, self.reason = stage, reason


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kind(mime: str) -> str:
    if mime.startswith(MEDIA):
        return "media"
    return {GDOC: "gdoc", GSHEET: "gsheet", XLSX: "xlsx", PDF: "pdf"}.get(
        mime, "text" if mime in TEXT else "unsupported")


def _size_text(size: int | None) -> str:
    return f"{size / 1048576:.1f} MB" if size else "size unknown"


class _Pull:
    def __init__(self, cfg, records, run_id, client: DriveClient):
        self.cfg, self.records, self.run_id, self.client = cfg, records, run_id, client
        self.result = SourceResult()
        self.allow = list(dict.fromkeys(cfg.drive_folder_ids))
        pattern = cfg.notebooklm_output_pattern.strip()
        self.output_re = re.compile(pattern, re.IGNORECASE) if pattern else None
        state = json.loads(records.get_cursor(CURSOR_KEY) or "null") or {}
        self.state = state
        self.folders: dict[str, dict] = state.get("folders") or {}
        self._scope: dict[str, bool] = {}
        self.config_fp = hashlib.sha256(",".join(sorted(self.allow)).encode()).hexdigest()
        self.md5_canon: dict[str, int] = {}
        self.touched: set[int] = set()
        for item in records.items():
            if (item.source in SOURCES and item.upstream_md5 and item.disposition == "ingest"
                    and item.status != "removed" and item.pending != "remove"):
                self.md5_canon.setdefault(item.upstream_md5, item.id)

    # --- driver ----------------------------------------------------------------

    def run(self) -> SourceResult:
        if self.state.get("token") and self.state.get("config") == self.config_fp:
            token = self.changes(self.state["token"])
            self._count_untouched()
        else:
            token = self.client.json("/changes/startPageToken")["startPageToken"]
            self.walk(self.allow, reconcile=True)
        self.records.set_cursor(CURSOR_KEY, json.dumps(
            {"token": token, "config": self.config_fp, "folders": self.folders}))
        return self.result

    def _count_untouched(self) -> None:
        """Items the changes feed did not mention are unchanged (contracts/kb-cli.md → pull)."""
        for item in self.records.items():
            if (item.source in SOURCES and item.status != "removed" and item.pending != "remove"
                    and item.id not in self.touched):
                self.result.unchanged += 1

    # --- folder tree -----------------------------------------------------------

    def _list_children(self, folder_id: str):
        page = None
        while True:
            params = {"q": f"'{folder_id}' in parents and trashed=false",
                      "fields": f"nextPageToken,files({FILE_FIELDS})", "pageSize": 1000}
            if page:
                params["pageToken"] = page
            body = self.client.json("/files", params)
            yield from body.get("files") or []
            page = body.get("nextPageToken")
            if not page:
                return

    def _cache_folder(self, meta: dict) -> None:
        self.folders[meta["id"]] = {"name": meta.get("name"), "parents": meta.get("parents") or [],
                                    "trashed": bool(meta.get("trashed"))}
        self._scope.clear()

    def in_scope(self, folder_id: str, _seen=None) -> bool:
        """True if the folder is allow-listed or descends from an allow-listed folder."""
        if folder_id in self.allow:
            return True
        if folder_id in self._scope:
            return self._scope[folder_id]
        seen = _seen or set()
        if folder_id in seen:
            return False
        seen.add(folder_id)
        meta = self.folders.get(folder_id)
        if meta is None:
            resp = self.client.get(f"/files/{folder_id}", {"fields": FOLDER_FIELDS})
            if resp.status_code == 404:
                self._scope[folder_id] = False
                return False
            if resp.status_code >= 400:
                raise DriveError("GET", f"/files/{folder_id}", resp)
            body = resp.json()
            if body.get("mimeType") != FOLDER:
                self._scope[folder_id] = False
                return False
            self._cache_folder(body)
            meta = self.folders[folder_id]
        ok = not meta["trashed"] and any(self.in_scope(p, seen) for p in meta["parents"])
        self._scope[folder_id] = ok
        return ok

    def walk(self, roots: list[str], *, reconcile: bool) -> None:
        """Walk folder trees breadth-first; process each file once with all its locations."""
        found: dict[str, list] = {}  # file id -> [meta, [folder ids]]
        queue, visited = list(roots), set()
        for root in roots:
            self._cache_folder(self.client.json(f"/files/{root}", {"fields": FOLDER_FIELDS}))
        while queue:
            folder = queue.pop(0)
            if folder in visited:
                continue
            visited.add(folder)
            for child in self._list_children(folder):
                if child.get("mimeType") == FOLDER:
                    self._cache_folder(child)
                    queue.append(child["id"])
                else:
                    entry = found.setdefault(child["id"], [child, []])
                    entry[0] = child
                    if folder not in entry[1]:
                        entry[1].append(folder)
        for meta, locations in found.values():
            self.process(meta, locations)
        if reconcile:
            for item in self.records.items():
                if (item.source in SOURCES and item.status != "removed"
                        and item.pending != "remove" and item.source_id not in found):
                    self.mark_remove(item, self._why_gone(item.source_id))

    def _why_gone(self, file_id: str) -> str:
        resp = self.client.get(f"/files/{file_id}", {"fields": "id,trashed"})
        if resp.status_code == 404:
            return "deleted_upstream"
        if resp.status_code < 400 and resp.json().get("trashed"):
            return "trashed_upstream"
        return "out_of_allow_list"

    # --- changes feed ----------------------------------------------------------

    def changes(self, token: str) -> str:
        latest: dict[str, dict] = {}
        while True:
            body = self.client.json("/changes", {
                "pageToken": token, "includeRemoved": "true", "pageSize": 1000, "spaces": "drive",
                "fields": f"nextPageToken,newStartPageToken,changes(changeType,removed,fileId,"
                          f"file({FILE_FIELDS}))"})
            for ch in body.get("changes") or []:
                if ch.get("changeType", "file") != "file":
                    continue
                latest.pop(ch["fileId"], None)
                latest[ch["fileId"]] = ch
            if body.get("nextPageToken"):
                token = body["nextPageToken"]
                continue
            new_token = body.get("newStartPageToken")
            if not new_token:
                raise KbSyncError("Drive changes.list returned neither nextPageToken nor newStartPageToken")
            break

        # Folders first, so file scope is judged against the new tree.
        rescope, newly = False, []
        for fid, ch in latest.items():
            f = ch.get("file")
            is_folder = (f and f.get("mimeType") == FOLDER) or (not f and fid in self.folders)
            if not is_folder:
                continue
            before = self.in_scope(fid) if fid in self.folders or fid in self.allow else False
            if f is None:
                self.folders.pop(fid, None)
                self._scope.clear()
            else:
                self._cache_folder(f)
            after = f is not None and self.in_scope(fid)
            if before != after:
                rescope = True
                if after:
                    newly.append(fid)
        for fid, ch in latest.items():
            f = ch.get("file")
            if (f and f.get("mimeType") == FOLDER) or (not f and fid in self.folders):
                continue
            self.apply_change(fid, ch)
        if newly:
            self.walk(newly, reconcile=False)
        if rescope:
            self._rescope()
        return new_token

    def apply_change(self, fid: str, ch: dict) -> None:
        item = self.existing(fid)
        f = ch.get("file")
        if ch.get("removed") or not f:
            if item:
                self.mark_remove(item, "deleted_upstream")
            return
        if f.get("trashed"):
            if item:
                self.mark_remove(item, "trashed_upstream")
            return
        locations = [p for p in f.get("parents") or [] if self.in_scope(p)]
        if not locations:
            if item:
                self.mark_remove(item, "out_of_allow_list")
            return  # outside the allow-list: never downloaded
        self.process(f, locations)

    def _rescope(self) -> None:
        """After folder moves, drop locations that left the allow-list."""
        for item in self.records.items():
            if item.source not in SOURCES or item.status == "removed" or item.pending == "remove":
                continue
            locs = self.records.locations(item.id)
            keep = [loc for loc in locs if self.in_scope(loc)]
            if not keep:
                self.mark_remove(item, "out_of_allow_list")
            elif keep != locs:
                self.records.set_locations(item.id, keep)

    # --- items -----------------------------------------------------------------

    def existing(self, fid: str):
        for source in SOURCES:
            item = self.records.get_item(source, fid)
            if item:
                return item
        return None

    def _log(self, item_id, title, source, action, reason=""):
        self.touched.add(item_id)
        self.records.log_item(self.run_id, item_id=item_id, title=title, source=source,
                              action=action, reason=reason)

    def process(self, meta: dict, locations: list[str]) -> None:
        fid, name, mime = meta["id"], meta.get("name") or meta["id"], meta.get("mimeType") or ""
        item = self.existing(fid)
        if item:
            source = item.source
        else:
            source = "notebook-output" if self.output_re and self.output_re.search(name) else "drive"
        size = int(meta["size"]) if meta.get("size") else None
        md5 = meta.get("md5Checksum")
        base = {"title": name, "url": meta.get("webViewLink") or f"https://drive.google.com/file/d/{fid}/view",
                "modified": meta.get("modifiedTime"), "mime": mime, "size_bytes": size,
                "upstream_md5": md5}
        kind = _kind(mime)

        if kind == "media":
            if source == "notebook-output":
                disposition, reason = "record_only", "NotebookLM audio/video output: recorded, not ingested"
            else:
                disposition, reason = "skip_media", f"{mime} is not ingested"
            it = self.records.upsert_item(source, fid, **base, disposition=disposition,
                                          status="active", pending="none", held=False,
                                          removal_reason=None)
            self.records.set_locations(it.id, locations)
            self.result.skipped.append((name, reason))
            self._log(it.id, name, source, "skipped", reason)
            return

        if item and self._unchanged_upstream(item, base):
            self._touch(item, base, locations)
            return

        canon_id = self.md5_canon.get(md5) if md5 else None
        if canon_id and (item is None or (canon_id != item.id and not item.doc_id)):
            self._duplicate(item, source, fid, base, locations, canon_id)
            return

        try:
            payload, ext = self._fetch(fid, name, mime, kind, size)
        except _ItemFailed as e:
            self._failed(item, source, fid, base, locations, e)
            return

        data = payload.encode("utf-8") if ext == "md" else payload
        fingerprint = hashlib.sha256(data).hexdigest()
        path = store.store_path(self.cfg.inputs_dir, source, fid, ext=ext)
        rewrite = (item is None or item.fingerprint != fingerprint or not path.exists()
                   or (item.title, item.url, item.modified) != (base["title"], base["url"], base["modified"]))
        if rewrite:
            if ext == "md":
                store.write_markdown(self.cfg.inputs_dir, source, fid, title=base["title"],
                                     url=base["url"], modified=base["modified"] or "", body=payload)
            else:
                store.write_binary(self.cfg.inputs_dir, source, fid, ext, data)
        fields = dict(base, fingerprint=fingerprint, store_path=rel_store_path(self.cfg, path),
                      staged_name=store.staged_name(source, fid, ext=ext), disposition="ingest",
                      duplicate_of=None, held=False, removal_reason=None)
        if item is None or item.status == "removed":
            action = "new"
            fields.update(status="stale", pending="ingest", failure=None, doc_id=None)
            self.result.new += 1
        elif fingerprint != item.fingerprint or item.disposition != "ingest":
            action = "changed"
            fields.update(status="stale", pending="replace" if item.doc_id else "ingest", failure=None)
            self.result.changed += 1
        else:
            action = None
            fields["pending"] = self._resumed_pending(item)
            self.result.unchanged += 1
        it = self.records.upsert_item(source, fid, **fields)
        self.touched.add(it.id)
        self.records.set_locations(it.id, locations)
        if md5:
            self.md5_canon.setdefault(md5, it.id)
        if action:
            self._log(it.id, name, source, action)

    @staticmethod
    def _unchanged_upstream(item, base) -> bool:
        if item.status == "removed" or item.modified != base["modified"] or item.mime != base["mime"]:
            return False
        if item.disposition == "duplicate":
            return True
        return bool(item.fingerprint) or item.failure is not None

    def _resumed_pending(self, item) -> str:
        if item.pending != "remove":
            return item.pending
        if item.status == "active":
            return "none"
        if item.failure and item.failure.get("fingerprint") == item.fingerprint:
            return "none"
        return "replace" if item.doc_id else "ingest"

    def _touch(self, item, base, locations) -> None:
        """Upstream content unchanged: update title, link and locations only."""
        fields = {"title": base["title"], "url": base["url"], "size_bytes": base["size_bytes"],
                  "upstream_md5": base["upstream_md5"]}
        if item.pending == "remove":
            fields.update(pending=self._resumed_pending(item), held=False, removal_reason=None)
        if (item.title, item.url) != (base["title"], base["url"]) and item.store_path \
                and item.store_path.endswith(".md"):
            path = resolve_store_path(self.cfg, item.store_path)
            if path.exists():
                _, body = store.read_markdown(path)
                store.write_markdown(self.cfg.inputs_dir, item.source, item.source_id,
                                     title=base["title"], url=base["url"],
                                     modified=item.modified or "", body=body)
        self.records.update_item(item.id, **fields)
        self.records.set_locations(item.id, locations)
        self.touched.add(item.id)
        self.result.unchanged += 1

    def _duplicate(self, item, source, fid, base, locations, canon_id) -> None:
        canon = self.records.get_by_id(canon_id)
        it = self.records.upsert_item(source, fid, **base, disposition="duplicate",
                                      duplicate_of=canon_id, status="active", pending="none",
                                      held=False, removal_reason=None, fingerprint=None,
                                      store_path=None, staged_name=None, failure=None)
        self.records.set_locations(it.id, locations)
        folders = list(dict.fromkeys(self.records.locations(canon_id) + locations))
        reason = f"same content as {canon.title}; folders: {', '.join(folders)}"
        if item is None:
            self.result.new += 1
        else:
            self.result.unchanged += 1
        self._log(it.id, base["title"], source, "duplicate", reason)
        log(f"duplicate: {base['title']} ({source}): {reason}")

    def _failed(self, item, source, fid, base, locations, e: _ItemFailed) -> None:
        fields = dict(base, disposition="ingest", held=False, removal_reason=None,
                      failure={"stage": e.stage, "reason": e.reason, "fingerprint": None, "at": _now()})
        if item is None or item.status == "removed":
            fields.update(status="stale", pending="none", doc_id=None)
        else:
            # Any older version stays in the knowledge base until a good copy arrives.
            fields.update(status="stale", pending="none")
        it = self.records.upsert_item(source, fid, **fields)
        self.records.set_locations(it.id, locations)
        self.result.failed.append((base["title"], e.reason))
        self._log(it.id, base["title"], source, "failed", e.reason)

    def mark_remove(self, item, reason: str) -> None:
        if item.status == "removed" or item.pending == "remove":
            return
        if item.upstream_md5 and self.md5_canon.get(item.upstream_md5) == item.id:
            del self.md5_canon[item.upstream_md5]
        if item.doc_id:
            self.records.update_item(item.id, pending="remove", removal_reason=reason, held=False)
            self._log(item.id, item.title, item.source, "remove", reason)
        else:
            # Nothing in LightRAG to delete: retire the record and move any store file aside.
            fields = {"status": "removed", "pending": "none", "held": False, "removal_reason": reason}
            if item.store_path:
                path = resolve_store_path(self.cfg, item.store_path)
                if path.exists():
                    fields["store_path"] = rel_store_path(self.cfg, store.move_to_removed(path))
            self.records.update_item(item.id, **fields)
            self._log(item.id, item.title, item.source, "removed", reason)
        self.result.removed += 1
        if item.disposition == "ingest":
            self._promote_duplicate(item)

    def _promote_duplicate(self, canon) -> None:
        """The canonical copy went away: the oldest duplicate takes over (data-model FR-013)."""
        dups = [i for i in self.records.items()
                if i.duplicate_of == canon.id and i.status != "removed" and i.pending != "remove"]
        if not dups:
            return
        heir, rest = dups[0], dups[1:]
        for d in rest:
            self.records.update_item(d.id, duplicate_of=heir.id)
        heir = self.records.update_item(heir.id, disposition="ingest", duplicate_of=None,
                                        fingerprint=None)
        resp = self.client.get(f"/files/{heir.source_id}", {"fields": FILE_FIELDS})
        if resp.status_code >= 400 or resp.json().get("trashed"):
            return  # its own change will retire it
        locations = self.records.locations(heir.id)
        self.process(resp.json(), locations)

    # --- content ---------------------------------------------------------------

    def _download(self, fid: str) -> bytes:
        resp = self.client.get(f"/files/{fid}", {"alt": "media"})
        if resp.status_code >= 400:
            raise _ItemFailed("export", f"download failed: HTTP {resp.status_code}"
                              + (f" ({_reason(resp)})" if _reason(resp) else ""))
        return resp.content

    def _export(self, fid: str, mime: str, size: int | None) -> httpx.Response:
        resp = self.client.get(f"/files/{fid}/export", {"mimeType": mime})
        too_big = _reason(resp) == "exportSizeLimitExceeded" or (
            resp.status_code == 403 and size is not None and size > EXPORT_LIMIT)
        if too_big:
            raise _ItemFailed("export", f"too large for Drive export ({_size_text(size)})")
        return resp

    def _fetch(self, fid, name, mime, kind, size) -> tuple[str | bytes, str]:
        if kind == "unsupported":
            raise _ItemFailed("convert", f"unsupported type {mime}")
        if kind == "gdoc":
            resp = self._export(fid, "text/markdown", size)
            if resp.status_code >= 400:
                resp = self._export(fid, "text/plain", size)
            if resp.status_code >= 400:
                raise _ItemFailed("export", f"export failed: HTTP {resp.status_code}"
                                  + (f" ({_reason(resp)})" if _reason(resp) else ""))
            return resp.content.decode("utf-8", "replace"), "md"
        if kind in ("gsheet", "xlsx"):
            if kind == "gsheet":
                resp = self._export(fid, XLSX, size)
                if resp.status_code >= 400:
                    raise _ItemFailed("export", f"export failed: HTTP {resp.status_code}"
                                      + (f" ({_reason(resp)})" if _reason(resp) else ""))
                data = resp.content
            else:
                data = self._download(fid)
            try:
                return sheets.xlsx_to_markdown(data), "md"
            except Exception as e:  # a corrupt or password-protected workbook
                raise _ItemFailed("convert", f"spreadsheet could not be read ({type(e).__name__})") from None
        if kind == "pdf":
            return self._download(fid), "pdf"
        return self._download(fid).decode("utf-8", "replace"), "md"


def pull(cfg, records, run_id) -> SourceResult:
    """Pull the allow-listed Drive folders into the store (contracts/kb-cli.md → pull)."""
    if not cfg.drive_folder_ids:
        return SourceResult()
    client = DriveClient(cfg)
    try:
        return _Pull(cfg, records, run_id, client).run()
    finally:
        client.close()
