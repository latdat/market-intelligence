# Market Intelligence — Data Engineer Workspace

Repository này chứa phần Data Engineer của Market & Regulatory Intelligence
Platform. Các thành phần hiện tại bao gồm source models, ingestion/normalization,
deterministic deduplication và persistence MVP cho `CanonicalArticle`; chưa có một
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
