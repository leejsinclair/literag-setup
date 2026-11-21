# Feature Specification: Personal knowledge pipeline (`kb-sync`)

**Feature Branch**: `002-kb-sync-knowledge-pipeline`
**Created**: 2026-11-21
**Status**: Draft
**Input**: "Personal knowledge base: architecture and build plan", draft 4, 21 November 2026, with the owner's decisions in its section 6.

## Overview

The knowledge base from feature 001 answers questions, but only over files someone copies into it by hand. The owner's notes live in AppFlowy and Google Drive, research is staged in NotebookLM, and essays are written with Claude. This feature connects them. A sync tool keeps one local store of Markdown in step with chosen Drive folders and AppFlowy pages. The knowledge base answers from that store with links back to the original page. Topics can be pushed to NotebookLM as curated research packs, and the packs are reviewed and pruned on request.

It is built in phases, each usable on its own. The stories below follow that order.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Keep chosen Drive folders in the knowledge base (Priority: P1)

The owner lists one or more Drive folders by ID. Running a pull copies every supported file in those folders and their subfolders into the local Markdown store, each stamped with its title, origin and link. Running ingest brings the knowledge base up to date. When a Drive doc is edited, the next pull and ingest replace the old version's knowledge. When it is deleted or trashed, its knowledge is removed. Files nobody chose, elsewhere in Drive, are never read. The first corpus is the **Leadership** folder.

**Why this priority**: Drive has the clearest change feed, so it proves the store, the record of what came from where, and the replace and delete logic on the easiest source. Every later story depends on these.

**Independent Test**: Point the tool at the Leadership folder, pull and ingest, then ask a question only one of its documents answers. Edit that doc, re-run, and see the answer change. Delete it, re-run, and see the knowledge base no longer answer from it.

**Acceptance Scenarios**:

1. **Given** a configured Drive folder and a signed-in Google account, **When** the owner runs a pull, **Then** each supported file in the folder tree appears once in the store with its title, origin, link and last-modified time, and files outside the folder tree are untouched.
2. **Given** a pulled and ingested Drive doc, **When** it is edited in Drive and the owner pulls and ingests again, **Then** answers reflect the new text and no longer reflect removed text.
3. **Given** a pulled and ingested Drive file, **When** it is deleted or trashed in Drive and the owner pulls and ingests again, **Then** its knowledge is removed, a backup was taken first, and the removal is logged.
4. **Given** the same PDF sits in two configured subfolders, **When** the owner pulls, **Then** it is ingested once and both folder locations are recorded against it.
5. **Given** a folder holding audio, video, images and a scanned (image-only) PDF, **When** the owner pulls and ingests, **Then** the media files are skipped and listed as skipped, and the scanned PDF is reported as failed, never silently dropped.
6. **Given** a spreadsheet in a configured folder, **When** the owner pulls, **Then** its sheets become Markdown tables in the store and are ingested.
7. **Given** nothing changed in Drive since the last run, **When** the owner pulls and ingests, **Then** no document is re-processed.

---

### User Story 2 - Ask a question and get a cited answer (Priority: P1)

The owner asks a question on the command line. The knowledge base retrieves matching passages without writing an answer. A local filter drops the passages that do not bear on the question. Claude then writes the answer from the surviving passages only, citing each claim. Every answer ends with a numbered source list whose links open the original Drive doc or AppFlowy page. If nothing relevant survives, the tool says so without calling Claude.

**Why this priority**: This is the part used daily, and the citation format it settles shapes the MCP tools, the research packs and the essay workflow.

**Independent Test**: With the Leadership folder ingested, run a set of about 20 questions with known answers plus a few outside the corpus. Check each answer's links open the right source, and that the outside questions get "not in the knowledge base".

**Acceptance Scenarios**:

