<!--
Sync Impact Report
==================
Version change: 1.0.0 → 1.1.0
Rationale: MINOR. Materially expanded guidance for feature 002 (kb-sync knowledge
pipeline, specs/002-kb-sync-knowledge-pipeline/ FR-001). No principle removed; Principle X
is narrowed from "out of scope" to "in scope under stated limits", which widens what is
permitted without invalidating anything built under 1.0.0.

Modified principles:
  - III. Local-First: adds the Recorded Cloud Dependencies table (Google Drive, AppFlowy
    Cloud, NotebookLM, Claude API) and the rule that content leaves the machine only to a
    recorded dependency, on an owner-initiated command, with the minimum content needed.
  - V. Persistent Data Is Sacred: permits source-driven knowledge removal by the sync tool
    under backup, logging, and a held-removal safety limit.
  - X. Future Integration Without Premature Implementation: brings Claude API answers and
    a local MCP server for Claude Code into scope, through the REST API only.

Added principles: none
Added sections: none
Removed sections: none

Templates / files requiring review:
  - .specify/templates/plan-template.md ✅ reads constitution at runtime; no change required
  - .specify/templates/spec-template.md ✅ no change required
  - .specify/templates/tasks-template.md ✅ no change required
  - README.md ✅ "What this deliberately does not do" updated

Follow-up TODOs:
  - The local reranker service (spec 002, Story 2) is NOT authorised by this amendment.
    It needs its own Principle I/IX justification once Phase 0 has picked and tested a
    server and the test set shows the local model's filtering falls short.
-->

# LiteRAG Setup Constitution

## Core Principles

### I. Simplicity Over Infrastructure

This is a personal, single-user knowledge system. The deployment MUST use the smallest
number of components necessary to satisfy stated requirements. Infrastructure MUST NOT be
introduced merely because it is technically possible or conventionally expected. Every
component, service, database, or dependency added to the system MUST be traceable to a
concrete, documented requirement. When two designs satisfy the same requirement, the one
with fewer moving parts wins.

**Rationale**: A single-user system carries none of the scaling, multi-tenancy, or
high-availability burdens that justify heavy infrastructure. Unjustified components become
permanent maintenance and failure surface.

### II. Containerised Application

Application dependencies MUST remain isolated from the EndeavourOS host wherever
practical. Docker Compose is the preferred deployment mechanism and MUST be the default
for all application services. The host is responsible only for infrastructure that is
intentionally shared — currently Docker and Ollama — and the application MUST consume
those as external dependencies rather than reimplementing or bundling them.

**Rationale**: Host isolation keeps the rolling-release host upgradable without breaking
the knowledge base, and keeps the application's runtime reproducible and disposable.

### III. Local-First

Documents and knowledge-base data MUST remain on the local machine. Cloud services and
external APIs MUST NOT be introduced unless a specific requirement explicitly demands them,
and any such dependency MUST be recorded with its justification. Network exposure MUST be
limited to localhost until a deliberate, documented decision widens it.

The knowledge base itself (the Markdown store, index, graph, and sync records) MUST stay
on the local machine. Content MAY leave the machine only to a dependency recorded below,
only on a command the owner runs, and only the minimum that command needs. The owner MUST
be able to skip any command that sends content out without losing local function.

**Recorded Cloud Dependencies** (added 1.1.0 for feature 002):

| Service | Why it is needed | What crosses the boundary |
| --- | --- | --- |
| Google Drive | The owner's documents live there; syncing chosen folders is the requirement (spec 002 FR-006). | Read only (`drive.readonly`), narrowed to the configured folder allow-list by the sync tool. Nothing is written to Drive. |
| AppFlowy Cloud | The owner's notes live in the hosted service (spec 002 FR-015). | Read only: page tree and page content of the chosen workspace. |
| NotebookLM (consumer API) | Research packs are pushed to a notebook for report, podcast, and infographic generation, which has no local equivalent (spec 002 FR-027). | The items in a pack the owner builds and pushes. Never the whole store. |
| Claude API | The owner's chosen writer of cited answers from retrieved passages (spec 002 FR-019). | The question and the passages that survive the local filter. `--local` sends nothing. |

**Rationale**: The value of a personal knowledge base depends on the owner retaining full
custody of their data and not leaking it to third parties by default. Where a cloud
service is the requirement, recording it and bounding what it sees keeps that custody
deliberate.

### IV. Replaceable AI Models

The architecture MUST NOT become tightly coupled to any single LLM or embedding model. LLM
and embedding providers and model names MUST be configurable through environment
configuration, not hard-coded. Switching model or provider MUST be achievable through
configuration and data re-processing where required — never through application redesign.

**Rationale**: Local model quality and availability change rapidly; the system must be able
to follow that curve without a rewrite.

### V. Persistent Data Is Sacred

Container recreation, image updates, and application updates MUST NEVER destroy or corrupt
the knowledge base as a side effect. Persistent state MUST live in named volumes or bind
mounts that survive `docker compose down` and image replacement. Destructive operations
(volume deletion, index wipes, bulk re-ingestion that replaces data) MUST require explicit
human confirmation and MUST NOT be default behaviour of any routine command. A backup of
the knowledge base MUST be supported and MUST be taken before any significant upgrade.

