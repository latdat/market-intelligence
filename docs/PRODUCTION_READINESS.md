# Production Readiness — Market & Regulatory Intelligence Platform

> Detailed production review guidance for this repository.
>
> Read this document when a task affects production behavior, security, persistence,
> external integrations, deployment, reliability, observability, performance,
> backup/recovery, release readiness, or when reviewing the system as a whole.
>
> This document is a **review framework**, not proof that any capability is implemented.
> Always verify repository/runtime evidence before making implementation claims.

---

## 1. Purpose

The objective is not to make the system “enterprise-looking”.

The objective is to ensure that the current product can safely and economically serve its actual target:

- approximately 1,000 active users;
- VN / US / EU / CN;
- Law & Policy, Energy, Technology, Real Estate, Finance;
- relevant information available for notification within <= 60 minutes when the source is normally accessible;
- normal operating target ~$30–60/month;
- hard ceiling < $100/month.

Production readiness must therefore be:

- risk-based;
- evidence-based;
- proportional to current scale;
- cost-conscious;
- simple enough to operate.

Do not introduce large-system infrastructure unless present requirements or measurements justify it.

---

## 2. What “production-ready” means

A production system should provide adequate confidence across these dimensions:

1. correctness;
2. security;
3. reliability;
4. data integrity;
5. observability;
6. deployment safety;
7. recoverability;
8. performance/capacity;
9. operational ownership;
10. cost sustainability;
11. source-rights/privacy compliance.

Passing local tests is useful evidence, but not sufficient evidence for the whole system.

---

## 3. Evidence standard

Classify every reviewed capability as:

- `Implemented`
- `Partially implemented`
- `Planned / documented only`
- `Not found`
- `Not verified`

Evidence may include:

- source code;
- configuration;
- migrations/schemas;
- infrastructure definitions;
- automated tests;
- CI/CD;
- executable commands;
- runtime logs/metrics;
- deployed configuration;
- restore/load/smoke-test evidence.

Do not treat documentation as implementation evidence.

Do not infer operational readiness from architecture diagrams alone.

---

# 4. Production review workflow

When reviewing a release or the whole system:

1. inventory actual implemented components;
2. reconstruct actual request/data flow;
3. identify stateful components and external dependencies;
4. identify user-critical paths;
5. identify potential failure modes;
6. inspect test/validation evidence;
7. inspect observability evidence;
8. inspect deployment and rollback paths;
9. inspect persistence, backup, and recovery;
10. inspect scale/cost assumptions;
11. classify findings by severity;
12. produce the smallest prioritized remediation plan.

Use:

- `P0 — Release blocker`
- `P1 — Must fix before production`
- `P2 — Should fix soon`
- `P3 — Improvement`

---

# 5. Product-level readiness

## 5.1 Critical user journey

At minimum, validate the end-to-end path:

```text
Source publication
  ↓
Discovery / fetch
  ↓
Parse / normalize
  ↓
Deduplicate
  ↓
Classification
  ↓
Persistence
  ↓
Preference matching
  ↓
Alert candidate
  ↓
Email queue/delivery
  ↓
User receives useful information
```

The pipeline is not healthy merely because individual jobs return success.

Questions:

- Can a newly published relevant item reach the correct user?
- Can the system explain where an item is stuck?
- Is `published_at` preserved correctly?
- Is end-to-end latency measurable?
- Can duplicate polling create duplicate user notifications?
- Can failure at one stage be retried safely?
- Can irrelevant content be prevented from producing alerts?

## 5.2 Product KPI

The primary target is:

> Relevant new information should become available for notification within <= 60 minutes of source publication when the source is normally accessible.

Measure at least:

```text
discovery_latency = discovered_at - published_at
classification_latency = classified_at - discovered_at
matching_latency = matched_at - classified_at
queue_latency = email_queued_at - matched_at
delivery_latency = email_sent_at - email_queued_at
end_to_end_latency = email_sent_at - published_at
```

