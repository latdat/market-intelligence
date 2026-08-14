# AGENTS.md — Market & Regulatory Intelligence Platform

> Repository-level instructions for Codex.
>
> This file contains only guidance that should apply to almost every task.
> Detailed production review guidance lives in `docs/PRODUCTION_READINESS.md`.

---

## 0. Response language

For all explanations, plans, reviews, summaries, and completion reports:

- respond in Vietnamese;
- keep code, identifiers, filenames, commands, API names, and standard technical terms in English when appropriate;
- when using an uncommon English technical term, briefly explain it in Vietnamese the first time.

---

## 1. Explanation style

Assume the repository owner is learning Data Engineering.

When reporting technical decisions:

1. explain what the component does;
2. explain why it is needed;
3. explain why this option is chosen over obvious alternatives;
4. point out anything the owner should understand before approving it.

Do not only report implementation details.

Prefer concise explanations for simple tasks and deeper reasoning for architecture, data contracts, production risk, or breaking changes.

---

## 2. Project mission

Build a cost-conscious **Market & Regulatory Intelligence Platform** for approximately **1,000 active users** across:

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

> Detect relevant new information, classify it, match it to user interests, and make it available for notification within **<= 60 minutes of source publication** when the source is normally accessible.

Target operating cost:

- normal target: **~$30–60/month**
- warning: **$70/month**
- critical: **$85/month**
- hard ceiling: **< $100/month**

Do not optimize for hypothetical millions of users at the expense of the current product requirements.

---

## 3. Implementation truth

**Never confuse documentation, architecture diagrams, roadmaps, TODOs, prompts, issues, or plans with implemented functionality.**

Classify capabilities as:

- `Implemented`
- `Partially implemented`
- `Planned / documented only`
- `Not found`
- `Not verified`

A claim is considered implemented only when supported by evidence such as:

- source code;
- configuration;
- database schema or migrations;
- infrastructure definitions;
- automated tests;
- executable commands;
- CI/CD configuration;
- reproducible runtime output or logs.

When reporting repository status, explicitly distinguish what exists from what is merely planned.

Never fabricate:

- implementation status;
- commands executed;
- test results;
- deployment status;
- files;
- services;
- tables;
- endpoints;
- infrastructure;
- runtime evidence.

---

## 4. Read before changing code

Before implementing a non-trivial task, read the relevant project documents.

Core references:

1. `docs/ARCHITECTURE.md`
2. `docs/DATA_CONTRACTS.md`
3. `docs/ENGINEERING_RULES.md`
4. `docs/TESTING_STRATEGY.md`

For source connector work, also read:

5. `docs/SOURCE_CONNECTOR_GUIDE.md`

For task execution, use:

6. `docs/CODEX_TASK_TEMPLATE.md`

For changes involving production behavior, deployment, infrastructure, security, persistence, reliability, observability, external integrations, backup/recovery, performance, or release readiness, also read:

7. `docs/PRODUCTION_READINESS.md`

Do not read every document for every tiny change. Route to the smallest relevant set.

Do not silently change architecture or contracts because another design appears more sophisticated.

---

## 5. Repository inspection before non-trivial changes

Before editing:

1. inspect `git status` and current branch;
2. note existing uncommitted changes;
3. inspect relevant code, tests, configuration, migrations, infrastructure, and documentation;
4. determine actual current behavior from implementation evidence;
5. identify affected components, contracts, persistence, external dependencies, and operational impact;
6. propose the smallest viable change;
7. implement only within requested scope.

Prefer small, reviewable diffs.

Do not refactor unrelated code in the same task.

Do not overwrite or revert unrelated user changes.

---

## 6. Source-of-truth priority

When repository sources disagree, use this order:

1. reproducible runtime evidence;
2. automated tests;
3. executable code and configuration;
4. database / infrastructure definitions;
5. API and data contracts;
6. repository documentation;
7. roadmap / design documents;
8. assumptions.

