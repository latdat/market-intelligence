# Codex Task Template

Copy this template for one implementation task.

---

## Task ID

`DE-XXX`

## Goal

Describe one outcome.

Example:

> Implement generic RSS/Atom ingestion that returns `RawArticle` records.

## Context

Read before editing:

- `AGENTS.md`
- `docs/ARCHITECTURE.md`
- `docs/DATA_CONTRACTS.md`
- relevant existing code
- any task-specific doc

## Current behavior

Ask Codex to inspect and summarize current behavior before editing when the task is non-trivial.

## Scope

Files/directories allowed to change:

```text
...
```

## Requirements

- requirement 1
- requirement 2
- requirement 3

## Explicit non-goals

Do not:

- ...
- ...
- ...

## Contracts

Inputs:

```text
...
```

Outputs:

```text
...
```

Shared contracts that must not change:

```text
...
```

## Failure behavior

Define expected handling for:

- timeout
- invalid input
- retryable external failure
- permanent external failure
- duplicate/repeated execution

## Observability

Required logs/telemetry:

```text
...
```

## Acceptance criteria

- [ ] required behavior implemented
- [ ] relevant unit tests added
- [ ] integration tests added where needed
- [ ] repeated execution/idempotency tested where applicable
- [ ] relevant tests pass
- [ ] lint/type checks pass when configured
- [ ] no secret committed
- [ ] no unrelated refactor
- [ ] docs updated if behavior/contracts changed

## Verify

Run:

```text
<commands>
```

If repository commands are not yet defined, Codex should inspect the repo and report the exact commands it used rather than inventing fake success.

## Completion report

Return:

```text
Summary:
Changed files:
Design decisions:
Tests added:
Tests run:
Test results:
Known risks/gaps:
Suggested next task:
```