1. **Given** an ingested corpus, **When** the owner asks a question it covers, **Then** the answer cites each claim with `[n]` and lists every cited source as `[n] Title (AppFlowy|Drive) link`, with each link opening the original item.
2. **Given** a question the corpus does not cover, **When** the owner asks it, **Then** the answer is "not in the knowledge base", Claude is not called, and nothing is filled in from general knowledge.
3. **Given** any question, **When** the answer stage runs, **Then** only the filtered passages and the question leave the machine.
4. **Given** the `--local` switch, **When** the owner asks a question, **Then** the local model writes the answer and no text leaves the machine.
5. **Given** the `--no-filter` switch, **When** the owner asks a question, **Then** the unfiltered retrieved context goes to the answer stage.
6. **Given** the preferred local filter is unavailable, **When** the owner asks a question, **Then** the fallback filter is used and the answer says which filter ran.

---

### User Story 3 - Query the knowledge base from Claude Code (Priority: P2)

While researching or drafting on this machine, the owner's Claude Code session can search the knowledge base and fetch a source's details through two local tools. Results carry the same citations as the command line, so drafts in `leesinclair.dev` can point to where the evidence is.

**Why this priority**: It reuses Story 2's retrieval and citations, and it is how the essay workflow consumes the knowledge base. It adds no new knowledge, so it follows Story 2.

**Independent Test**: In a Claude Code session on this machine, ask it to search the knowledge base for a Leadership-folder fact and open the source it cites.

**Acceptance Scenarios**:

1. **Given** the local tools are registered with Claude Code, **When** Claude searches the knowledge base, **Then** it receives filtered passages each labelled with its source title, origin and link.
2. **Given** a source reference from a search, **When** Claude asks for that source, **Then** it receives the source's title, origin, link, last-modified time and stored text.
3. **Given** the knowledge base server is stopped, **When** Claude calls either tool, **Then** the tool returns an error naming the stopped server and how to start it.

---

### User Story 4 - Bring AppFlowy notes into the knowledge base (Priority: P2)

The owner signs in to the hosted AppFlowy Cloud once. A pull walks the chosen workspace's page tree, converts each document page to Markdown and each database to Markdown, and stores them with links back to the page. Edits and deletions in AppFlowy flow through on the next pull and ingest, as for Drive.

**Why this priority**: AppFlowy holds the owner's own notes, but its API is less documented and its page format is internal, so it comes after the record-keeping is proven on Drive.

**Independent Test**: Pull a workspace, ingest, and ask a question only an AppFlowy note answers. The cited link opens that page.

**Acceptance Scenarios**:

1. **Given** a signed-in AppFlowy account and a chosen workspace, **When** the owner pulls, **Then** each document page appears in the store as Markdown keeping its headings, paragraphs, lists, to-dos, code, quotes, links and tables.
2. **Given** a database in the workspace, **When** the owner pulls, **Then** it is stored as Markdown: one table per database, or one file per row for databases the owner marks as notes.
3. **Given** a page edited since the last pull, **When** the owner pulls and ingests, **Then** answers reflect the edit.
4. **Given** a page moved to trash, **When** the owner pulls and ingests, **Then** its knowledge is removed as in Story 1.
5. **Given** a block type the converter does not recognise, **When** the page is converted, **Then** the block's text is kept as plain text and the block type is reported.

---

### User Story 5 - Build a research pack and push it to NotebookLM (Priority: P3)

For a topic, the owner builds a research pack: a reviewable list of knowledge-base items, chosen with Claude's help, plus any public web sources. Pushing the pack creates the topic's notebook if needed and adds each item as a source. The owner generates the report, podcast and infographic in NotebookLM by hand. Outputs saved back to Drive are recognised as NotebookLM outputs on the next pull, so the report becomes searchable and citable.

**Why this priority**: It depends on Stories 1 and 2, and the NotebookLM API is a pre-release preview that cannot yet generate outputs, so much of this loop stays manual.

**Independent Test**: Build a pack for one topic, push it, see the notebook and its sources in NotebookLM, save the generated report to Drive, pull and ingest, and get an answer citing the report.

**Acceptance Scenarios**:

1. **Given** a topic, **When** the owner builds a pack, **Then** a readable, editable pack file lists each item's title, origin and link.
2. **Given** a pack, **When** the owner pushes it, **Then** the notebook exists, each item is a source in it, and the tool records which notebook source came from which item.
3. **Given** a pack larger than the notebook's source limit, **When** the owner pushes it, **Then** nothing beyond the limit is pushed and the tool lists what was left out.
4. **Given** a pushed item that changed since the push, **When** the owner pushes again, **Then** the changed item is re-added in its new version.
5. **Given** a NotebookLM report saved in a configured Drive folder next to its sources, **When** the owner pulls and ingests, **Then** it is tagged as a NotebookLM output and cited like any other source.
6. **Given** a NotebookLM podcast or video saved to Drive, **When** the owner pulls, **Then** it is recorded but not ingested.
7. **Given** the `research/` folder of `leesinclair.dev` is configured as a source, **When** the owner pulls and ingests, **Then** past research is searchable and cited.

---

### User Story 6 - Review and prune a topic's notebook (Priority: P3)

On request, or monthly, the owner runs a review for a topic. It writes a proposal listing what to drop, re-push or add, each with its reason. The owner edits or approves the proposal, then applies it. Applying updates the notebook and the tool's records. Nothing is dropped without that approval.

**Why this priority**: Pruning only matters once notebooks exist and age.

**Independent Test**: For a pushed topic, delete one source upstream, edit another, and add a new matching item. Run the review and see all three proposed with reasons. Apply and see the notebook change to match.

**Acceptance Scenarios**:

1. **Given** a pushed topic, **When** the owner runs a review, **Then** the proposal lists: sources changed since push (re-push), deleted or trashed upstream (drop), web sources that no longer resolve (drop or replace), untouched for the configured number of months and never cited (candidate drop), near-duplicates (candidate drop), and new matching items (candidate add).
2. **Given** a proposal, **When** the owner applies it unedited, **Then** every proposed change is made in the notebook and in the tool's records.
3. **Given** a proposal the owner edited, **When** they apply it, **Then** only the edited proposal's changes are made.
4. **Given** NotebookLM offers no way to remove a source, **When** a drop is applied, **Then** the tool records it as pending and tells the owner which sources to remove by hand.
5. **Given** adds would exceed the notebook's source limit, **When** the owner applies, **Then** the tool refuses the excess and says how many slots are free.

---

### User Story 7 - Unattended nightly sync (Priority: P3)

Once Stories 1 and 4 are trusted, a nightly job pulls and ingests on its own, outside working hours. The owner sees each run's result without watching it.

**Why this priority**: Manual runs serve until the owner trusts the delete logic. Automation comes last.

**Independent Test**: Enable the nightly job, edit a Drive doc during the day, and next morning see the change answered and the run's summary.

**Acceptance Scenarios**:

1. **Given** the nightly job is enabled, **When** it runs, **Then** it pulls all configured sources, ingests, and writes a summary of added, replaced, removed, skipped and failed items.
2. **Given** a run would remove more documents than the configured safety limit, **When** the job runs, **Then** it removes none of them, ingests everything else, and reports the held removals for the owner to confirm.
3. **Given** a source's sign-in has expired, **When** the job runs, **Then** the other sources still sync and the summary names the expired sign-in and the command to renew it.

### Edge Cases

- **Empty source.** A configured folder or workspace with no supported files syncs nothing and says so; it is not an error.
- **Folder removed from config.** Items that came only from that folder are proposed for removal and held for the owner's confirmation; they are not removed silently.
- **Moved or renamed upstream.** An item keeps its identity and its store file; only its title, link or folder locations change.
- **Sync interrupted.** A pull or ingest stopped mid-run leaves the store and the record of what came from where consistent; the next run completes the work without duplicates.
- **Oversized Google Doc.** A doc too large for Drive's export is reported as failed with its size and title. The 9.9 MB "A Primer on Cultivating High-Performing Engineering Teams" is the first test.
- **Duplicate content.** Byte-identical files are ingested once wherever they appear.
- **Knowledge base server stopped.** Pull still works; ingest, ask and the Claude Code tools fail with a message naming the stopped server and how to start it.
- **Restore.** After the owner restores a backup, the tool's records match the restored knowledge base, because both are in the same backup.
- **Answer stage offline.** If Claude cannot be reached, `ask` reports that and suggests `--local`; it never falls back to sending unfiltered context.
- **Expired sign-in.** Google's testing-mode refresh tokens may expire after about seven days. The tool detects it and names the renew command.
- **Pre-release APIs change.** A NotebookLM or AppFlowy call that starts failing names the failing call; the knowledge base and Drive sync keep working.