When publication timestamp is unavailable/unreliable, document the fallback measurement.

Do not silently substitute ingestion time for publication time.

---

# 6. Architecture and dependency review

Maintain an actual dependency map, not just a conceptual diagram.

Current expected major dependencies:

- source websites/APIs/RSS feeds;
- Google Cloud Scheduler;
- Google Cloud Run Jobs;
- Supabase PostgreSQL;
- BigQuery telemetry;
- DeepSeek V4 Flash;
- Amazon SES;
- Cloudflare Pages/Workers.

For each dependency, answer:

- What feature depends on it?
- Is it user-critical?
- What happens if it is unavailable?
- What timeout applies?
- What retry behavior applies?
- What quota/rate limit exists?
- What cost grows with usage?
- How is failure observed?
- Is there a fallback or graceful degradation?
- Is the dependency optional or mandatory?

Avoid allowing an optional dependency to unnecessarily take down the whole pipeline.

---

# 7. Correctness

Review:

- input validation;
- parsing correctness;
- normalization rules;
- timestamps/timezones;
- deduplication;
- classification validation;
- preference matching;
- persistence;
- external side effects;
- partial failures;
- repeated execution;
- concurrent execution where relevant.

## 7.1 Happy path is not enough

Test relevant cases such as:

- malformed feeds;
- missing fields;
- invalid HTML;
- unexpected content type;
- missing publication timestamp;
- duplicate source items;
- redirected canonical URLs;
- duplicate job invocation;
- LLM malformed JSON;
- LLM timeout;
- database timeout;
- temporary source failure;
- SES failure;
- invalid user preference;
- stale/late-arriving source content.

## 7.2 Invariants

Useful invariants may include:

- the same source item is not persisted twice under equivalent identity;
- repeated job execution does not produce duplicate external side effects;
- `published_at` is not overwritten by ingestion time;
- persisted classification conforms to the contract;
- an `AlertCandidate` can be traced to source article and preference rule;
- notification state transitions are valid and auditable.

---

# 8. Security

Use OWASP ASVS / OWASP Top 10 as baseline references for web-facing security.

Review at minimum:

- authentication;
- authorization;
- least privilege;
- server-side validation;
- injection risks;
- output encoding where applicable;
- session/token handling;
- CSRF where applicable;
- CORS;
- file uploads if introduced;
- rate limiting;
- abuse controls;
- secret management;
- dependency vulnerabilities;
- transport security;
- debug/admin endpoints;
- sensitive data in logs;
- auditability.

Rules:

- authentication does not imply authorization;
- client-side validation is not a security boundary;
- secrets must not live in source control;
- debug features must not expose sensitive internals;
- failures should not leak credentials or sensitive payloads.

## 8.1 Cloud/service credentials

Check:

- Google service accounts use least privilege;
- Supabase credentials have appropriate scope;
- DeepSeek keys are server-side only;
- SES credentials are server-side only;
- Cloudflare secrets use the platform's secret/config mechanism;
- CI credentials are scoped and protected;
- local `.env` files are ignored from Git.

## 8.2 Secret scanning

Before production release:

- inspect tracked files for secrets;
- inspect examples/test fixtures;
- inspect CI logs/config;
- verify `.gitignore`;
- rotate any credential that was previously committed.

Removing a secret from the latest commit is not sufficient if it remains usable in Git history.

---

# 9. Source rights and content handling

Fetching content does not automatically grant permission to store or redistribute full text.

Review:

- source registry rights metadata;
- permitted acquisition method;
- storage scope;
- redistribution/display scope;
- attribution;
- retention;
- rate limits;
- technical restrictions;
- contractual/API terms where applicable.

Default product posture:

- metadata-first;
- snippet/description where allowed;
- canonical URL;
- provenance;
- no full publisher HTML/body by default.

Do not bypass access controls or restrictions.

---

