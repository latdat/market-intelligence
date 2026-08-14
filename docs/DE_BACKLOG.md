# Data Engineer Backlog — Initial Build

This is an implementation ordering guide, not proof that any component is already built.

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

Status: implemented as a standalone, offline-tested adapter; not wired or persisted.

Goal:

- minimal structured input
- validated structured output
- timeout/retry handling
- telemetry/cost fields

### DE-009 Classification persistence

Status: planned only.

Goal:

- associate classification with article
- use `(article_id, classifier_version)` identity
- own durable attempt lifecycle and cross-run idempotency
- preserve `classified_at`

## Phase 3 — Matching

### DE-010 User preference read contract

Goal:

- consume shared user preference contract without owning UI

### DE-011 Rule-based matching

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

Goal:

- repeated pipeline runs must not produce duplicate logical alert candidates

## Phase 4 — Telemetry and operations

### DE-013 Pipeline telemetry

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

## Phase 6 — More acquisition methods

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
