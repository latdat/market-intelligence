# Market Intelligence — Data Engineer Workspace

Repository này chứa phần Data Engineer của Market & Regulatory Intelligence
Platform. DE-001 mới chỉ thiết lập Python package và development tooling; chưa có
pipeline hoặc business logic chạy production.

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

## Configuration và secrets

DE-001 chưa yêu cầu environment variable. Khi configuration được thêm ở task sau:

- nhận giá trị theo environment thay vì hard-code;
- không commit `.env` hoặc secret thật;
- thêm `.env.example` với tên biến và placeholder an toàn;
- không ghi credentials, token hoặc authorization header vào logs.

Đọc `AGENTS.md` và các tài liệu trong `docs/` trước khi thay đổi code hoặc contract.