## Requirements *(mandatory)*

### Functional Requirements

**Governance and safety**

- **FR-001**: Before any feature code is merged, the project constitution MUST be amended (to v1.1.0) to record each cloud dependency with its justification (Google Drive, AppFlowy Cloud, the consumer NotebookLM API, Claude), to bring Claude Code and MCP integration into scope for this feature, and to permit the source-driven knowledge removals in FR-010. The README's "does not do" list MUST be updated to match.
- **FR-002**: The sync tool MUST talk to the knowledge base only through its existing REST API; the knowledge base server MUST need no change except configuration.
- **FR-003**: The sync tool MUST run containerised in the existing Compose project and MUST NOT start when the knowledge base server starts.
- **FR-004**: Configuration (Drive folder IDs, AppFlowy workspace, Claude model, filter choice, safety limits, notebook source limit) MUST live in `.env`. Secrets (Google OAuth client and refresh token, AppFlowy credentials) MUST live in an ignored file readable only by the owner and MUST never be committed or logged.
- **FR-005**: The tool's record of what came from where MUST be stored under `data/kb-sync/` and MUST be included in every knowledge-base backup and restore.

**Sync and the Markdown store**

- **FR-006**: The tool MUST pull only from configured Drive folders and their subfolders, and MUST ignore changes anywhere else in Drive.
- **FR-007**: The tool MUST store each pulled item as one Markdown file at a stable path per source and item, headed with its title, origin, link and last-modified time.
- **FR-008**: The tool MUST keep one record per item holding its origin, origin ID, title, link, last-modified time, content fingerprint, store path, knowledge-base document ID, topics, per-topic notebook source IDs, and status (`active`, `stale`, `removed`).
- **FR-009**: The tool MUST detect changes from each source's change feed or modification times, and MUST re-process only items whose content changed.
- **FR-010**: When an item changes, the tool MUST remove its old version from the knowledge base before ingesting the new one. When an item is deleted or trashed upstream, the tool MUST remove it. Every removal MUST be preceded by a knowledge-base backup in the same run and MUST be logged with the item's title, origin and reason.
- **FR-011**: The tool MUST hold, not perform, removals that exceed a configurable safety limit in one run, and MUST report them for the owner's confirmation.
- **FR-012**: The tool MUST export Google Docs as text, download PDFs, and convert spreadsheets to Markdown tables. It MUST skip audio, video and images and list them as skipped. It MUST report any file that fails to export or ingest, with its title and the reason.
- **FR-013**: The tool MUST ingest byte-identical files once and record every folder each appears in.
- **FR-014**: The tool MUST tag files matching NotebookLM output naming as NotebookLM outputs, keep them alongside their sources, ingest text outputs, and record but not ingest audio and video outputs.
- **FR-015**: The tool MUST sign in to hosted AppFlowy Cloud, walk a chosen workspace's page tree, and convert document pages to Markdown preserving headings, paragraphs, lists, to-dos, code, quotes, links and tables. Unknown block types MUST keep their text and be reported.
- **FR-016**: The tool MUST store each AppFlowy database as one Markdown table, or as one file per row for databases the owner marks as notes.
- **FR-017**: The tool SHOULD accept a local folder (the `leesinclair.dev` `research/` folder) as a source.
- **FR-018**: The tool MUST provide a sign-in command per cloud source that the owner can re-run to renew an expired sign-in, and MUST name that command whenever a sign-in has expired.