# 10. Reliability and failure handling

Assume:

- network calls fail;
- services return 429/5xx;
- jobs restart;
- scheduler invokes twice;
- DB connections fail;
- dependencies become slow;
- credentials expire;
- downstream email delivery fails.

## 10.1 Timeouts

Every external network call should have intentional timeouts.

Avoid relying on unlimited/default waits when a timeout could hold a job indefinitely.

Consider:

- connect timeout;
- read timeout;
- total request timeout where relevant.

## 10.2 Retries

Retries should be:

- bounded;
- applied only to appropriate transient failures;
- backoff-based;
- jittered when many workers could retry together;
- observable.

Do not retry permanent validation failures indefinitely.

## 10.3 Idempotency

Critical for:

- source ingestion;
- article persistence;
- classification state updates;
- matching;
- email enqueue/send state.

Ask:

- What happens if this operation runs twice?
- What deterministic key identifies the operation?
- Can duplicate side effects occur?
- Does retry after partial persistence remain safe?

## 10.4 Graceful degradation

Examples:

- one source being unavailable should not prevent healthy sources from ingesting;
- classification provider outage may delay classification without corrupting raw metadata;
- telemetry failure should not necessarily block primary pipeline success;
- optional frontend features should not block ingestion.

Whether degradation is acceptable must be explicit.

---

# 11. Data correctness and persistence

Review:

- primary keys;
- unique constraints;
- foreign keys where useful;
- nullability;
- data types;
- indexes;
- transactions;
- upserts;
- duplicate handling;
- timestamps;
- retention;
- deletion;
- provenance;
- schema evolution.

## 11.1 Required timestamps

Preserve:

- `published_at`
- `discovered_at`
- `classified_at`
- `matched_at`
- `email_queued_at`
- `email_sent_at`

Define timezone behavior consistently, preferably with timezone-aware UTC storage unless the existing contract says otherwise.

## 11.2 Data lineage

A persisted alert should be traceable to:

```text
Alert
  → matching decision
  → classification
  → normalized article
  → source
```

Enough metadata should exist to debug why a user received an alert.

## 11.3 Data quality

Consider checks for:

- missing canonical URL;
- missing/invalid timestamps;
- impossible future timestamps;
- unknown market/category;
- duplicate identity;
- malformed classification;
- stale source health;
- unexpectedly low/high item counts.

Do not equate “job success” with “data quality success”.

---

# 12. Database migrations

Every schema change should answer:

1. Can old application instances coexist with the new schema?
2. Can new code work during deployment transition?
3. Does the migration lock or rewrite significant data?
4. Is a backfill required?
5. Should schema change and backfill be separate?
6. Can application rollback still work?
7. Is the migration reversible?
8. If irreversible, what is the recovery plan?

Prefer backward-compatible expansion before contraction.

Examples:

```text
safer sequence:
add new nullable column
→ deploy code that can use old/new state
→ backfill
→ enforce constraint
→ remove obsolete field in later release
```

Avoid destructive schema changes in the same release as dependent code unless explicitly justified.

---

# 13. Classification / LLM production readiness

DeepSeek V4 Flash is currently intended for lightweight classification only.

Review:

- prompt/input size;
- timeout;
- retry;
- malformed response;
- structured-output validation;
- unknown category handling;
- confidence semantics;
- cost per article;
- quota;
- provider outage;
- provider/model version change;
- privacy/data leakage;
- prompt injection from untrusted source text.

Rules:

- model output is untrusted input;
- validate before persistence;
- never let model text bypass authorization;
- do not send secrets to the model;
- deterministic logic should handle deterministic tasks;
- keep classification input minimal.

## 13.1 Failure modes

Define behavior for:

- timeout;
- rate limit;
- 5xx;
- malformed JSON;
- missing required field;
- unsupported label;
- low confidence;
- response exceeding expected size.

Decide whether the article becomes:

