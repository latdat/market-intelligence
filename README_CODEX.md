# Codex Starter Pack

> **Historical starter-pack notice:** the task numbering below predates the current
> backlog. Use `docs/DE_BACKLOG.md` as the task source of truth and
> `docs/SOURCE_REGISTRY.md` for Source Onboarding. In particular, DE-013 is `PAUSED`; it
> is not the REST connector task described by this historical list.

This folder is the operating context for Codex when working on the Data Engineer side of the Market & Regulatory Intelligence Platform.

## Recommended repository placement

Copy the files into the repository root like this:

```text
market-intelligence/
├── AGENTS.md
├── README_CODEX.md
├── docs/
│   ├── ARCHITECTURE.md
│   ├── DATA_CONTRACTS.md
│   ├── ENGINEERING_RULES.md
│   ├── TESTING_STRATEGY.md
│   ├── SOURCE_CONNECTOR_GUIDE.md
│   ├── CODEX_TASK_TEMPLATE.md
│   └── DE_BACKLOG.md
├── services/
├── packages/
├── tests/
└── infra/
```

## How to use

For a new Codex task:

1. Make sure Codex can read `AGENTS.md`.
2. Ask Codex to inspect the relevant docs and code before editing.
3. Copy `docs/CODEX_TASK_TEMPLATE.md` into your prompt or fill it as a ticket.
4. For architectural changes, require a plan before implementation.
5. After implementation, run a second review focused on:
   - data loss
   - duplicate processing
   - retry behavior
   - idempotency
   - timestamps
   - rate limits
   - rights
   - observability
   - cloud cost

## Recommended first Codex tasks

Start in this order:

```text
DE-001 Repository scaffold
DE-002 Source Registry model
DE-003 Canonical Article contracts
DE-004 Generic RSS/Atom connector
DE-005 Normalization pipeline
DE-006 Deterministic deduplication
DE-007 Supabase article persistence
DE-008 DeepSeek classification adapter
DE-009 Rule-based matching
DE-010 BigQuery telemetry
DE-011 Cloud Run data job
DE-012 Cloud Scheduler configuration
DE-013 REST connector framework
DE-014 HTML connector framework
DE-015 Integration tests
```

Do not ask one Codex task to build all of these at once.