**Answers**

- **FR-019**: `ask` MUST retrieve context from the knowledge base without generating an answer there, filter it locally, and send only the question and the surviving passages to Claude.
- **FR-020**: The preferred filter MUST be a dedicated local reranker configured inside the knowledge base with a minimum relevance score; when it is unavailable, the tool MUST fall back to scoring each passage with the local model and keeping the top passages. The answer MUST say which filter ran.
- **FR-021**: Each surviving passage MUST keep its source reference, and every answer MUST list its sources as `[n] Title (AppFlowy|Drive) link`, resolved through the tool's records.
- **FR-022**: Claude MUST be instructed to answer only from the passages and cite each claim. When no passage survives the filter, the tool MUST answer "not in the knowledge base" without calling Claude.
- **FR-023**: `ask` MUST support `--local` (the local model answers; nothing leaves the machine) and `--no-filter` (the unfiltered context goes to the answer stage).
- **FR-024**: Two local tools for Claude Code on this machine MUST be provided: `kb_search` returns filtered, cited passages for a query, and `kb_get_source` returns a source's details and stored text.
- **FR-025**: A test set of about 20 questions with known answers MUST be runnable with no filter, the reranker, and local-model scoring, reporting for each whether the evidence the answer needs survived.

**NotebookLM and pruning**

- **FR-026**: The owner MUST be able to build a research pack per topic as a readable, editable file listing knowledge-base items and public web sources.
- **FR-027**: Pushing a pack MUST create the topic's notebook if absent, add each item as a source (Markdown text, PDF upload, or web URL), record each notebook source ID against its item, and never exceed the configured notebook source limit.
- **FR-028**: A review MUST write a proposal file of drops, re-pushes and adds, each with its reason, from the signals in Story 6.
- **FR-029**: Applying a review MUST carry out only the approved proposal. Drops NotebookLM cannot perform MUST be recorded as pending and listed for manual removal.
- **FR-030**: The tool SHOULD optionally add candidate web sources from a short Claude web search on the topic to a review proposal.

**Operation**

- **FR-031**: Each command MUST be reachable through a wrapper script in the repository's existing style, with its uniform exit codes, actionable error messages, and progress on stderr.
- **FR-032**: A nightly job MUST be available that pulls and ingests every configured source, isolates a failure in one source from the others, and writes a run summary of added, replaced, removed, held, skipped and failed items.

### Key Entities

- **Source**: a place items come from: a Drive folder allow-list, an AppFlowy workspace, a local folder, NotebookLM outputs, or the web. Holds its sign-in and change-tracking position.
- **Item record**: one per pulled item. Ties the upstream original (origin, ID, title, link, modified time, fingerprint, folder locations) to its store file, its knowledge-base document, the topics that include it, and its notebook sources. Has a status.
- **Store file**: the Markdown copy of one item, at a stable path, headed with its provenance. The knowledge base ingests these.
- **Research pack**: a topic's list of items and web sources, owner-editable. Linked to one notebook.
- **Notebook**: a NotebookLM notebook for one topic, with a source limit. Its sources map back to item records.
- **Review proposal**: a topic's proposed drops, re-pushes and adds, each with a reason. Applied only after approval.
- **Run log**: what one pull, ingest or nightly run did, including every removal and every held removal.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a Drive doc is edited, one pull and ingest is enough for answers to reflect the edit, in 100% of a 5-edit test.
- **SC-002**: After a Drive file is deleted, one pull and ingest is enough for no answer to cite it, in 100% of a 3-deletion test.
- **SC-003**: The first full ingest of the Leadership folder (44 files) completes unattended within one overnight run of 12 hours, with every file accounted for as ingested, skipped or failed.
- **SC-004**: A pull and ingest with no upstream changes re-processes zero documents and finishes in under 5 minutes.
- **SC-005**: 100% of cited links in the test-set answers open the original item.
- **SC-006**: 100% of out-of-corpus test questions are answered "not in the knowledge base" without a call to Claude.
- **SC-007**: The chosen filter keeps the needed evidence for at least 18 of the 20 test questions.
- **SC-008**: Zero knowledge-base removals happen without a backup taken earlier in the same run, verified from the run logs.
- **SC-009**: With `--local`, no network traffic leaves the machine during an answer.
- **SC-010**: Renewing an expired sign-in takes the owner under 2 minutes with one command.
- **SC-011**: A topic goes from a research pack to a populated notebook in one command, and its saved report is citable after the next pull and ingest.
- **SC-012**: A review of a topic with one deleted, one edited and one new matching item proposes all three, each with the correct reason.

