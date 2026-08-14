# AGENTS.md — Market & Regulatory Intelligence Platform

## Response language

For all explanations, plans, reviews, summaries, and completion reports:
- respond in Vietnamese.
- keep code, identifiers, filenames, commands, API names, and standard technical terms in English when appropriate.
- when using an uncommon English technical term, briefly explain it in Vietnamese the first time.

## Explanation style

Assume the repository owner is learning Data Engineering.

When reporting technical decisions:
1. explain what the component does;
2. explain why it is needed;
3. explain why this option is chosen over obvious alternatives;
4. point out anything the owner should understand before approving it.

Do not only report implementation details.

## 1. Project mission

Build a cost-conscious Market & Regulatory Intelligence Platform for approximately 1,000 active users across:

- Vietnam (VN)
- United States (US)
- European Union (EU)
- China (CN)

Initial domains:

- Law & Policy
- Energy
- Technology
- Real Estate
- Finance

Primary product KPI:

> Detect relevant new information, classify it, match it to user interests, and make it available for notification within <= 60 minutes of source publication when the source is normally accessible.

Target operating cost:

- normal target: ~$30–60/month
- warning: $70/month
- critical: $85/month
- hard ceiling: < $100/month

## 2. Read before changing code

Before implementing a non-trivial task, read the relevant files:

1. `docs/ARCHITECTURE.md`
2. `docs/DATA_CONTRACTS.md`
3. `docs/ENGINEERING_RULES.md`
4. `docs/TESTING_STRATEGY.md`

For source connector work, also read:

5. `docs/SOURCE_CONNECTOR_GUIDE.md`

For task execution, use:

6. `docs/CODEX_TASK_TEMPLATE.md`

Do not silently change architecture or contracts because another design appears more sophisticated.

## 3. Data Engineer ownership

Primary DE-owned areas:

- source discovery support and source registry
- RSS / Atom ingestion
- REST / API ingestion
- HTML ingestion
- scheduling inputs and polling behavior
- fetch / parse / normalize
- deterministic deduplication
- AI classification adapter
- operational data persistence for sources/articles/classification
- personalized matching logic that produces alert candidates
- pipeline telemetry
- source health
- data quality
- latency measurement
- cost observability for data-side workloads
- Cloud Run data jobs
- BigQuery lightweight telemetry

Shared boundaries with Software Engineer:

- database migrations
- `UserPreference`
- `AlertCandidate`
- `AlertBatch`
- email delivery state
- end-to-end latency SLO

Software Engineer owns the user-facing product experience and notification delivery implementation unless a task explicitly says otherwise.

## 4. Current approved stack

### Runtime / libraries

- Python 3.12+
- `httpx`
- `feedparser`
- `BeautifulSoup` and/or `lxml`
- `pydantic`

Playwright is fallback-only and requires explicit justification.

### Platform

- Google Cloud Scheduler
- Google Cloud Run Jobs
- Supabase PostgreSQL
- BigQuery for lightweight telemetry only
- DeepSeek V4 Flash for classification only
- Amazon SES for email delivery
- Cloudflare Pages / Workers for frontend

## 5. Explicit non-goals for current phase

Do not introduce these unless the task explicitly changes architecture and documents the reason:

- Airflow
- Cloud Composer
- Kafka
- Pub/Sub
- Kubernetes
- dedicated always-on VM
- vector database
- embedding pipeline
- Elasticsearch / OpenSearch / Typesense
- recommendation ML
- full Story clustering
- paid news aggregator by default
- long LLM summaries for every article
- chatbot
- semantic search

Do not add infrastructure simply because it is common in larger systems.

## 6. Core pipeline

```text
Sources
  ↓
Source Registry
  ↓
RSS / REST / HTML
  ↓
Cloud Scheduler
  ↓
Cloud Run Job
  ↓
Fetch
  ↓
Parse
  ↓
Normalize
  ↓
Deduplicate
  ↓
DeepSeek V4 Flash classification
  ↓
Supabase PostgreSQL
  ↓
Rule-based matching
  ↓
Alert Candidate / Web Feed
  ↓
Notification pipeline
```

Telemetry is emitted separately to BigQuery.

## 7. Engineering principles

### 7.1 Idempotency

All ingestion paths must tolerate repeated execution.

Where applicable:

- use deterministic identifiers
- use upserts or equivalent safe writes
- prevent duplicate external side effects
- test repeated runs
- make retry behavior explicit

### 7.2 Metadata-first

By default, store:

- source metadata
- article metadata
- title
- description/snippet
- canonical URL
- publication/discovery timestamps
- classification metadata
- provenance

Do not store publisher full HTML or full article body by default.

### 7.3 Cheap deterministic logic before AI

Use deterministic approaches first for:

- URL normalization
- source item IDs
- hashes
- deduplication
- matching rules
- validation

Do not call an LLM when a deterministic rule solves the task reliably.

### 7.4 No hidden contract changes

If a task requires changing a shared contract:

1. explain why
2. update `docs/DATA_CONTRACTS.md`
3. identify affected services
4. update tests
5. call out migration/backward-compatibility impact

### 7.5 Observability

Important events should be observable without reading raw stack traces.

Use structured logs where practical and include useful context such as:

- `source_id`
- `article_id`
- pipeline stage
- attempt number
- duration
- status
- error category

Never log secrets.

## 8. Required pipeline timestamps

Preserve distinct timestamps:

- `published_at`
- `discovered_at`
- `classified_at`
- `matched_at`
- `email_queued_at`
- `email_sent_at`

Never replace `published_at` with ingestion time.

## 9. Classification constraints

DeepSeek V4 Flash is used for lightweight classification only.

Input should be minimal:

- title
- description/snippet
- source metadata

Expected output:

- market verification
- category
- topics
- confidence
- optional basic relevance metadata

Do not implement long-form rewriting or summarization unless explicitly requested.

## 10. Deduplication order

Prefer cheap deterministic checks first:

1. canonical URL
2. source item ID
3. content/title hash
4. normalized-title similarity

Do not introduce a vector database or LLM-based duplicate decision in the current phase.

## 11. Source acquisition preference

Prefer acquisition methods in this order when reasonable:

1. official REST/API
2. RSS/Atom
3. HTML
4. sitemap where appropriate
5. Playwright only as fallback

Respect source-specific rate limits and rights metadata.

## 12. Source rights

Fetching a page does not imply permission to store or redistribute full text.

Connector and persistence code must respect source registry rights fields.

Do not bypass technical or legal restrictions.

## 13. Definition of Done

A task is not done merely because code compiles.

Unless the task explicitly says otherwise, completion requires:

- implementation matches requested scope
- relevant tests added or updated
- relevant tests pass
- type/lint checks pass when configured
- error/retry paths considered
- idempotency considered for ingestion/state changes
- logs/telemetry considered
- no secret committed
- no unnecessary dependency introduced
- docs/contracts updated if behavior changed
- final report lists changed files, tests run, results, and remaining risks

## 14. Change discipline for Codex

Before editing a non-trivial task:

1. inspect relevant code and docs
2. summarize current behavior
3. propose the smallest viable change
4. identify files likely to change
5. identify risks
6. implement only within scope

Prefer small, reviewable diffs.

Do not refactor unrelated code in the same task.

## 15. When uncertain

If repository behavior conflicts with documentation:

- do not guess silently
- identify the conflict
- preserve existing production behavior unless the task explicitly asks to change it
- propose the smallest resolution