One exception (added 1.1.0): the sync tool MAY remove a document's knowledge without
per-run confirmation when that document's upstream source changed (replace) or was deleted
or trashed (remove). Every such removal MUST follow a knowledge-base backup taken in the
same run, MUST be logged with the item and the reason, and removals beyond a configured
per-run safety limit MUST be held for the owner's confirmation. The sync tool's records
MUST be backed up and restored together with the knowledge base.

**Rationale**: The knowledge base is the irreplaceable asset; everything else in the
repository can be rebuilt from source.

### VI. Reproducibility

The repository MUST contain everything required to reconstruct the application deployment
from a clean checkout: compose files, Dockerfiles, configuration templates, and setup
documentation. Machine-specific state and secrets MUST NOT be committed; they MUST be
provided through ignored local files (for example `.env`) with a committed example
template.

**Rationale**: A reproducible deployment is recoverable after disk loss and auditable for
what it actually runs.

### VII. Operational Clarity

Common operations — start, stop, ingest, back up, restore, check health, view logs — MUST
each have a simple, documented command. Failures MUST produce actionable diagnostics that
identify the failing dependency and the likely cause, not just a stack trace. Health checks
MUST verify that dependencies (Ollama reachability, model availability, storage writability)
actually work, not merely that a container process is running.

**Rationale**: A system operated occasionally by one person must be legible months later
without re-deriving how it works.

### VIII. Evidence Over Assumption

When the behaviour of LightRAG, Docker, Ollama, or any other dependency affects an
implementation decision, the current official documentation for that dependency MUST be
consulted before the decision is made. Configuration options, API endpoints, environment
variables, and CLI flags MUST NOT be invented or assumed; each MUST be traceable to
official documentation or verified behaviour.

**Rationale**: Guessed configuration produces systems that appear to work and fail silently
or on upgrade.

### IX. Incremental Complexity

The smallest working system MUST be built and validated first. Additional databases,
services, optimisations, caches, or integrations MUST NOT be added until a demonstrated
requirement — an observed limitation, not a hypothetical one — justifies them. Each such
addition MUST be introduced as a discrete, reviewable change.

**Rationale**: Complexity added preemptively is rarely removed and usually mis-targeted.

### X. Future Integration Without Premature Implementation

The LightRAG REST API MUST remain available and stable as the integration boundary for
AI tooling. Integrations such as Claude Code and MCP were OUT OF SCOPE for the initial
implementation (feature 001).

From 1.1.0, feature 002 brings two integrations into scope: Claude API answers and a local
MCP server that lets Claude Code on this machine query the knowledge base. Both MUST reach
LightRAG only through its REST API, MUST NOT change the LightRAG server beyond its
configuration, and MUST stay on the loopback interface. Any further integration MUST be
specified as its own feature before it is built.

**Rationale**: Keeping a clean API boundary costs nothing and preserves every option.
Integrations are built when a specified need exists, not before.

## Additional Constraints

- **Host baseline**: EndeavourOS (Arch-based, rolling release). Shared host services:
  Docker and Ollama. The application MUST tolerate host package updates.
- **Deployment unit**: A single Docker Compose project under version control in this
  repository.
- **Configuration**: All environment-specific and model-specific settings MUST be
  supplied via an ignored `.env` file with a committed `.env.example` counterpart.
- **Networking**: Published ports MUST bind to `127.0.0.1` unless a documented decision
  changes this.

## Development Workflow & Quality Gates

- Spec Kit artifacts (`spec.md`, `plan.md`, `tasks.md`) MUST be checked against this
  constitution during planning; violations MUST be resolved or explicitly justified in the
  plan's Complexity Tracking section.
- Any change that adds a service, volume, external dependency, or network exposure MUST
  state which principle permits it and why.
- Changes affecting persistent data or destructive commands MUST document the backup and
  recovery path before merge.
- Dependency behaviour claims MUST cite official documentation in the plan or the change
  description (Principle VIII).

## Governance

This constitution supersedes ad-hoc practice for the project. When guidance conflicts, the
constitution wins.

- **Amendments**: Proposed by editing this file. Each amendment MUST update the version,
  the Last Amended date, and the Sync Impact Report comment at the top of this file.
- **Versioning policy** (semantic):
  - **MAJOR**: Removal or backward-incompatible redefinition of a principle or governance
    rule.
  - **MINOR**: Addition of a new principle or section, or materially expanded guidance.
  - **PATCH**: Clarifications, wording, and non-semantic refinements.
- **Compliance review**: At each planning cycle and before each significant upgrade, the
  active design MUST be reviewed against all ten principles. Deviations MUST be recorded
  with justification or corrected.
- **Runtime guidance**: Agent- and contributor-facing operational guidance lives in the
  repository README and Spec Kit templates, which defer to this constitution.

**Version**: 1.1.0 | **Ratified**: 2026-08-30 | **Last Amended**: 2026-11-21