- retryable;
- deferred;
- quarantined;
- classified as unknown;
- manually inspectable.

Do not silently discard failed classification items without telemetry.

---

# 14. Deduplication readiness

Current preferred order:

1. canonical URL;
2. source item ID;
3. content/title hash;
4. normalized-title similarity.

Review:

- deterministic identity;
- URL normalization;
- redirects/tracking parameters;
- same article across repeated polls;
- updated article content;
- cross-source duplicates if in scope;
- false positives;
- false negatives.

Current phase does not require vector DB or LLM-based deduplication.

Measure deterministic approaches before adding semantic infrastructure.

---

# 15. Observability

A production system should be diagnosable without adding emergency logging after the incident.

OpenTelemetry's observability model emphasizes telemetry signals such as:

- logs;
- metrics;
- traces.

This project may use a simpler implementation appropriate to scale; OpenTelemetry itself is not mandatory.

## 15.1 Structured logs

Useful fields include:

- timestamp;
- level;
- `source_id`;
- `article_id`;
- job/run ID;
- pipeline stage;
- attempt number;
- duration;
- status;
- error category.

Do not log:

- passwords;
- API keys;
- access tokens;
- private keys;
- full sensitive payloads without explicit need.

## 15.2 Metrics

Candidate pipeline metrics:

### Source health
- fetch success rate;
- fetch error rate;
- last successful fetch;
- item count per fetch;
- parse failures.

### Freshness
- discovery latency;
- classification latency;
- matching latency;
- notification latency;
- end-to-end latency.

### Processing
- articles discovered;
- articles normalized;
- duplicates rejected;
- classification failures;
- matching candidates;
- queued emails;
- sent emails;
- failed emails;
- retry counts.

### Dependencies
- DeepSeek latency/error/quota;
- Supabase latency/error;
- SES delivery/error;
- Cloud Run job duration;
- scheduler invocation success.

### Cost
- LLM requests/tokens;
- BigQuery usage;
- Cloud Run execution;
- email volume;
- telemetry volume.

## 15.3 Alerts

Alerts should be actionable.

Prefer alerting on symptoms such as:

- freshness SLO violation;
- source group stops ingesting;
- sustained error rate;
- notification backlog;
- quota near exhaustion;
- cost threshold crossed.

Avoid noisy alerts based on arbitrary resource thresholds without user impact.

---

# 16. SLI / SLO thinking

Useful Service Level Indicators (SLIs) may include:

- source fetch success ratio;
- relevant-item freshness;
- classification success ratio;
- notification delivery success ratio;
- end-to-end latency.

Example product SLO candidate:

> X% of valid relevant items from normally accessible configured sources are processed and made available for notification within 60 minutes of publication.

Do not invent `X%` without product approval/evidence.

Track SLO definitions separately from implementation claims.

---

# 17. Performance and capacity

Do not infer production capacity from a successful local run.

Review:

- expected sources;
- polling frequency;
- items/source/run;
- articles/hour;
- classification calls/hour;
- PostgreSQL writes/queries;
- user preference matching workload;
- emails/hour;
- Cloud Run duration/concurrency;
- provider quotas;
- storage growth.

## 17.1 Latency

When useful, observe distributions:

- p50;
- p95;
- p99.

Average latency alone can hide tail failures.

## 17.2 Load testing

Use production-like tests when the risk warrants them.

Possible targets:

- ingestion of many sources in one polling window;
- burst of newly published items;
- database connection pressure;
- matching against ~1,000 active users;
- email batching/queueing.

Do not perform expensive scale engineering before identifying a real bottleneck.

---

# 18. Scheduling and concurrency

Google Cloud Scheduler + Cloud Run Jobs means repeated or overlapping execution must be considered.

Review:

- schedule frequency;
- max job duration;
- overlapping runs;
- duplicate invocation;
- per-source lock/claim behavior if needed;
- retries;
- job timeout;
- partial completion;
- source fairness.