If ambiguity remains, report it instead of silently inventing behavior.

If implementation conflicts with documentation, do not silently “fix” either side. Identify the conflict and determine which source is authoritative for the requested task.

---

## 7. Data Engineer ownership

Primary DE-owned areas:

- source discovery support and source registry;
- RSS / Atom ingestion;
- REST / API ingestion;
- HTML ingestion;
- scheduling inputs and polling behavior;
- fetch / parse / normalize;
- deterministic deduplication;
- AI classification adapter;
- operational data persistence for sources/articles/classification;
- personalized matching logic that produces alert candidates;
- pipeline telemetry;
- source health;
- data quality;
- latency measurement;
- cost observability for data-side workloads;
- Cloud Run data jobs;
- BigQuery lightweight telemetry.

Shared boundaries with Software Engineer:

- database migrations;
- `UserPreference`;
- `AlertCandidate`;
- `AlertBatch`;
- email delivery state;
- end-to-end latency SLO.

Software Engineer owns the user-facing product experience and notification delivery implementation unless a task explicitly says otherwise.

Do not cross ownership boundaries silently. If a DE task requires changing a shared SWE contract, call it out.

---

## 8. Current approved stack

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

Use the project's existing libraries and patterns before introducing alternatives.

---

## 9. Explicit non-goals for the current phase

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

Any architecture addition must be justified by a present requirement, measured bottleneck, reliability requirement, or clearly approved product need.

---

## 10. Core pipeline

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

When changing the pipeline, identify exactly which stage(s) are affected.

---

## 11. Core engineering principles

### 11.1 Smallest viable change

- Solve the requested problem with the smallest correct change.
- Preserve backward compatibility unless the task explicitly permits a breaking change.
- Avoid broad rewrites.
- Avoid hidden coupling.
- Keep interfaces explicit.
- Do not introduce new dependencies without clear value.

### 11.2 Idempotency

All ingestion paths must tolerate repeated execution.

Where applicable:

- use deterministic identifiers;
- use upserts or equivalent safe writes;
- prevent duplicate external side effects;
- test repeated runs;
- make retry behavior explicit.

Retrying a non-idempotent operation requires explicit reasoning.

### 11.3 Metadata-first

By default, store:

- source metadata;
- article metadata;
- title;
- description/snippet;
- canonical URL;
- publication/discovery timestamps;
- classification metadata;
- provenance.

Do not store publisher full HTML or full article body by default.

### 11.4 Cheap deterministic logic before AI

Use deterministic approaches first for:

- URL normalization;
- source item IDs;
- hashes;
- deduplication;
- matching rules;
- validation.

Do not call an LLM when deterministic logic solves the task reliably.

### 11.5 Explicit failure handling

Assume that:

- network requests time out;
- third-party APIs fail or rate-limit;
- processes restart;
- a scheduled job may run more than once;
- partial work may already be persisted.

Where relevant use:

- explicit timeouts;
- bounded retries;
- backoff/jitter;
- idempotent state changes;
- categorized errors;
- safe partial-failure behavior.

Never create unbounded retries.

### 11.6 No hidden contract changes

If a task requires changing a shared contract:

1. explain why;
2. update `docs/DATA_CONTRACTS.md`;
3. identify affected services;
4. update tests;
5. call out migration and backward-compatibility impact.

### 11.7 No hidden security weakening

Never:

- disable validation merely to make tests pass;
- broaden authorization without explicit requirement;
- expose secrets;
- hard-code production credentials;
- leak sensitive data in logs;
- trust client-side validation as a security boundary.

### 11.8 Production claims require evidence

Do not call a component or the whole system `production-ready` based only on:

- local success;
- compilation;
- passing one happy-path test;
- architecture documentation;
- successful deployment without operational evidence.

Use `docs/PRODUCTION_READINESS.md` for detailed review criteria.

---

## 12. Required pipeline timestamps

Preserve distinct timestamps:

- `published_at`
- `discovered_at`
- `classified_at`
- `matched_at`
- `email_queued_at`
- `email_sent_at`

Never replace `published_at` with ingestion time.

Timestamp changes are contract changes and require compatibility review.

The primary latency KPI should be derivable from these timestamps.

---

## 13. Classification constraints

DeepSeek V4 Flash is used for lightweight classification only.

Input should be minimal:

- title;
- description/snippet;
- source metadata.

Expected output:

- market verification;
- category;
- topics;
- confidence;
- optional basic relevance metadata.

Do not implement long-form rewriting or summarization unless explicitly requested.

Treat LLM output as untrusted input:

- validate structured output;
- handle malformed responses;
- bound retries;
- track latency/cost where material;
- do not let model output bypass authorization or data validation.

---

## 14. Deduplication order

Prefer cheap deterministic checks first:

1. canonical URL;
2. source item ID;
3. content/title hash;
4. normalized-title similarity.

Do not introduce a vector database or LLM-based duplicate decision in the current phase.

Deduplication must remain safe across repeated runs.

---

## 15. Source acquisition preference

Prefer acquisition methods in this order when reasonable:

1. official REST/API;
2. RSS/Atom;
3. HTML;
4. sitemap where appropriate;
5. Playwright only as fallback.

Respect:

- source-specific rate limits;
- rights metadata;
- robots/access restrictions where applicable;
- external API quotas;
- timeouts and failure behavior.

Do not bypass technical or legal restrictions.

---

## 16. Source rights

Fetching a page does not imply permission to store or redistribute full text.

Connector and persistence code must respect source registry rights fields.

By default:

- prefer metadata/snippet storage;
- preserve provenance;
- preserve canonical URLs;
- do not remove source attribution;
- do not silently expand collection scope beyond approved rights.

---

## 17. Observability baseline

Important pipeline events should be observable without reading raw stack traces.

Use structured logs where practical and include useful context such as:

- `source_id`
- `article_id`
- pipeline stage
- attempt number
- duration
- status
- error category

Never log secrets.

For pipeline health, consider metrics such as:

- source fetch success/failure;
- ingestion latency;
- classification latency;
- matching latency;
- end-to-end freshness;
- rejected/invalid records;
- retry counts;
- notification success/failure;
- external API usage/cost.

A green scheduler/job status is not sufficient evidence that downstream data is correct or fresh.

---

## 18. Database and migration discipline

For persistence changes consider:

- schema constraints;
- indexes;
- transactions;
- connection behavior;
- idempotent writes;
- migration ordering;
- backward compatibility;
- rollback compatibility;
- realistic data volume;
- backfill requirements.

Before a schema change, answer:

1. can old and new application versions coexist with the new schema?
2. could the migration lock or rewrite a large table?
3. can application rollback still work?
4. if the migration is irreversible, what is the recovery plan?

Do not perform destructive migrations casually.

---

## 19. Cost discipline

Every material architecture or data-processing change should consider cost against the project budget.

Pay attention to:

- Cloud Run executions;
- scheduler frequency;
- PostgreSQL usage;
- BigQuery query/storage usage;
- DeepSeek API calls/tokens;
- Amazon SES volume;
- Cloudflare usage;
- third-party APIs;
- logging/telemetry volume;
- network egress.

Prefer measurable estimates.

Do not describe a design as “cheap” without a basis when cost is material to the decision.

---

## 20. Testing expectations

Use the test taxonomy already present in the repository.

Typical layers:

- unit;
- integration;
- e2e;
- fixtures/data-quality tests where relevant.

For bug fixes, add a regression test when practical.

For important behavior, test at the lowest reliable layer that proves it.

For ingestion/state-changing logic, test repeated execution when relevant.

For external APIs, avoid brittle live-network tests when deterministic fixtures or mocks are sufficient.

---

## 21. Validation commands

Use project-defined commands from `pyproject.toml`, README, CI, or existing scripts as the source of truth.