## Assumptions

- The phases run in the order of the stories: constitution amendment and checks first, then Stories 1, 2 and 3, then 4, then 5, then 6 and 7.
- Phase 0 checks are prerequisites, not deliverables: how the knowledge base's query endpoint returns references in v1.5.6, which local reranker server runs most simply on this machine (likely on the CPU, since the 6 GB GPU is nearly full), and the hosted AppFlowy Cloud base URL and sign-in method.
- The reranker is a new local service. The constitution amendment records it under Principle IX, justified by the 3B model's weak relevance judgement.
- Everything runs on the owner's EndeavourOS desktop, next to the knowledge base and Ollama. A cloud Claude session cannot reach the loopback-only server.
- NotebookLM is used through the consumer API on the owner's personal Google account. Output generation stays manual until Google exposes it. The notebook source limit defaults to 50 (the free plan) until the owner confirms their plan.
- The consumer NotebookLM API's ability to remove a source is unconfirmed; Story 6 degrades to manual removal until it is.
- The default removal safety limit is 10 documents per run; above it, removals are held.
- The default answer filter keeps passages at or above a reranker score of 0.6, as LightRAG's example suggests for a weak answering model; the fallback keeps the top 8 passages scored 0 to 3. Both are tuned from the FR-025 test results.
- The candidate-drop age in reviews defaults to 12 months.
- The owner's AppFlowy page count and database use are still unknown; both document pages and databases are supported.
- Scanned PDFs are out of scope: there is no OCR. Audio and video transcription is out of scope.
- Citation checking against primary sources in the essay workflow stays a human step; the knowledge base points to evidence, it does not replace reading it.
- Google Drive access scope: [NEEDS CLARIFICATION: read-only access to all of Drive with the OAuth client kept in testing mode (simple, but Google classes it as restricted and sign-ins may lapse weekly), or per-file access granted by picking the KB folders once in a Google picker (narrower, but needs a one-time browser step)?]

## Verbatim Constraints

- Tool and commands: `kb-sync`, `kb-sync auth google|appflowy`, `kb-sync pull [--source drive|appflowy]`, `kb-sync ingest`, `kb-sync pack <topic>`, `kb-sync notebook push <topic>`, `kb-sync review <topic>`, `kb-sync review <topic> --apply`, `kb-sync ask "question"`, wrapper `scripts/kb.sh`
- Ask switches: `--local`, `--no-filter`
- Claude Code tools: `kb_search`, `kb_get_source`
- Citation line: `[n] Title (AppFlowy|Drive) link`
- No-answer text: "not in the knowledge base"
- Locations: tool source `sync/`; records `data/kb-sync/`; store files `data/inputs/<source>/<source_id>.md`; packs `packs/<topic>.yaml`
- Source values: `drive`, `appflowy`, `notebook-output`, `web`
- Status values: `active`, `stale`, `removed`
- Store file front-matter keys: `title`, `source`, `url`, `modified`
- First corpus Drive folder: Leadership, ID `1qIMoMk98MsRmIJvLsfqo5Pv7f7aKlKRk`
- Retrieval: `POST /query` with `mode: "mix"` and `only_need_context: true`; removal: `DELETE /documents/{doc_id}`; ingest: `POST /documents/scan`
- Reranker: `BAAI/bge-reranker-v2-m3`, `RERANK_BINDING=cohere`, `MIN_RERANK_SCORE`
- Constitution version after amendment: v1.1.0