The system should not rely on “the scheduler only runs exactly once”.

Where overlap is possible, ensure idempotency or explicit coordination.

---

# 19. Third-party dependency readiness

For each external provider, document:

- purpose;
- owner/account;
- credentials;
- timeout;
- retry;
- rate limit/quota;
- expected cost;
- outage behavior;
- monitoring;
- data/privacy impact.

## 19.1 Source websites/APIs

Risks:

- feed format changes;
- anti-bot changes;
- rate limiting;
- source outage;
- publication timestamp changes;
- removed content.

Connector failures should be isolated by source where practical.

## 19.2 DeepSeek

Risks:

- latency;
- malformed output;
- quota;
- model behavior changes;
- cost;
- outage.

## 19.3 Amazon SES

Risks:

- credentials;
- sending quota;
- bounces/complaints;
- suppression;
- delivery failures;
- abuse.

Do not treat “API accepted message” as guaranteed inbox delivery.

## 19.4 Supabase

Risks:

- DB availability;
- connection exhaustion;
- migration errors;
- credentials;
- storage growth;
- backup/recovery.

## 19.5 BigQuery

BigQuery is for lightweight telemetry only in the approved architecture.

Do not move operational application state into BigQuery without an explicit architecture change.

---

# 20. Deployment readiness

Prefer reproducible deployment.

Review:

- deterministic build;
- dependency control;
- tests;
- lint/type checks;
- environment-specific configuration;
- secrets injection;
- infrastructure config;
- migrations;
- smoke tests;
- version identification;
- rollback.

Avoid manual production edits that are not captured in source/configuration.

## 20.1 Environment separation

Production should not silently share mutable state or credentials with development.

Consider:

- separate secrets;
- separate DB/config where feasible;
- staging/test data;
- limited production access.

Do not copy sensitive production data into local/test environments without need and controls.

---

# 21. CI/CD

When CI/CD exists, production-oriented changes should use it rather than bypass it.

Useful gates may include:

```text
install
  ↓
lint / format
  ↓
type check
  ↓
unit tests
  ↓
integration tests
  ↓
build
  ↓
migration validation
  ↓
deploy
  ↓
smoke check
```

Not every task requires every stage.

Use repository-defined commands as the source of truth.

Never weaken CI simply to make a failing change pass.

---

# 22. Release strategy and rollback

A deployment strategy is incomplete without a rollback strategy.

For each risky release, know:

- what version is currently deployed;
- what artifact/config is being released;
- how to detect failure;
- how to stop/disable the release;
- how to restore the previous application version;
- whether DB changes are backward-compatible;
- how long rollback should take operationally.

Higher-risk changes may justify:

- staged rollout;
- canary;
- feature flag;
- blue/green;
- rolling deployment.

Do not add these mechanisms automatically if current risk/scale does not justify them.

---

# 23. Backup and recovery

A backup job reporting success is not sufficient.

For important persistent data:

- define what must be backed up;
- automate backup where appropriate;
- protect/encrypt backup;
- define retention;
- define restore procedure;
- periodically test restore;
- verify restored data is usable.

Where material define:

- **RPO (Recovery Point Objective)**: maximum acceptable data loss;
- **RTO (Recovery Time Objective)**: maximum acceptable recovery time.

## 23.1 Restore evidence

A restore test should verify:

- data can be restored;
- data is readable;
- key integrity checks pass;
- restoration completes within expected time;
- recovered data age meets RPO.

Production recovery claims require restore evidence, not only backup existence.

---

# 24. Disaster and incident thinking

Consider realistic incidents:

- Supabase unavailable;
- accidental destructive migration;
- corrupted application data;
- source connector starts producing invalid records;
- DeepSeek unavailable;
- SES quota exhausted;
- Cloud Run deployment broken;
- secret leaked;
- cost spikes unexpectedly;
- all jobs stop being scheduled.

For critical incidents, know:

- detection mechanism;
- owner;
- immediate mitigation;
- rollback/recovery;
- communication;
- evidence to preserve.

After significant incidents, convert lessons into:

- tests;
- alerts;
- runbooks;
- guardrails;
- updated documentation.

---

# 25. Operational ownership

Before launch, identify:

- service owner;
- data pipeline owner;
- database/migration owner;
- email delivery owner;
- alert destination;
- recovery authority;
- who can rotate credentials;
- who can roll back deployment.

For a two-person team, ownership may be lightweight but should not be ambiguous.

---

# 26. Runbooks

Create runbooks only for meaningful operational procedures.

Candidate runbooks:

- source ingestion stopped;
- source format changed;
- Cloud Run job repeatedly failing;
- DB unavailable;
- migration rollback/recovery;
- DeepSeek outage/quota;
- SES delivery failure/quota;
- secret rotation;
- backup restore;
- cost anomaly.

A useful runbook answers:

1. how do we detect the problem?
2. how do we confirm scope?
3. what is the safest immediate action?
4. how do we recover?
5. how do we verify recovery?
6. when should we escalate?

---

# 27. Cost readiness

Target:

- normal: ~$30–60/month;
- warning: $70/month;
- critical: $85/month;
- hard ceiling: < $100/month.

Track material contributors:

- Cloud Run;
- Cloud Scheduler;
- Supabase;
- BigQuery;
- DeepSeek;
- SES;
- Cloudflare;
- external APIs;
- logging/telemetry;
- egress/storage.

## 27.1 Cost guardrails

Before architecture changes estimate:

```text
unit workload
× frequency
× unit cost
≈ monthly cost
```

Examples:

```text
sources
× polls/day
× days/month
```

```text
articles classified/month
× average model cost/article
```

```text
emails/month
× delivery cost
```

Use measurements once production data exists.

Avoid “cost optimization” that materially harms freshness/reliability unless explicitly approved.

---

# 28. Privacy and user data

Identify user data such as:

- email addresses;
- preferences/interests;
- notification history;
- account identifiers;
- operational logs linked to users.

Consider:

- least collection;
- access control;
- retention;
- deletion;
- export;
- logging;
- backups;
- auditability.

Do not store sensitive data merely because it may be useful later.

---

# 29. Email production readiness

Before real-user email delivery review:

- verified sending identity/domain;
- sender configuration;
- SES quota;
- bounce handling;
- complaint handling;
- suppression behavior;
- duplicate prevention;
- user preference enforcement;
- unsubscribe/opt-out requirements where applicable;
- rate control;
- retry behavior;
- delivery telemetry.

Do not send repeated alerts for the same article/user because a job was retried.

Separate:

- alert candidate creation;
- batching;
- enqueue;
- send attempt;
- provider acceptance;
- final delivery/bounce where observable.

---

# 30. Source health

Each source should have enough telemetry to answer:

- when was it last checked?
- when did it last succeed?
- how many items were returned?
- when was the newest published item?
- what is the current error state?
- has the source format likely changed?
- is the source stale relative to expected cadence?

A system can appear healthy while silently missing news if source health is not monitored.

---

# 31. Data freshness and completeness

Freshness is a primary product concern.

Track:

- delayed discovery;
- delayed classification;
- delayed matching;
- delayed email enqueue;
- missing source runs;
- unusual zero-item runs.

Consider completeness checks such as:

- configured sources expected vs actually polled;
- source runs successful vs failed;
- discovered items vs parsed items;
- parsed items vs persisted items.

A sudden drop to zero can be a pipeline failure, not “no news”.

---

# 32. Testing strategy for production risks

## 32.1 Unit tests

Good for:

- URL normalization;
- deterministic IDs;
- parsers;
- validators;
- dedup logic;
- matching rules;
- timestamp logic.

## 32.2 Integration tests

Good for:

- persistence;
- migration behavior;
- repository/data access;
- external adapter boundaries;
- pipeline stage integration.