Current Python quality checks may include, when configured:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Run only checks relevant to the change, plus the broader suite when risk warrants it.

Never report a command as run unless it was actually executed.

---

## 22. Definition of Done

A task is not done merely because code compiles or one example works.

Unless the task explicitly says otherwise, completion requires applicable items below:

- [ ] implementation matches requested scope;
- [ ] actual repository state was verified;
- [ ] relevant tests were added or updated;
- [ ] relevant tests pass;
- [ ] lint/type/build checks pass when configured and relevant;
- [ ] error/retry paths were considered;
- [ ] idempotency was considered for ingestion/state changes;
- [ ] security impact was considered;
- [ ] data/schema/contract impact was considered;
- [ ] logs/telemetry were considered;
- [ ] deployment/rollback impact was considered when material;
- [ ] performance/cost impact was considered when material;
- [ ] no secret or sensitive data was committed;
- [ ] no unnecessary dependency/infrastructure was introduced;
- [ ] docs/contracts were updated if behavior changed;
- [ ] remaining risks and unverified assumptions are explicitly reported.

Use `N/A` where an item truly does not apply. Do not fabricate evidence to satisfy the checklist.

---

## 23. Required completion report

After a non-trivial implementation task, report:

### 1. Summary
What was implemented or changed.

### 2. Actual repository state
What was verified to exist before/after the change.

Explicitly identify anything that remains only planned/documented.

### 3. Changed files
Files created, modified, or deleted.

### 4. Validation performed
Exact commands/checks actually run and results.

### 5. Production impact
Briefly cover, where relevant:

- security;
- reliability;
- data/contracts;
- observability;
- deployment/rollback;
- performance/cost.

Use `No material impact` where justified.

### 6. Remaining risks / limitations
Known gaps and unverified assumptions.

### 7. Recommended next step
The smallest logical next task. Do not over-scope.

---

## 24. When asked to review the whole system

If asked to:

- review the system;
- inspect the project;
- audit production readiness;
- compare implementation with architecture;
- decide whether it can be deployed;

then:

1. do not immediately modify code;
2. inventory the implemented system;
3. derive the actual request/data flow from code and configuration;
4. compare implementation against documented architecture;
5. identify discrepancies;
6. read `docs/PRODUCTION_READINESS.md`;
7. evaluate relevant production dimensions;
8. separate confirmed issues from likely risks and unverified areas;
9. prioritize remediation by severity;
10. propose the smallest reasonable remediation plan.

Use these priorities:

- `P0 — Release blocker`: severe security breach, unrecoverable data loss, or critical systemic failure risk.
- `P1 — Must fix before production`: material security, reliability, correctness, or recovery issue.
- `P2 — Should fix soon`: important hardening issue with acceptable temporary workaround.
- `P3 — Improvement`: maintainability, efficiency, developer experience, or future-scale improvement.

Do not recommend a rewrite unless evidence shows incremental remediation is unreasonable.

---

## 25. Prohibited behavior

Codex must not:

- claim planned components are implemented;
- fabricate tests or command output;
- hide failing tests;
- delete/weaken tests merely to make CI green;
- commit secrets;
- expose production credentials;
- silently weaken validation/security;
- perform destructive migrations without explicit justification;
- introduce breaking API/schema changes without calling them out;
- refactor unrelated code in the same task;
- add infrastructure for hypothetical scale without evidence;
- bypass source rights or technical/legal restrictions;
- call the system production-ready based only on local success.

---

## 26. Preferred engineering loop

Use:

**Understand → Verify → Change → Test → Observe → Evaluate risk → Report**

Not:

**Generate code → see it run once → declare done**

A successful demo proves a happy path.

Production confidence requires evidence about:

- normal operation;
- invalid input;
- retries and repeated execution;
- dependency failures;
- security boundaries;
- load/capacity when material;
- deployment and rollback;
- data recovery;
- observability;
- long-term operating cost.

