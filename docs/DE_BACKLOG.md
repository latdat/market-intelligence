# Data Engineer Backlog — Initial Build

This is an implementation ordering guide, not proof that any component is already built.

## Source Onboarding roadmap

This is the approved conceptual sequence for the Official Source Architecture v1 track.
The canonical 25-source matrix lives in
[`SOURCE_REGISTRY.md`](SOURCE_REGISTRY.md#23-target-official-architecture-v1--canonical-matrix)
and is not duplicated here.

| Task | Outcome | Status |
|---|---|---|
| SO-002 | Adopt Official Source Architecture v1 in documentation | `Implemented (documentation/design only)` |
| SO-003 | Rights-safe `SourceConfig` / content-scope contract | `Implemented` |
| SO-004 | RSS + Government API official sources | `Implemented` |
| SO-005 | Legal Corpus sources | `Implemented (SO-005 v1: PLAW package-level)` |
| SO-006 | Official Listing sources | `Implemented` |
| SO-007 | Complete 25-source portfolio | `COMPLETE FOR PILOT SOURCE ONBOARDING (25/25 dispositioned: 18 implemented, 5 source-level blocked, 2 deferred pending post-pilot)` |
| SO-008 | Production verification | `Planned / documented only` |

SO-001 and SO-004 remain the implemented pilot, with SO-004 now expanding to cover seven sources (six RSS/Atom, one REST API for `us_federal_register`).
SO-005 adds `us_govinfo_legal` with `LegalCorpusConnector` v1.
SO-006 adds `vn_sbv_regulatory_docs` with `OfficialListingConnector` v1.
The China final implementation batch is completed: PBOC/NEA current-runtime live preflight PASS. State Council/CSRC implementation complete but current-runtime production-style live verification remains limited.
FERC/MOHURD deferred post-pilot.
SO-008 is NOT started.
This roadmap does not resume or renumber DE work. DE-013 remains `PAUSED`.

## Phase 0 — Repository foundations

### DE-001 Repository scaffold

Goal:

- create clear DE service/package/test layout
- establish Python tooling
- add environment/config conventions

Definition of Done:

- repository runs locally
- test command exists
- lint/type commands defined if selected
- no production feature required yet

### DE-002 Source Registry model

Goal:

- define source registry validation model
- define source config loading approach
- add initial test fixtures

Must include core fields:

- source_id
- name
- market
- language
- source_type
- authority_level
- domains
- acquisition method
- polling interval
- rate limit
- rights
- cost
- priority
- health state

## Phase 1 — Core ingestion contracts

### DE-003 RawArticle + CanonicalArticle models

Goal:

- implement contracts from `DATA_CONTRACTS.md`
- define validation and timestamp policy

### DE-004 Generic RSS/Atom connector

Goal:

- fetch/parse RSS/Atom
- output `RawArticle`

### DE-005 Normalization pipeline

Goal:

- convert `RawArticle` to `CanonicalArticle`
- normalize URL/title/description/time safely
- create deterministic hash/ID policy

### DE-006 Deterministic deduplication

Order:

1. canonical URL
2. source item ID
3. hash
4. normalized-title similarity

Do not add vector infrastructure.

## Phase 2 — Persistence and classification

### DE-007 Supabase article persistence

Goal:

- schema/migration for source/article operational state
- idempotent insert/update behavior
- preserve first discovery time

### DE-008 DeepSeek V4 Flash classification adapter

Status: implemented as a standalone, offline-tested provider adapter. DE-009B/DE-009C later
wired it as the rights-gated fallback; DE-009 owns durable persistence.

Goal:

- minimal structured input
- validated structured output
- timeout/retry handling
- telemetry/cost fields

### DE-009 Classification persistence

Status: implemented and PostgreSQL 17 verified; both migrations are applied to and
catalog-verified on remote Supabase. DE-009B orchestration is implemented separately.

Goal:

- associate classification with article
- use `(article_id, classifier_version)` identity
- own durable attempt lifecycle and cross-run idempotency
- preserve `classified_at`
- fence stale workers with `claim_token` and leases
- reject enqueue lineage mismatch without overwriting history

### DE-009B Classification runner / orchestration

Status: implemented with offline fake-classifier tests; no production article has been
classified by the implementation task.

Goal:

- bounded rights-aware discovery and idempotent enqueue
- sequential claim, article/source reload, rights recheck, DE-008 invocation
- fenced completion/failure persistence with lease heartbeat
- systemic `STOP_BATCH` behavior without changing DE-009 lifecycle

### DE-009C Deterministic / Hybrid Classification

Status: implemented and offline-tested locally. The additive
`20260817000000_add_classification_method.sql` migration has been applied to and verified
on remote Supabase; no production articles were classified by the implementation task.

Goal:

- run versioned conservative deterministic rules before provider access
- route only `AMBIGUOUS` work through the existing rights-gated DE-008 adapter
- reuse DE-009B claim/lease/fencing and the single DE-009 persistence path
- persist DE-internal `classification_method` without changing shared `ClassifiedArticle`
- keep `classification-v1` as historical DeepSeek-first identity and use
  `classification-v2` with `deterministic-rules-v1`

## Phase 3 — Matching

### DE-010 User preference read contract

Status: implemented as a strict model and protocol boundary; offline-tested locally. No concrete persistence adapter is provided.

Goal:

- implement/reuse the strict shared `UserPreference` model without owning UI or writes
- expose a backend-neutral read boundary for DE-011 matching
- preserve shared classification taxonomy codes for market/category/topic preferences
- define bounded deterministic `user_id`-ordered keyset page semantics
- do not invent preference persistence/schema before product/SWE provides an authoritative
  persistence contract

### DE-011 Rule-based matching

Status: implemented and offline-tested. No alert-candidate persistence is introduced; DE-012 owns durable candidate idempotency.

Rules include:

- market
- category
- topic
- mute rules
- freshness
- basic importance

Output:

- `AlertCandidate`

### DE-012 Alert candidate idempotency

Status: implemented and offline-tested; alert-candidate migration verified on isolated local
PostgreSQL. Remote migration drift is exactly:

- `20260818000000_create_alert_candidates.sql`
- `20260818000001_grant_alert_candidates_service_role.sql`

The missing production matching runner does not block SWE Data Ready v1 synthetic contract
data. It blocks real `alert_candidates` population. Product/SWE owns authoritative
`UserPreference` persistence; DE consumes the shared contract and must not create a fake
preference schema or adapter as production state.

Goal:

- repeated pipeline runs must not produce duplicate logical alert candidates

## Phase 4 — Telemetry and operations

### DE-013 Pipeline telemetry

Status: `PAUSED`. Do not start DE-013 until it is explicitly resumed.

Track:

- published_at
- discovered_at
- classified_at
- matched_at
- status
- duration
- estimated AI cost

### DE-014 BigQuery telemetry sink

Goal:

- lightweight append/query path
- not used for web serving

### DE-015 Source health tracking

Track:

- fetch success rate
- latency
- last success/failure
- consecutive failures
- new items discovered
- parse errors

## Phase 5 — Deployment

### DE-016 Cloud Run Job

Goal:

- package data pipeline
- environment/config setup
- safe retries
- structured logs

### DE-017 Cloud Scheduler

Goal:

- trigger ingestion at target intervals
- default ~15 minutes where appropriate
- source-specific intervals when needed

## Phase 6 — More acquisition methods (legacy DE placeholders)

The DE-018 through DE-021 labels below predate the approved Source Onboarding roadmap.
They remain historical generic placeholders, are not implementation evidence, and must
not be started or mapped one-to-one without reconciling them with SO-005 through SO-008.

### DE-018 Generic REST connector framework

### DE-019 First official REST source

### DE-020 HTML connector framework

### DE-021 First HTML source

Add Playwright only after proving static HTTP parsing is insufficient.

## Phase 7 — Reliability

### DE-022 Integration test suite

Critical path:

```text
fixture source
→ connector
→ normalize
→ dedup
→ persistence
→ classification mock
→ matching
→ alert candidate
```

### DE-023 Repeated-run/idempotency test

Run the same logical ingestion more than once and verify:

- no duplicate article
- first discovery time preserved
- no duplicate classification side effect
- no duplicate alert candidate

### DE-024 Freshness measurement

Compute stage latencies and P50/P95.

## Suggested first vertical slice

Before implementing dozens of sources, prove one end-to-end path:

```text
1 real RSS/API source
   ↓
RawArticle
   ↓
CanonicalArticle
   ↓
Dedup
   ↓
Supabase
   ↓
Classification
   ↓
Matching
   ↓
AlertCandidate
   ↓
Telemetry
```

Then generalize the framework and add sources in parallel.