## 32.3 End-to-end tests

Focus on a few critical journeys rather than many brittle scenarios.

Example:

```text
fixture source item
→ ingestion
→ normalize
→ classify adapter fixture
→ persist
→ match preference
→ create alert candidate
```

## 32.4 Failure tests

Test selected failure modes:

- timeout;
- malformed response;
- duplicate execution;
- partial DB failure;
- retry;
- invalid data.

## 32.5 Load tests

Use only when production risk warrants them.

---

# 33. AI-generated code review

When code was generated rapidly with AI/Codex, explicitly review areas that happy-path demos often miss:

- authorization;
- validation;
- secrets;
- transaction boundaries;
- concurrency;
- idempotency;
- retries;
- timeouts;
- resource leaks;
- error handling;
- migration compatibility;
- dependency behavior;
- logs containing sensitive values.

“Generated quickly” is not itself a problem.

“Unreviewed and unverified” is the production risk.

---

# 34. Production release gate

Use this checklist before describing the system as production-ready.

## 34.1 Product

- [ ] Critical user journey works end-to-end.
- [ ] End-to-end freshness can be measured.
- [ ] Product KPI definition is explicit.
- [ ] Failure behavior is understood for critical stages.

## 34.2 Implementation evidence

- [ ] Architecture claims match implemented components.
- [ ] Planned-only components are clearly marked.
- [ ] Running version/configuration can be identified.

## 34.3 Security

- [ ] Authentication/authorization reviewed where applicable.
- [ ] Secrets are external to source control.
- [ ] Input/security boundaries reviewed.
- [ ] Critical known vulnerabilities are addressed or explicitly accepted.
- [ ] Production credentials use least privilege.

## 34.4 Sources and rights

- [ ] Source acquisition methods are approved.
- [ ] Rate limits/restrictions are respected.
- [ ] Persistence/display scope respects rights metadata.
- [ ] Provenance/canonical URL are preserved.

## 34.5 Data

- [ ] Schema constraints are appropriate.
- [ ] Required timestamps are preserved.
- [ ] Deduplication/idempotency are verified.
- [ ] Migrations are validated.
- [ ] Data-quality failure modes are observable.

## 34.6 Reliability

- [ ] External calls have intentional timeouts.
- [ ] Retries are bounded.
- [ ] State-changing retries are safe.
- [ ] Important dependency failures are handled.
- [ ] Duplicate scheduled runs do not corrupt state or duplicate side effects.

## 34.7 Observability

- [ ] Critical structured logs exist.
- [ ] Source health can be inspected.
- [ ] Freshness can be measured.
- [ ] Important failures are detectable.
- [ ] Operators can trace a failed item/job sufficiently to investigate.

## 34.8 Performance

- [ ] Capacity assumptions are documented.
- [ ] Known quotas are documented.
- [ ] Obvious bottlenecks are understood.
- [ ] Production-like load is evaluated when risk warrants it.

## 34.9 Delivery

- [ ] Build/deployment is reproducible enough for current scale.
- [ ] Relevant automated checks pass.
- [ ] Migration order is known.
- [ ] Smoke validation exists.
- [ ] Rollback path is known and feasible.

## 34.10 Backup / recovery

- [ ] Important persistent data has a backup/recovery strategy.
- [ ] Restore procedure is documented.
- [ ] Restore has been tested where data recovery matters.
- [ ] RPO/RTO are defined if business risk requires them.

## 34.11 Operations

- [ ] Ownership is clear.
- [ ] Critical alerts have a destination/owner.
- [ ] Essential runbooks exist.
- [ ] Credential rotation/recovery procedures are understood.

## 34.12 Cost

- [ ] Monthly cost estimate fits the approved target.
- [ ] Major variable cost drivers are measurable.
- [ ] Quota/cost thresholds are known.
- [ ] No architecture component exists solely for hypothetical scale.

