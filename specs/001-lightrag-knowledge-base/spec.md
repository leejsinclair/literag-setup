# Feature Specification: LightRAG Local Knowledge Base

**Feature Branch**: `001-lightrag-knowledge-base`

**Created**: 2026-08-30

**Status**: Draft

**Input**: User description: "Create a local, single-user knowledge base using LightRAG. The goal is to have a reliable local service that can ingest a collection of personal and technical documents, build a searchable knowledge graph and retrieval index from them, and allow me to query that knowledge through a Web UI and API. [...] The initial implementation should establish a reliable LightRAG knowledge-base service."

## Clarifications

### Session 2026-08-30

- Q: Which document formats must the knowledge base be able to ingest at launch? → A: Plain text, Markdown, and PDF.
- Q: How should ingestion of documents in the input directory be triggered? → A: An explicit owner-run command that scans the input directory on demand; the Web UI upload panel is also available. No background file-watcher and no automatic scan on startup.
- Q: Roughly how large is the document collection the system needs to handle well at launch? → A: Up to a few hundred documents (hundreds). The initial build should use LightRAG's default lightweight file-based storage; an external database is not introduced unless a demonstrated need arises.
- Q: Should the local API require an authentication token, or rely only on being bound to localhost? → A: No authentication; access control is provided solely by binding the Web UI and API to the local loopback interface.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Build and query a personal knowledge base (Priority: P1)

The owner places a set of personal and technical documents into a designated local
input directory, triggers ingestion, and then asks natural-language questions through
the Web UI. Answers are grounded in the content of the ingested documents, and the
owner can see which sources informed each answer.

**Why this priority**: This is the core reason the system exists. Without ingest-then-query,
nothing else has value. It is the minimum viable product on its own.

**Independent Test**: Place one known document in the input directory, run ingestion,
open the Web UI, ask a question whose answer only appears in that document, and confirm
the response reflects the document's content.

**Acceptance Scenarios**:

1. **Given** the service is running and the input directory is empty, **When** the owner adds a supported document and starts ingestion, **Then** the document is processed and becomes queryable, and the owner receives feedback that ingestion completed.
2. **Given** a document has been ingested, **When** the owner asks a question in the Web UI whose answer is contained in that document, **Then** the system returns an answer derived from that document and indicates the contributing source(s).
3. **Given** a document contains no information relevant to a question, **When** the owner asks that question, **Then** the system responds without fabricating document-sourced content and makes the absence of supporting material clear.
4. **Given** an unsupported file type is placed in the input directory, **When** ingestion runs, **Then** that file is skipped and reported as unsupported without aborting the rest of the batch.

---

### User Story 2 - Knowledge base survives restarts and updates (Priority: P1)

The owner restarts the machine, restarts the service, or applies an application/image
update. After the operation, the previously built knowledge graph, retrieval indexes,
and document metadata are intact and immediately queryable with no re-ingestion.

**Why this priority**: Reliability is the top operational goal. A knowledge base that
must be rebuilt after every restart is not usable as a personal tool, and accidental
data loss on update is the single worst failure mode.

**Independent Test**: Ingest a document, confirm it is queryable, stop and recreate the
service (including replacing the application image), then query again and confirm the
same answer is returned without re-ingesting.

**Acceptance Scenarios**:

1. **Given** documents have been ingested, **When** the service is stopped and started again, **Then** all prior knowledge-base content is queryable without reprocessing.
2. **Given** documents have been ingested, **When** the application is updated to a new version and recreated, **Then** the knowledge base is preserved and queryable.
3. **Given** a routine operational command is run (start, stop, restart, ingest, health check), **When** it completes, **Then** no persistent knowledge-base data is deleted or overwritten as a side effect.
4. **Given** an operation would destroy or replace persistent data, **When** the owner initiates it, **Then** the system requires explicit confirmation before proceeding.

---

### User Story 3 - Back up and restore the knowledge base (Priority: P2)

Before a significant upgrade, or on a routine schedule, the owner creates a backup of
the entire knowledge base with a single documented command. If the knowledge base is
later lost or corrupted, the owner restores it from a backup and returns to the exact
prior state.

