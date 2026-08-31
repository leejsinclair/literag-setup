# Model selection

All model configuration is in `.env` (Principle IV, FR-024). Nothing model-specific
is baked into `compose.yaml` or the scripts.

## The tested combination

| Role | `.env` key | Default | Why |
|------|-----------|---------|-----|
| Query + extraction LLM | `LLM_MODEL` | `qwen2.5:3b-instruct` | 3B instruct model, follows LightRAG's structured extraction prompts well. Fits entirely in 6 GB of VRAM alongside the embedding model. Alt: `llama3.2:3b`. |
| Embedding | `EMBEDDING_MODEL` | `bge-m3:latest` | Strong multilingual embeddings, still small. Alt: `nomic-embed-text` (lighter, `EMBEDDING_DIM=768`). |
| Embedding dimension | `EMBEDDING_DIM` | `1024` | **Must equal the embedding model's native dimension.** `bge-m3` = 1024. |

The stack runs on CPU alone, but the reference machine has an **NVIDIA GTX 1660 Ti
(6 GB)** and the shipped `.env` + `ollama/ollama.service` are tuned to use it. See
"Performance tuning" below. If you move to a box with no GPU, restore the previous
CPU-only profile from git history (`git log -p .env`).

## Performance tuning

The single biggest factor is **where the LLM runs**. Left to its own devices at a
large context, Ollama splits `qwen2.5:3b` ~9 % CPU / 91 % GPU, which stalls the
whole pipeline — check `ollama ps`, the `PROCESSOR` column must read `100% GPU`.

### Ollama side — `ollama/ollama.service`

A tuned **systemd user unit** (install with `./scripts/apply-perf-tuning.sh`, or by
hand per the header comment in that file):

| Env | Value | Note |
|-----|-------|------|
| `OLLAMA_FLASH_ATTENTION` | `1` | Halves KV-cache compute/memory on Turing+. |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | Quantised KV cache — the headroom that lets two models + parallel slots fit in 6 GB. |
| `OLLAMA_KEEP_ALIVE` | `30m` | Ingestion alternates LLM ↔ embedding; keep both hot. |
| `OLLAMA_MAX_LOADED_MODELS` | `2` | `qwen2.5:3b` + `bge-m3` resident together. |
| `OLLAMA_NUM_PARALLEL` | `1` | **Must be 1 on a 6 GB card.** At `2`, Ollama reserves a second KV slot per model, the LLM + embedding pair no longer fits, and the two models evict-and-reload each other on every ingest phase switch — an embedding batch then waits behind a full reload and trips LightRAG's embedding-worker timeout (2× `EMBEDDING_TIMEOUT`; 60 s at the default `EMBEDDING_TIMEOUT=30`), halting the pipeline. |

### LightRAG side — `.env`

| Key | Value | Was | Note |
|-----|-------|-----|------|
| `OLLAMA_LLM_NUM_GPU` / `OLLAMA_EMBEDDING_NUM_GPU` | `99` | unset | Force **all** layers onto the GPU. Removes the CPU/GPU split. |
| `OLLAMA_LLM_NUM_CTX` | `12288` | `32768` | Covers the trimmed query context (`MAX_TOTAL_TOKENS` 10000 + prompt) and extraction. Largest value at which the LLM and `bge-m3` both stay resident on the 6 GB card. Don't raise without lowering something else. |
| `OLLAMA_EMBEDDING_NUM_CTX` | `2048` | `8192` | Longest embedded text is a merge summary capped at `SUMMARY_MAX_TOKENS=1000`. The rest was wasted VRAM. |
| `OLLAMA_LLM_NUM_PREDICT` | `3072` | `8192` | Bounds a runaway extraction call to ~40 s; still ample for an answer. |
| `OLLAMA_LLM_REPEAT_PENALTY` | `1.15` | `1.1` | Nudges qwen out of the extraction repetition loop. `1.3` was too high — it made the model drop fields ("found 4/5 fields on RELATION"). |
| `MAX_ASYNC_LLM` / `MAX_PARALLEL_INSERT` | `2` / `1` | `4` / `3` (upstream) | Ollama serves 1 request per model (`NUM_PARALLEL=1`); more just deepens the queue. |
| `EMBEDDING_BATCH_NUM` / `EMBEDDING_FUNC_MAX_ASYNC` | `10` / `2` | `10` / `8` | A 32-text batch of long merge summaries was the exact call that hit the worker timeout. |
| `EMBEDDING_TIMEOUT` | `120` | `30` | LightRAG kills an embedding batch at 2× this. Raised so a transient slow batch doesn't fail the document. |
| `ENABLE_LLM_CACHE` | `true` | `false` | Repeated queries / re-ingests skip the model. |

**Query latency** (the answer LLM prompt-processes the whole assembled context first —
~300 tok/s on this card, so the default 30 000-token budget alone costs ~90–110 s):

| Key | Value | Default | |
|-----|-------|---------|--|
| `MAX_TOTAL_TOKENS` | `10000` | `30000` | The dominant knob. |
| `MAX_ENTITY_TOKENS` / `MAX_RELATION_TOKENS` | `2500` / `3000` | `6000` / `8000` | KG context blocks. |
| `TOP_K` / `CHUNK_TOP_K` / `RELATED_CHUNK_NUMBER` | `20` / `8` / `3` | `40` / `20` / `5` | Retrieval breadth. |

For simple factual questions also pass `"mode":"local"` (or `"naive"`) — far cheaper
than the `hybrid` default; `mix` is the slowest.

**Ingestion depth** (aggressive-speed profile — trades some graph recall):

| Key | Value | Default | |
|-----|-------|---------|--|
| `MAX_GLEANING` | `0` | `1` | Drops the second extraction pass (~half the LLM calls). |
| `CHUNK_SIZE` / `CHUNK_OVERLAP_SIZE` | `2000` / `200` | `1200` / `100` | Fewer, larger chunks. |
| `FORCE_LLM_SUMMARY_ON_MERGE` / `SUMMARY_MAX_TOKENS` | `12` / `1000` | `8` / `1200` | Fewer / shorter merge summaries. |

If graph quality drops too far, step back: `MAX_GLEANING=1`, `CHUNK_SIZE=1600`.

### If extraction is still slow or documents get stuck

`qwen2.5:3b-instruct` handled the fixtures in ~30–60 s/document on CPU alone; on the
GPU it is markedly faster. On a dense document it can still be slow. Options, in order:

1. Lower `OLLAMA_LLM_NUM_PREDICT` to `2048`.
2. Confirm `ollama ps` shows `100% GPU` (not a split).
3. Try `llama3.2:3b` as `LLM_MODEL` — less prone to the extraction repetition loop.
4. Move to a 7B–8B model (`qwen2.5:7b-instruct`, `llama3.1:8b`) — more reliable at
   structured extraction but will spill off a 6 GB card. `.env` edit + `restart.sh`,
   no re-index (query and extraction LLM are the same key).

### If embeddings are the bottleneck (not extraction)

`bge-m3` is a large embedding model (~0.7 s per short text on this GPU). If
`nvidia-smi dmon` shows the embedding phase dominating ingestion, switch to
`nomic-embed-text` (already pulled, 768-d, ~5–10× faster) — but this **requires a full
re-index** (`EMBEDDING_MODEL` + `EMBEDDING_DIM=768`, wipe `data/rag_storage/`,
re-ingest) and costs some multilingual/retrieval quality. See below.

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
