# Market Intelligence — Data Engineer Workspace

Repository này chứa phần Data Engineer của Market & Regulatory Intelligence
Platform. Các thành phần hiện tại bao gồm source models, ingestion/normalization,
deterministic deduplication, classification adapter và durable classification
persistence contract; chưa có một
pipeline production hoàn chỉnh.

## Yêu cầu

- Python 3.12 trở lên
- pip đi kèm Python

## Cấu trúc chính

```text
src/market_intelligence/  Python package chính
tests/unit/               Unit tests
tests/integration/        Integration tests
tests/e2e/                End-to-end smoke tests
tests/fixtures/           Test fixtures dùng lại được
services/                 Deployable services/jobs trong các task sau
packages/                 Dành cho shared packages nếu phát sinh nhu cầu
infra/                    Infrastructure configuration trong các task sau
docs/                     Architecture, contracts và engineering guidance
```

## Cài đặt trên Windows PowerShell

Tạo virtual environment:

```powershell
py -3.12 -m venv .venv
```

Cài project ở editable mode cùng development dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

`editable mode` liên kết environment với source local, vì vậy không cần cài lại
project sau mỗi lần sửa Python code.

## Cài đặt trên macOS/Linux

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

## Chạy tests

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

macOS/Linux:

```bash
.venv/bin/python -m pytest
```

## Chạy lint

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
```

macOS/Linux:

```bash
.venv/bin/python -m ruff check .
```

## Kiểm tra format

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m ruff format --check .
```

macOS/Linux:

```bash
.venv/bin/python -m ruff format --check .
```

## Chạy type checking

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m mypy
```

macOS/Linux:

```bash
.venv/bin/python -m mypy
```

Mypy strict mode hiện chỉ áp dụng cho `src/market_intelligence`. Tests không bị ép
strict trong DE-001.

## SO-001 source onboarding

Production source configuration nằm trong config/sources/, mỗi source có một file
TOML và filename phải khớp source_id.

Chạy live preflight giới hạn 20 entries/source mà không ghi Supabase:

    .\.venv\Scripts\python.exe scripts\run_source_onboarding.py --preflight --max-items 20

Sau khi preflight thành công, chạy bounded ingestion:

    .\.venv\Scripts\python.exe scripts\run_source_onboarding.py --max-items 20

--max-items là runtime safety bound bắt buộc, không phải field của SourceConfig.
Runner thực hiện RSS → normalization → batch-local deterministic deduplication →
Supabase. Duplicate decisions được báo cáo nhưng mọi source-local record có quyền lưu
metadata vẫn được persist; DE-007 first-write-wins giữ repeated ingestion idempotent.

## Classification configuration

DE-008 reads `DEEPSEEK_API_KEY` directly from the environment. Pricing defaults to
`config/classification/deepseek_pricing.toml`; use `DEEPSEEK_PRICING_CONFIG_PATH` only
when a deployment needs another path. Pricing is effective-dated and is never fetched
from the internet at runtime.

The DeepSeek adapter remains a standalone provider boundary and is not wired into RSS
onboarding. Current classification behavior is `classification-v2`: deterministic rules
run first, and only `AMBIGUOUS` articles may reach the rights-gated DeepSeek fallback.
No provider request is permitted unless the source has both
`rights_review_status=APPROVED` and `can_ai_process=true`. Unit and integration tests use
mocked HTTP and do not need or use a real API key.

DE-009 adds `ClassificationRepository`, additive migrations for
`public.article_classifications`, transactional claim/lease/fencing RPCs, and offline
PostgreSQL integration/concurrency tests. The original DE-009 migrations and
`20260817000000_add_classification_method.sql` are applied to and verified on the linked
remote Supabase project.

The separate bounded classification runner defaults to hybrid `classification-v2`. It
discovers articles from sources with metadata-storage rights, runs deterministic
classification, and checks AI rights immediately before any DeepSeek fallback. Historical
`classification-v1` remains DeepSeek-first and discovers only AI-approved sources. The
classification runner is not wired into RSS onboarding.

The manual entrypoint requires explicit acknowledgement because it may call DeepSeek:

```powershell
.\.venv\Scripts\python.exe scripts\run_classification.py --confirm-live-provider
```

Do not run it against production until source rights and the separate live-smoke gate are
approved. Current production source configs are not AI eligible: deterministic v2 work may
still be discovered, while an `AMBIGUOUS` result is quarantined as
`AI_FALLBACK_NOT_ALLOWED` without a provider call. Normal tests remain fully offline and
require no DeepSeek key.

## SWE Data Ready v1

Linked synthetic shared-contract data is available in `swe_handoff/`. It supports SWE
read-side development without claiming that production matching or preference persistence
exists. `UserPreference` persistence belongs to Product/SWE; DE consumes the shared
contract and does not create a substitute schema. The missing production matching runner
blocks real `alert_candidates` population, not SWE Data Ready v1.

The only documented remote migration drift is the two DE-012 alert-candidate migrations:

- `20260818000000_create_alert_candidates.sql`
- `20260818000001_grant_alert_candidates_service_role.sql`

DE-013 pipeline telemetry remains `PAUSED`.

## Supabase configuration

Persistence MVP đọc hai biến trực tiếp từ environment:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`

Sao chép `.env.example` thành `.env` nếu muốn quản lý giá trị local, nhưng project
không tự động load file `.env`. Trước khi chạy code dùng persistence, export các biến
vào process hiện tại. Ví dụ trên Windows PowerShell:

```powershell
$env:SUPABASE_URL = 'https://your-project.supabase.co'
$env:SUPABASE_SERVICE_KEY = 'your-service-key'
```

Không commit `.env` hoặc secret thật và không ghi service key, token hay authorization
header vào logs. `SUPABASE_SERVICE_KEY` có đặc quyền cao, vì vậy chỉ dùng trong backend
trusted environment, không đưa vào frontend.

Migration đề xuất nằm trong `supabase/migrations/`. Migration phải được review và áp
dụng riêng trước khi persistence code được chạy với Supabase thật.

Đọc `AGENTS.md` và các tài liệu trong `docs/` trước khi thay đổi code hoặc contract.