**Why this priority**: Easy recovery is an explicit top-tier goal. Backups are only
valuable if restore is proven to work, so both halves must be delivered together.

**Independent Test**: Ingest documents, take a backup, delete or corrupt the live
knowledge-base data, run the restore procedure, and confirm queries return the same
results as before deletion.

**Acceptance Scenarios**:

1. **Given** a populated knowledge base, **When** the owner runs the backup procedure, **Then** a single restorable artifact (or clearly identified set) is produced in a known location and the command reports success.
2. **Given** a backup exists and the live knowledge base has been lost, **When** the owner runs the restore procedure, **Then** the knowledge base is returned to the state captured in that backup and is queryable.
3. **Given** a backup is being created, **When** the procedure runs, **Then** it does not silently omit any component required for a full restore (knowledge graph, indexes, metadata, ingestion tracking).

---

### User Story 4 - Query the knowledge base through the API (Priority: P2)

A local script or tool sends a query to the knowledge base over its local API and
receives a structured response suitable for programmatic use, without going through
the Web UI.

**Why this priority**: The API is the designated integration boundary for future AI
tooling. It must be reachable and usable from day one, even though no specific
integration is built now.

**Independent Test**: With a document ingested, send a query to the local API endpoint
from a command-line tool and confirm a structured answer referencing the document is
returned.

**Acceptance Scenarios**:

1. **Given** the service is running, **When** a local client calls the query API with a question, **Then** it receives a structured response containing the answer and source references.
2. **Given** the service is running, **When** a local client calls the API from another process on the same machine, **Then** the call succeeds over localhost without additional network configuration.
3. **Given** the service is running, **When** a client requests the API from outside the local machine, **Then** the request is not reachable.

---

### User Story 5 - Determine whether the system is healthy (Priority: P2)

The owner runs a single command (or opens a single page) and learns whether the
service and its dependencies are working: the application is responding, the language
and embedding models are reachable, and persistent storage is writable. When something
is wrong, the output names the failing component and the likely cause.

**Why this priority**: A system operated occasionally by one person must be diagnosable
without re-deriving how it works. Health visibility underpins every other operation.

**Independent Test**: Run the health check with everything working and confirm a clear
healthy result; stop the model service, run it again, and confirm the output identifies
the model dependency as the failure.

**Acceptance Scenarios**:

1. **Given** all components are working, **When** the owner runs the health check, **Then** it reports overall healthy status and confirms each checked dependency.
2. **Given** the model service is unreachable, **When** the owner runs the health check, **Then** it reports an unhealthy status and identifies the model service as the cause.
3. **Given** persistent storage is not writable, **When** the owner runs the health check, **Then** it reports the storage problem rather than a generic failure.
4. **Given** ingestion, indexing, or model inference fails during processing, **When** the owner reviews operational output, **Then** the failure is reported with enough detail to identify which document and which stage failed.

---

### User Story 6 - Change the language model without rebuilding the knowledge base (Priority: P3)

The owner switches the LLM used for answering queries (a different Ollama model, or a
different provider entirely) by changing configuration only. Existing knowledge-base
content remains valid and queryable. If the embedding model changes, the system makes
clear that re-indexing is required and provides a path to do it without losing source
documents.

**Why this priority**: Model quality and availability change quickly. Avoiding a
redesign when the model changes protects the whole investment, but it is not needed for
the first working system.

**Independent Test**: Ingest documents with one configured LLM, change the LLM in
configuration, restart, and confirm the same knowledge base answers queries using the
new model with no re-ingestion.

**Acceptance Scenarios**:

1. **Given** a populated knowledge base, **When** the owner changes the query LLM in configuration and restarts, **Then** queries are answered by the new model and existing knowledge-base content is unchanged.
2. **Given** a populated knowledge base, **When** the owner changes the embedding model, **Then** the system communicates that re-indexing is required and does not present stale results as current.
3. **Given** the owner is selecting a model, **When** they consult the project configuration, **Then** LLM and embedding provider/model settings are exposed as configuration values, not fixed in the application.

