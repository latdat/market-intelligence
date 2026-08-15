# Testing Strategy — Data Engineer Scope

## 1. Testing goals

Tests should protect the properties most likely to damage this product:

- missed information
- duplicate information
- wrong timestamps
- repeated side effects
- bad source parsing
- broken classification output
- incorrect matching
- silent pipeline failure

## 2. Test levels

### Unit tests

Use for:

- URL normalization
- timestamp parsing
- content hashing
- source-specific mapping
- classification output validation
- matching rules
- dedup helpers

### Connector tests

Use fixtures/mocked HTTP responses.

Do not require live public internet for normal CI.

### Integration tests

Use for:

- connector → normalization
- normalization → persistence
- persistence → classification
- classification → matching
- idempotent repeated runs

### End-to-end smoke tests

Use a small controlled source/fixture path to validate the critical pipeline.

Do not make production third-party services mandatory for every local test run.

## 3. Required connector cases

Every connector family should test representative cases:

- HTTP 200
- timeout
- connection failure
- 429 rate limit
- 500/502/503 transient error
- malformed response
- empty response/feed
- missing title
- missing description
- missing publication time
- unexpected encoding when relevant
- duplicate source item
- repeated run

RSS/Atom additionally:

- malformed XML
- feed with different date formats
- entries without GUID/source item ID

REST additionally:

- pagination
- empty page
- changed/missing optional field
- rate-limit response

HTML additionally:

- target element missing
- layout change fixture
- relative URL resolution
- duplicate links

## 4. Normalization cases

Test:

- tracking parameter removal
- canonical URL stability
- Unicode title normalization where used
- whitespace normalization
- timezone conversion
- missing timezone policy
- description cleanup
- deterministic content hash
- repeated normalization gives same result

## 5. Deduplication cases

Test each layer separately:

```text
same canonical URL → duplicate
same source item ID → duplicate
same deterministic hash → duplicate
similar normalized title → policy-dependent duplicate
different real article → not duplicate
```

Also test order of operations so expensive similarity checks are avoided when a cheap exact check succeeds.

## 6. Persistence/idempotency cases

Required:

- first insert succeeds
- repeated identical ingestion does not create a second logical article
- `discovered_at` preserves first discovery
- changed allowed metadata can update safely
- transaction rollback does not leave partial critical state
- duplicate classification side effect is prevented where applicable

## 7. Classification cases

Mock the LLM API.

Test:

- valid structured output
- strict field/type validation and rejection of unknown fields
- valid unsorted markets/topics normalize without another provider call
- duplicate and unsupported controlled codes fail validation
- relevant/irrelevant cross-field invariants
- confidence finite/range boundaries and rejection of numeric strings/booleans
- rights denial and source mismatch produce zero prompt/HTTP calls
- provider prompt excludes IDs, URLs, raw/full content, and credentials
- invalid JSON
- missing required field
- unknown category
- confidence outside expected range
- connect/read/write timeout and connection failure
- 429 plus bounded `Retry-After`
- generic transient 500/502/503/504
- `insufficient_system_resource` retries
- `content_filter`, `length`, and unexpected `tool_calls` do not retry
- retry exhaustion
- provider attempt count includes every actual call
- observed usage and cost aggregate across failed and successful attempts
- invalid/negative/inconsistent provider usage is rejected
- timeout/lost-response attempts do not invent usage
- errors/logs do not leak keys, prompts, article content, or raw responses
- effective-date and UTC peak/off-peak pricing boundaries

DE-008 tests only bounded retries within one invocation and use mocked HTTP; normal CI
must not call the live provider.

DE-009 unit tests validate strict persistence models, repository-to-RPC mapping,
sanitized persistence errors, enqueue lineage mismatch, and the independence of durable
`attempt_count` from DE-008 `provider_attempts`.

DE-009 integration tests use an isolated local PostgreSQL cluster when PostgreSQL
`initdb`, `pg_ctl`, and `psql` are available. They apply every committed migration and
verify:

- exact composite primary key, constraints, indexes, RLS, grants, and RPC privileges;
- idempotent enqueue and `LINEAGE_MISMATCH` without overwriting lineage;
- concurrent `FOR UPDATE SKIP LOCKED` claims issue at most one claim per row;
- claim/reclaim increments once per durable invocation;
- lease renewal and `updated_at` mutation;
- stale token fencing after expiry/reclaim;
- immutable/idempotent `SUCCEEDED` replay;
- retry scheduling, cumulative observed usage/cost, and quarantine at budget exhaustion;
- non-retryable failures cannot remain `RETRYABLE`;
- invalid taxonomy arrays and inconsistent provider usage fail database validation;
- expired exhausted recovery mutates only one locked candidate per claim call.

The PostgreSQL fixture is offline, creates no provider calls, uses no remote Supabase,
and skips only when local PostgreSQL binaries are unavailable.

DE-009B tests use fake classifiers and fake persistence/read boundaries. They verify:

- a version-scoped PostgREST anti-join request and bounded deterministic ordering;
- v1 makes zero PostgREST discovery/enqueue calls when no source has approved AI rights;
- lineage preflight and idempotent enqueue outcomes;
- claim, article/source reload, rights recheck, success/failure persistence;
- terminal rights-revocation quarantine without provider access or automatic reset;
- durable retry/quarantine mapping and systemic `STOP_BATCH` behavior;
- immediate renewal, periodic heartbeat, cancellation and stale-claim rejection;
- sequential process/time limits and observed usage/cost propagation;
- no article content, provider payload, or credentials in errors/logs.

Normal DE-009B tests never construct the environment-based DeepSeek adapter and require
neither a DeepSeek account nor API key. A live smoke of one to three explicit articles is
a separate manually approved operational task.

DE-009C deterministic tests are offline and cover NFC/case/whitespace normalization,
token-boundary matching, strong-title and source-domain evidence, conflict/insufficient
fallback, provenance/content market separation, institution aliases, controlled
multi-market/topic output, no deterministic irrelevant decision, and reproducibility.

Hybrid/runner tests verify:

- confident deterministic success makes zero DeepSeek calls and has zero usage/cost;
- ambiguous approved work invokes the existing DE-008 boundary exactly once;
- ambiguous denied work makes zero provider calls and is terminal
  `AI_FALLBACK_NOT_ALLOWED`;
- `classification_method` persistence and separate v1/v2 identities;
- v2 discovery requires metadata-storage rights rather than AI rights;
- fallback still enforces AI rights and systemic provider failures keep `STOP_BATCH`;
- existing claim, lease, heartbeat, replay, and fencing tests continue to pass.

Migration tests apply all migrations to an isolated PostgreSQL cluster and verify method
constraints, deterministic zero-attempt success, rejection of zero-attempt DeepSeek
success, and null method on non-success states. PostgreSQL 17 verification remains a
required release gate for DE-009C.

DE-010 tests are offline and verify the shared `UserPreference` contract and read-page
invariants:

- all required fields and strict unknown-field rejection;
- shared market/category/topic taxonomy compatibility with classification;
- strict booleans, non-blank IDs, and duplicate collection rejection;
- empty interest/mute collections remain valid;
- input collection order is preserved;
- page-local unique `user_id` values and deterministic ascending order;
- empty-page/cursor consistency and cursor equality with the last returned `user_id`;
- sanitized read errors.

DE-010 does not fake a production persistence adapter merely to test pagination. Concrete
adapter request bounds, cursor continuity across pages, backend failure mapping, and
cross-page duplicate prevention must be tested when an authoritative preference
persistence adapter is introduced.

## 8. Matching cases

Test combinations:

- market match only
- category match
- topic match
- muted source
- muted topic
- stale article
- breaking disabled
- hourly disabled
- multiple matching reasons
- same article/user pair processed twice

Matching output should remain explainable.

## 9. Freshness/telemetry cases

Ensure stage timestamps are not conflated.

At minimum verify:

```text
published_at <= discovered_at
discovered_at <= classified_at
classified_at <= matched_at
```

Do not force these inequalities when source data is known to be inconsistent without defining a policy; surface the anomaly instead.

## 10. Test data

Prefer small deterministic fixtures.

Recommended structure:

```text
tests/
├── fixtures/
│   ├── rss/
│   ├── rest/
│   └── html/
├── unit/
├── integration/
└── e2e/
```

Never put secrets or copyrighted full-text datasets into test fixtures without explicit approval.

## 11. Completion report

Codex should finish a task by reporting:

```text
Tests added:
Tests run:
Result:
Known gaps:
```
