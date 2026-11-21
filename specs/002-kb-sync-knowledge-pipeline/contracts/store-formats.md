# Contract: File formats (store files, packs, proposals, ingest plan)

**Date**: 2026-11-21
**Feature**: [../spec.md](../spec.md) | **Plan**: [../plan.md](../plan.md)

Files the owner reads or edits, or that other components (LightRAG, tests) consume.

## Locations

| What | Path |
|---|---|
| Tool source | `sync/` |
| Records (SQLite, run logs, ingest plan) | `data/kb-sync/` (`kb-sync.db`, `runs/<run_id>.json`, `plan.json`) |
| Store files | `data/inputs/<source>/<source_id>.md` |
| Staged copies for scan | `data/inputs/<source>__<source_id>.md` (flat; LightRAG moves them to `data/inputs/__parsed__/`) |
| Research packs | `packs/<topic>.yaml` |
| Review proposals | `packs/<topic>.review.yaml` |

`<topic>` matches `^[a-z0-9][a-z0-9-]*$`. `<source_id>` is the upstream ID (Drive file ID,
AppFlowy object or row ID, or a path-derived slug for `research`), filesystem-safe and
containing no `__`.

## Source values and labels

| `source` value | Citation label |
|---|---|
| `drive` | `Drive` |
| `appflowy` | `AppFlowy` |
| `notebook-output` | `NotebookLM` |
| `web` | `Web` |
| `research` | `Research` |

Citation line: `[n] Title (Drive|AppFlowy|NotebookLM|Web|Research) link`.
Item status values: `active`, `stale`, `removed`.

## Store file

UTF-8 Markdown with YAML front-matter. The four keys are required, in this order; no
others are written.

```markdown
---
title: "Team topologies notes"
source: drive
url: "https://docs.google.com/document/d/1AbC.../edit"
modified: "2026-11-02T09:14:00Z"
---

# Team topologies notes

<converted body>
```

- `source`: one of the source values above.
- `url`: opens the original item; this is the link in every citation.
- `modified`: upstream last-modified time, ISO 8601 UTC.
- Spreadsheets: one `## <sheet name>` heading and one Markdown table per sheet.
- AppFlowy databases: one table per database, or one file per row for databases in
  `KB_SYNC_APPFLOWY_NOTES_DATABASES`.
- The path is stable for an item's life: rename or move upstream changes `title`, `url`
  or folder locations, never the path.

## Research pack `packs/<topic>.yaml`

Owner-editable. `kb-sync pack` writes it; `kb-sync notebook push` reads it.

```yaml
topic: engineering-leadership
notebook_id: null            # filled in by the first push
items:
  - ref: drive:1AbC...       # <source>:<source_id>
    title: Team topologies notes
    source: drive
    url: https://docs.google.com/document/d/1AbC.../edit
web:
  - title: Example public article
    url: https://example.org/article
```

Unknown `ref` or a missing `url` in `web` → exit `2` naming the entry.

## Review proposal `packs/<topic>.review.yaml`

`kb-sync review <topic>` writes it; the owner edits it; `kb-sync review <topic> --apply`
carries out exactly what remains.

```yaml
topic: engineering-leadership
generated_at: "2026-11-21T10:00:00Z"
drop:
  - ref: drive:1XyZ...
    reason: deleted_upstream     # deleted_upstream | unresolvable_url | stale_uncited | near_duplicate
repush:
  - ref: drive:1AbC...
    reason: changed_since_push
add:
  - ref: appflowy:9f2e...
    reason: new_match            # new_match | web_search
```

`stale_uncited` uses `KB_SYNC_REVIEW_STALE_MONTHS`. Web entries use `url:` in place of
`ref:`.

## Ingest plan `data/kb-sync/plan.json`

Internal to the `kb.sh ingest` host sequence; tests assert on it.

```json
{
  "plan_id": "20261121T023000Z-3f9a",
  "created_at": "2026-11-21T02:30:00Z",
  "removals": [{"ref": "drive:1XyZ...", "doc_id": "doc-...", "title": "...", "reason": "deleted_upstream"}],
  "stages": [{"ref": "drive:1AbC...", "action": "replace", "old_doc_id": "doc-..."}],
  "held": []
}
```

`removals` and `held` are disjoint; when the count exceeds `KB_SYNC_REMOVAL_LIMIT`,
every pending removal goes to `held`. `reason` for a removal is one of `deleted_upstream`,
`trashed_upstream`, `out_of_allow_list`, `replaced`.

## Run log `data/kb-sync/runs/<run_id>.json`

One per pull, ingest or nightly run. Holds the summary counts (`added`, `replaced`,
`removed`, `held`, `skipped`, `failed`), the backup archive name (or `null`), and one entry
per removal and held removal with `title`, `source`, `reason`. SC-008 is checked from
these: every run with `removed > 0` has a non-null backup.
