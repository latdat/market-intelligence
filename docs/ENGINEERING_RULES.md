# Engineering Rules

## 1. Optimize for the current product, not hypothetical scale

Current target is approximately 1,000 active users.

Prefer:

- simple
- observable
- idempotent
- cheap
- testable
- easy to debug

Do not optimize for millions of users unless a measured bottleneck appears.

## 2. Keep jobs small and restartable

Pipeline jobs should be safe to retry.

Prefer explicit stages and well-defined inputs/outputs.

Avoid one giant function that fetches, transforms, writes, classifies, matches, and sends notifications without recoverable boundaries.

## 3. Network calls

For external sources:

- set explicit timeouts
- distinguish retryable from permanent failures
- use bounded retries
- respect rate limits
- record useful failure context
- do not retry aggressively on 4xx errors unless the status is known to be transient such as 429
- avoid infinite retry loops

## 4. Time handling

Use timezone-aware timestamps.

Prefer UTC internally.

Preserve:

- source publication time
- system discovery time
- classification time
- matching time

Do not infer an exact publication timestamp when the source does not provide one.

## 5. URL normalization

Canonicalization should be deterministic and conservative.

Typical operations may include:

- normalize scheme/host casing
- remove known tracking query parameters
- normalize trailing slash only when safe
- preserve parameters that identify distinct content

Never assume all query parameters are tracking parameters.

## 6. Deduplication

Order:

1. canonical URL
2. source item ID
3. deterministic hash
4. normalized-title similarity

The system should explain why two records were considered duplicates where practical.

For the current deterministic implementation:

- canonical URL equality applies across sources;
- source item ID equality applies only within the same `source_id`;
- content hash equality applies across sources;
- normalized-title similarity is evaluated only after all exact checks fail;
- the title comparison key uses NFC, case folding, and collapsed whitespace while
  preserving punctuation and numbers;
- title similarity uses `SequenceMatcher(autojunk=False)` and is a hard duplicate only
  when the score is at least `0.98`, both comparison keys are at least 30 characters,
  both articles have the same market, and their effective timestamps differ by no more
  than 24 hours;
- effective time is `published_at` when available, otherwise `discovered_at`;
- numeric tokens are ordered contiguous digit runs; when both titles contain numeric
  tokens and their sequences differ, title similarity cannot decide a hard duplicate.

The deduplication decision identifies the matched existing article and the first matching
reason. It does not choose a canonical winner or perform persistence.

## 7. Database writes

For ingestion paths:

- prefer deterministic keys
- use transactions where atomicity matters
- preserve first discovery time
- do not duplicate classification or alert side effects unnecessarily
- use unique constraints to make invariants enforceable
- keep migrations reviewable

For the DE-007 persistence MVP, `article_id` is the enforced identity. Article writes use
first-write-wins semantics: a primary-key conflict is ignored and never updates
`discovered_at` or another article field. Other deduplication signals are not storage
uniqueness invariants.

## 8. Rights-aware storage

Default to metadata-first.

Do not persist:

- full publisher HTML
- full article body
- redistributed content

unless the source registry explicitly allows it and the task requires it.

## 9. LLM calls

LLM calls are external network dependencies and cost-bearing operations.

Rules:

- minimize input
- request structured JSON
- validate output
- set timeout
- handle malformed output
- measure success/failure
- estimate cost where practical
- avoid duplicate calls for the same stable article/classification version

## 10. Matching

Current matching is rule-based.

It should be:

- deterministic where practical
- explainable
- easy to debug
- cheap

Do not add recommendation ML or embeddings.

## 11. Logging

Prefer structured logs.

Useful fields:

```text
event
source_id
article_id
stage
status
attempt
duration_ms
error_type
```

Never log:

- API keys
- database passwords
- authorization headers
- user secrets

## 12. Configuration

Keep environment-specific settings out of source code.

Use environment/config variables for:

- database URL
- service credentials
- LLM API key
- scheduler parameters
- deployment-specific settings

Provide `.env.example` when the repository starts using environment variables.

Never commit real secrets.

## 13. Dependencies

Before adding a dependency, answer:

- Is it necessary?
- Can the standard library/current stack solve it?
- Does it materially increase image size, cold start, cost, or maintenance?
- Is it actively maintained?

Do not add a framework to solve a small utility problem.

## 14. Scope control

For each Codex task:

- modify only files required by scope
- do not perform unrelated cleanup
- do not rename broad parts of the project without explicit instruction
- do not redesign stable contracts while implementing a small connector

## 15. Documentation

Update docs when behavior, contracts, setup, or operational expectations change.

Comments should explain why, not repeat obvious code.

## 16. Security baseline

At minimum:

- validate untrusted external input
- use parameterized SQL/ORM safely
- do not interpolate untrusted values into shell commands
- avoid SSRF-prone arbitrary URL fetching interfaces
- restrict connector targets to configured sources
- do not expose secrets in logs/errors