---

### User Story 7 - Skip documents that have not changed (Priority: P3)

The owner re-runs ingestion after adding a few new documents to the input directory.
Documents that were already ingested and are unchanged are not reprocessed; only new or
modified documents consume processing time.

**Why this priority**: On a CPU-only machine, reprocessing the whole collection on every
ingest run is slow and wasteful. This matters once the collection grows but not for the
first proof of function.

**Independent Test**: Ingest a set of documents, run ingestion again with no changes and
confirm nothing is reprocessed, then modify one document and confirm only that one is
reprocessed.

**Acceptance Scenarios**:

1. **Given** a set of documents has been ingested, **When** ingestion runs again with no changes, **Then** no document is reprocessed and the run completes quickly.
2. **Given** one previously ingested document has been modified, **When** ingestion runs, **Then** only that document is reprocessed.
3. **Given** a new document is added alongside unchanged ones, **When** ingestion runs, **Then** only the new document is processed.

---

### Edge Cases

- A document is added to the input directory while ingestion is already running.
- Ingestion is interrupted partway (service stopped, machine powered off); a later run must recover without corrupting the knowledge base or double-counting content.
- The model service is reachable but the requested model is not installed.
- A document is very large relative to available memory on a CPU-only machine.
- A document is removed from the input directory after it was ingested (does its content remain in the knowledge base, and is that the intended behaviour?).
- Two documents contain contradictory information about the same topic.
- Disk fills during ingestion or backup.
- A backup artifact is from an older application version than the one now installed.
- The Web UI and API are queried at the same time.
- A restore is attempted onto a running service with a live knowledge base already present.

## Requirements *(mandatory)*

### Functional Requirements

#### Document intake and ingestion

- **FR-001**: The system MUST provide a designated local input directory into which the owner places documents for ingestion.
- **FR-002**: The system MUST ingest supported document types placed in the input directory into the knowledge base when the owner runs an explicit ingestion command that scans the input directory on demand. Supported types at launch are plain text, Markdown, and PDF.
- **FR-002a**: The Web UI's document-upload capability MUST also be available for ad-hoc ingestion. The system MUST NOT run a background file-watcher and MUST NOT ingest automatically on service startup.
- **FR-003**: The system MUST build and maintain a knowledge graph and a retrieval index from ingested document content.
- **FR-004**: The system MUST skip files whose type is not supported, report them as skipped, and continue processing the rest of the batch.
- **FR-005**: The system MUST detect documents that are unchanged since their last successful ingestion and MUST NOT reprocess them.
- **FR-006**: The system MUST reprocess a document that has been added or modified since the last ingestion run.
- **FR-007**: The system MUST recover from an interrupted ingestion run on the next run without corrupting existing knowledge-base data.

#### Query and retrieval

- **FR-008**: The owner MUST be able to query the knowledge base through a Web UI and receive answers grounded in ingested documents.
- **FR-009**: The system MUST expose a local API that accepts queries and returns structured responses suitable for programmatic consumption.
- **FR-010**: Query responses (Web UI and API) MUST include references to the source material that informed the answer.
- **FR-011**: The system MUST distinguish between "no supporting information found" and a substantive answer, and MUST NOT present fabricated content as document-sourced.

#### Persistence and data protection

- **FR-012**: The system MUST persist the knowledge graph, retrieval indexes, document metadata, and ingestion-tracking state such that they survive service restart, service recreation, and application/image updates.
- **FR-013**: Routine operations (start, stop, restart, ingest, health check, view logs, query) MUST NOT delete or overwrite persistent knowledge-base data as a side effect.
- **FR-014**: Any operation that would delete, replace, or overwrite persistent knowledge-base data MUST require explicit owner confirmation and MUST NOT be the default behaviour of a routine command.
- **FR-015**: The system MUST provide a single documented procedure to back up the complete knowledge base, producing a restorable artifact in a known location.
- **FR-016**: The system MUST provide a single documented procedure to restore the knowledge base from a backup artifact, returning it to the captured state.
- **FR-017**: The backup procedure MUST include every component required for a full restore and MUST NOT silently omit any.
- **FR-018**: The documented upgrade process MUST direct the owner to take a backup before any significant upgrade.

