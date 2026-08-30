# Model selection

All model configuration is in `.env` (Principle IV, FR-024). Nothing model-specific
is baked into `compose.yaml` or the scripts.

## The tested CPU combination

| Role | `.env` key | Default | Why |
|------|-----------|---------|-----|
| Query + extraction LLM | `LLM_MODEL` | `qwen2.5:3b-instruct` | 3B instruct model runs on CPU with acceptable single-user latency and follows LightRAG's structured extraction prompts well. Alt: `llama3.2:3b`. |
| Embedding | `EMBEDDING_MODEL` | `bge-m3:latest` | Strong multilingual embeddings, still small. Alt: `nomic-embed-text` (lighter, `EMBEDDING_DIM=768`). |
| Embedding dimension | `EMBEDDING_DIM` | `1024` | **Must equal the embedding model's native dimension.** `bge-m3` = 1024. |

Supporting tuning (CPU-conservative; upstream defaults are higher):

| Key | Value | Note |
|-----|-------|------|
| `OLLAMA_LLM_NUM_CTX` | `32768` | LightRAG needs a large context for entity/relation extraction; keep ≥ 32768 or extraction silently truncates. |
| `OLLAMA_LLM_NUM_PREDICT` | `8192` | **Required with small models.** Caps generation length. Without it, `qwen2.5:3b` can enter a repetition loop on LightRAG's extraction prompt and generate for 10+ minutes until the call times out and the document **fails**. Verified on the target machine. A healthy extraction uses far less; this just bounds a runaway (~90s/call). |
| `LLM_TIMEOUT` | `1200` | CPU inference is slow; the upstream default (240s) is too tight. 1200s covers a large document's extraction even with the cap above. |
| `MAX_ASYNC_LLM` | `2` | Bound concurrent LLM calls (upstream default 4). |
| `MAX_PARALLEL_INSERT` | `1` | One document through the extraction stage at a time (upstream default 3). |
| `OLLAMA_EMBEDDING_NUM_CTX` | `8192` | Embedding context window. |

### If extraction is still slow or documents get stuck

`qwen2.5:3b-instruct` handles the tested fixtures in ~30–60s per document on an
8-core CPU. On a dense document it can still be slow. Options, in order:

1. Lower `OLLAMA_LLM_NUM_PREDICT` to `4096`.
2. Try `llama3.2:3b` as `LLM_MODEL`.
3. Move to a 7B–8B model (`qwen2.5:7b-instruct`, `llama3.1:8b`) — markedly more
   reliable at structured extraction, ~2–4× slower per call. Still CPU-only, no
   GPU. This is a `.env` edit + `restart.sh`, no re-index (the query LLM and the
   extraction LLM are the same key; already-ingested content is not reprocessed).

You need GPU for none of this (SC-012). Larger models are explicitly *not* the
target — a 7B+ LLM is too slow for interactive CPU use.

---

## Change the query LLM — no re-index

The query/extraction LLM is **not** baked into the stored knowledge base.

```bash
$EDITOR .env            # set LLM_MODEL=<new-ollama-model>
ollama pull <new-ollama-model>
./scripts/restart.sh
./scripts/health.sh     # confirms the new model is reachable
```

Existing graph, index and ledger are untouched; queries are answered by the new
model immediately (FR-023, SC-009). Note: the extraction LLM is the same key, so
*future* ingestion will use the new model — but already-ingested content is not
reprocessed.

---

## Change the embedding model — re-index required

Stored vectors are specific to the embedding model **and** its dimension.
Changing `EMBEDDING_MODEL` invalidates the retrieval index (FR-025).

```bash
$EDITOR .env            # set EMBEDDING_MODEL=<new>  AND  EMBEDDING_DIM=<its native dim>
ollama pull <new>
./scripts/restart.sh
./scripts/health.sh     # check 5 verifies the probe vector length == EMBEDDING_DIM
```

Then **re-index** (see `docs/OPERATIONS.md` → "Re-index after an embedding-model
change"): re-run `./scripts/ingest.sh` against the existing `data/inputs/`. The
source documents are reused — you do not re-collect them — and the old vector
index is superseded.

### The `EMBEDDING_DIM` gotcha (Invariant 3)

If `EMBEDDING_DIM` does not match the model's real output dimension, the vector
store is silently corrupted. `health.sh` embeds a probe string and fails loudly
if the returned length differs from `EMBEDDING_DIM`. `restore.sh` and `health.sh`
also compare against what the current index was last built with (a stamp in
`data/rag_storage/.literag-index.json`) and tell you when a re-index is due —
**stale results must not be presented as current.**

Common native dimensions: `bge-m3` = 1024, `nomic-embed-text` = 768,
`mxbai-embed-large` = 1024, `all-minilm` = 384. Verify with:

```bash
curl -s http://localhost:11434/api/embed -d '{"model":"<model>","input":"x"}' \
  | jq '.embeddings[0] | length'
```
