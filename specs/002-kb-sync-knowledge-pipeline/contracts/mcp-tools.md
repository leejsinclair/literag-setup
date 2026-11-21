# Contract: Claude Code tools (`kb_search`, `kb_get_source`)

**Date**: 2026-11-21
**Feature**: [../spec.md](../spec.md) (Story 3, FR-024) | **Research**: [../research.md](../research.md) R10

- **Transport**: MCP over stdio. Server is `kb-sync mcp` (FastMCP), launched per session
  by `scripts/kb.sh mcp`. No network listener.
- **Registration** (user scope, this machine only):
  `claude mcp add kb -- <repo>/scripts/kb.sh mcp`
- **Scope**: loopback only. A cloud Claude session cannot reach it.
- Both tools share `ask`'s retrieval, filter and citation path. Neither calls Claude
  itself; the calling session is the answer stage.

## `kb_search`

**Input**

| Field | Type | Required | Meaning |
|---|---|---|---|
| `query` | string | yes | the search question |
| `filter` | `"reranker"` \| `"local"` \| `"none"` | no | defaults to `KB_SYNC_FILTER`, with the same reranker-unavailable fallback as `ask` |

**Output** (JSON text content)

```json
{
  "filter": "reranker",
  "passages": [
    {
      "n": 1,
      "ref": "drive:1AbC...",
      "title": "Team topologies notes",
      "source": "drive",
      "label": "Drive",
      "url": "https://docs.google.com/document/d/1AbC.../edit",
      "citation": "[1] Team topologies notes (Drive) https://docs.google.com/document/d/1AbC.../edit",
      "text": "chunk content..."
    }
  ]
}
```

- `citation` matches `[n] Title (Drive|AppFlowy|NotebookLM|Web|Research) link`, the same
  line `ask` prints.
- `source` is one of `drive`, `appflowy`, `notebook-output`, `web`, `research`; `label` is
  its display form (see [store-formats.md](./store-formats.md) → Source labels).
- `ref` is `<source>:<source_id>` and is the input to `kb_get_source`.
- No surviving passage → `"passages": []` and `"message": "not in the knowledge base"`.
- `filter` reads `"local (reranker unavailable)"` when the fallback ran.

## `kb_get_source`

**Input**

| Field | Type | Required | Meaning |
|---|---|---|---|
| `ref` | string | yes | `<source>:<source_id>` from a `kb_search` passage |

**Output** (JSON text content)

```json
{
  "ref": "drive:1AbC...",
  "title": "Team topologies notes",
  "source": "drive",
  "label": "Drive",
  "url": "https://docs.google.com/document/d/1AbC.../edit",
  "modified": "2026-11-02T09:14:00Z",
  "status": "active",
  "locations": ["Leadership/Org design"],
  "text": "<store file body without front-matter>"
}
```

`status` is one of `active`, `stale`, `removed`. Reads only item records and the store
file; it does not need LightRAG.

## Errors

Returned as MCP tool errors (`isError: true`) with one text line:

| Condition | Text |
|---|---|
| Knowledge-base server stopped (both tools; `kb_get_source` only if it needs the server) | `LightRAG server is not running at http://127.0.0.1:<PORT>. Start it with ./scripts/start.sh` |
| Unknown `ref` | `no source with ref <ref>` |
| Bad `filter` value | `filter must be one of reranker, local, none` |