#### Operability and diagnostics

- **FR-019**: Each common operation (start, stop, restart, ingest, back up, restore, check health, view logs) MUST have a simple, documented command.
- **FR-020**: The system MUST provide a health check that verifies the application is responding, the language and embedding models are reachable, the configured models are available, and persistent storage is writable — not merely that a process is running.
- **FR-021**: When a health check fails, the output MUST identify the failing component and the likely cause.
- **FR-022**: When ingestion, indexing, or model inference fails, the system MUST produce operational output identifying which document and which processing stage failed.

#### Model independence

- **FR-023**: The knowledge base MUST remain valid and queryable when the query LLM is changed, without re-ingesting source documents.
- **FR-024**: The LLM and embedding provider and model selections MUST be exposed as configuration values, not fixed in application code.
- **FR-025**: When a configuration change (such as changing the embedding model) invalidates existing indexes, the system MUST make clear that re-indexing is required and MUST NOT present stale results as current.
- **FR-026**: The system MUST remain usable with small CPU-runnable models and MUST NOT require GPU acceleration or a large local model to function.

#### Locality and exposure

- **FR-027**: All documents and knowledge-base data MUST remain on the local machine; the system MUST NOT require uploading documents to a third-party service.
- **FR-028**: Network services (Web UI and API) MUST be reachable only from the local machine by default and MUST NOT be exposed to the public Internet.
- **FR-028a**: Access control for the Web UI and API is provided solely by binding them to the local loopback interface. The initial implementation does NOT require an authentication token on the API. Adding authentication is a prerequisite of any future decision to widen network exposure.

#### Reproducibility

- **FR-029**: The project repository MUST contain everything required to reconstruct the deployment on the same machine after failure, including configuration templates and setup documentation.
- **FR-030**: Machine-specific state and secrets MUST be kept out of version control, supplied through ignored local configuration with a committed example.
- **FR-031**: The project MUST include an automated or scripted validation that demonstrates, end to end: the service starts; the API is reachable; the Web UI is reachable; a document can be ingested; a query retrieves information from that document; the knowledge base survives a restart; the knowledge base can be backed up; and the knowledge base can be restored.

### Key Entities *(include if feature involves data)*

- **Source Document**: A file the owner places in the input directory for ingestion. Key attributes: identity/path, content, type, last-modified state, ingestion status, a fingerprint used for change detection.
- **Knowledge Graph**: The network of entities and relationships extracted from source documents. Persistent. Rebuilt only by re-ingestion.
- **Retrieval Index**: The structure(s) used to find relevant content for a query, derived from document content and the embedding model. Persistent. Invalidated by an embedding-model change.
- **Document Metadata / Ingestion Ledger**: Per-document record of what has been ingested, when, and with what result; the basis for skipping unchanged documents and for reporting per-document failures.
- **Query**: An owner or client request for information. Attributes: question text, retrieval mode/parameters, response, source references.
- **Configuration**: The set of environment-specific and model-specific settings (LLM provider/model, embedding provider/model, directories, network binding). Kept outside version control; a committed example documents the shape.
- **Backup Artifact**: A restorable capture of the complete knowledge base (graph, indexes, metadata, ingestion ledger) at a point in time, stored in a known local location.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Starting from a clean checkout on the target machine, the owner can bring the service up and confirm it is healthy using only documented commands, in under 30 minutes.
- **SC-002**: A newly added supported document placed in the input directory becomes queryable after a single ingestion action, with no manual steps beyond that action.
- **SC-003**: For a question whose answer is contained in an ingested document, the owner receives a correct, source-referenced answer in at least 8 of 10 representative test questions.
- **SC-004**: After a full machine restart, 100% of previously ingested knowledge-base content is queryable with no re-ingestion.
- **SC-005**: After an application version update and service recreation, 100% of previously ingested knowledge-base content is queryable with no re-ingestion.
- **SC-006**: Re-running ingestion on an unchanged collection reprocesses 0 documents.
- **SC-007**: The owner can produce a backup and, from that backup alone, restore a knowledge base that returns identical query results to the pre-backup state, using only documented commands.
- **SC-008**: When a required dependency (model service, model, storage) is unavailable, the health check names the specific failing dependency in 100% of those cases.
- **SC-009**: The owner can change the query LLM via configuration and continue querying the existing knowledge base without re-ingestion.
- **SC-010**: The Web UI and API are reachable from the local machine and unreachable from any other host on the network.
- **SC-011**: The end-to-end validation script passes on the target machine, exercising all eight required checks (start, API reachable, Web UI reachable, ingest, query retrieval, restart survival, backup, restore).
- **SC-012**: The system runs its core ingest-and-query workflow to completion using a small CPU-only model, with no GPU present.
- **SC-013**: The system ingests and answers queries over a collection of at least several hundred documents without changes to the deployment topology (single application container on file-based storage).