If a critical item is unknown, report:

> `Not yet verified for production`

Do not report:

> `Production-ready`

---

# 35. Release risk levels

Not every release needs the full checklist.

## Low risk

Examples:

- documentation-only;
- internal refactor with strong tests;
- small parser change for one source;
- non-functional telemetry improvement.

Use targeted checks.

## Medium risk

Examples:

- new connector;
- classification contract change;
- new persistence field;
- matching behavior change;
- scheduler changes.

Use targeted production review plus integration validation.

## High risk

Examples:

- authentication/authorization;
- destructive migration;
- notification side effects;
- secrets/credentials;
- major pipeline architecture change;
- recovery design;
- user-data handling.

Use full relevant production gate and explicit rollback/recovery planning.

---

# 36. Review report template

When performing a production readiness review:

## Executive status

Choose one:

- `Not production-ready`
- `Production-ready with accepted limitations`
- `Production-ready for current target scale`
- `Not enough evidence to verify`

## Implemented system

Describe only verified components.

## Critical user/data flow

Document actual flow from implementation.

## Findings

For each finding:

```text
ID:
Severity: P0 / P1 / P2 / P3
Status: Confirmed / Likely risk / Unverified
Area:
Evidence:
Impact:
Recommended smallest fix:
Verification:
```

## Production gates

Summarize:

- correctness;
- security;
- source rights;
- reliability;
- data;
- observability;
- performance;
- deployment/rollback;
- backup/recovery;
- operations;
- cost.

## Remaining risks

Explicitly distinguish accepted risk from unresolved risk.

---

# 37. Anti-overengineering gate

Before adding a major technology, answer:

1. What current requirement does it satisfy?
2. What measured problem does it solve?
3. Why can the existing architecture not solve it?
4. What does it cost monthly?
5. What operational burden does it add?
6. What simpler alternative was considered?
7. What is the migration/rollback path?

Examples that require explicit justification in the current phase:

- Kafka;
- Pub/Sub;
- Airflow/Composer;
- Kubernetes;
- dedicated VM;
- vector DB;
- search cluster;
- Story clustering;
- recommendation ML.

Production readiness is not the number of technologies in the diagram.

---

# 38. Final principles

## 38.1 Demo vs production

A demo proves:

> the happy path can work.

Production readiness asks:

> what happens with real users, repeated execution, bad input, dependency failure, deployment mistakes, data loss, attacks, load, and long-term operation?

## 38.2 Backup principle

> A backup is trustworthy only after restoration has been tested.

## 38.3 Deployment principle

> A deployment strategy is incomplete without a rollback strategy.

## 38.4 Evidence principle

> Production is not merely a place where code is deployed. Production readiness is a standard of evidence.

If a claim cannot be supported by implementation or operational evidence, report it as unverified.

---

# 39. External references

These references inform the review framework. They do not override project-specific requirements.

## Codex project guidance

- OpenAI Codex — Custom instructions with `AGENTS.md`
  - https://developers.openai.com/codex/guides/agents-md
- OpenAI Codex — Customization
  - https://developers.openai.com/codex/concepts/customization
- OpenAI Codex — Best practices
  - https://developers.openai.com/codex/learn/best-practices

## Production launch / reliability

- Google SRE — Launch Coordination Checklist
  - https://sre.google/sre-book/launch-checklist/
- Google SRE — Reliable Product Launches at Scale
  - https://sre.google/sre-book/reliable-product-launches/

## Security

- OWASP Application Security Verification Standard (ASVS)
  - https://owasp.org/www-project-application-security-verification-standard/

## Observability

- OpenTelemetry — Observability primer
  - https://opentelemetry.io/docs/concepts/observability-primer/

## Backup / recovery

- AWS Well-Architected — Back up data
  - https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/back-up-data.html
- AWS Well-Architected — Periodic recovery testing
  - https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_backing_up_data_periodic_recovery_testing_data.html