## Assumptions

- **LightRAG provides the Web UI, the API, ingestion, the knowledge graph, and the retrieval index as built-in capabilities.** This feature configures and operates LightRAG's existing server rather than building any of these components. If a required capability turns out not to exist in LightRAG, that is a material finding to surface during planning.
- **The host already provides Docker and Ollama as shared infrastructure**, per the project constitution. Ollama runs at least one small CPU-capable model and at least one embedding-capable model, or such models can be pulled.
- **Supported document types at launch are plain text, Markdown, and PDF.** These are expected to be within LightRAG's built-in parsing; this will be confirmed against official documentation during planning. Office formats and others are out of scope for the initial implementation and can be added as a later feature.
- **The document collection is personal-scale** — up to a few hundred documents — and single-user with no concurrent ingestion by multiple actors. The initial build uses LightRAG's default lightweight file-based storage; an external database (e.g. PostgreSQL/Neo4j) is introduced only if a demonstrated need arises. The design will not be optimised for large corpora or high query concurrency.
- **Change detection is based on a per-document fingerprint** (such as a content hash and/or path plus modification time); the precise mechanism follows LightRAG's built-in document-tracking behaviour where available.
- **Backup is taken at the filesystem level** against the persistent data location, preferably with the service stopped or quiesced to ensure consistency. Application-level export is used instead only if LightRAG provides one that is more reliable.
- **Changing the query LLM does not require re-ingestion; changing the embedding model does require re-indexing** because stored vectors are model-specific. Re-indexing re-uses the original source documents and does not require the owner to re-collect them.
- **"Not exposed to the public Internet" is achieved by binding services to the local loopback interface by default**, consistent with the constitution's networking constraint. This loopback binding is also the sole access-control mechanism — no API authentication token is used in the initial implementation. Widening access later is an explicit, separate decision that must add authentication first.
- **Removing a document from the input directory does not automatically remove its content from the knowledge base** in the initial implementation; deletion from the knowledge base is an explicit owner-initiated operation. This behaviour will be documented.
- **The end-to-end validation is a script run on demand by the owner**, not a hosted CI pipeline, since the system is local and single-user.
- **Secrets in scope are minimal** (for example a provider API key only if a non-local LLM/embedding provider is ever configured); by default, local-only operation needs no secrets, and the LightRAG API itself is unauthenticated.

## Out of Scope

- Building Claude Code, MCP-server, local-script, or other AI-assistant integrations. The API is preserved as the integration boundary; consumers are added in later features.
- Any custom RAG pipeline, custom Web UI, custom vector database, custom document crawler, or authentication/multi-user platform, unless the chosen LightRAG deployment strictly requires it.
- Cloud or remote deployment, and exposure of the service beyond the local machine.
- Multi-user access, role-based permissions, or tenant isolation.
- Optimisation for large document collections or high query concurrency.
- Automatic synchronisation of knowledge-base deletions with the input directory.
- Scheduled/automated ingestion or backups (the owner triggers these; automation can be added later).
