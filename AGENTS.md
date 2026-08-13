# Vroom HR

Vroom HR (Vietnamese Recruit-Onboard-Operate-Manage) là nền tảng HR tự host, mã nguồn mở, cho doanh nghiệp Việt Nam. Mỗi công ty chạy một triển khai riêng, một DB riêng, một server riêng. Một triển khai chỉ phục vụ đúng một công ty. Mục lục này chốt nghĩa chuẩn của thuật ngữ domain để team dùng 1 từ cho 1 khái niệm trong spec, code, và docs.

## Agent skills

IMPORTANT: When applicable, prefer using intellij-index MCP tools for code navigation and refactoring.

## Code Search

Use `semble search` to find code by describing what it does or naming a symbol/identifier, instead of grep:

```bash
semble search "authentication flow" ./my-project --max-snippet-lines 10  # first 10 lines only, concise
semble search "save_pretrained" ./my-project                          # full chunk content
semble search "save model to disk" ./my-project --top-k 10           # more results
```

The index is built on first run (and cached for subsequent runs) and invalidated automatically when files change.

Use `--content docs` to search documentation and prose, `--content config` for config files (yaml, toml, etc.), or `--content all` to search code, docs, and config:

```bash
semble search "deployment guide" ./my-project --content docs
semble search "database host port" ./my-project --content config
semble search "authentication" ./my-project --content all
```

Use `semble find-related` to discover code similar to a known location (pass `file_path` and `line` from a prior search result):

```bash
semble find-related src/auth.py 42 ./my-project
```

`path` defaults to the current directory when omitted; git URLs are accepted.

If `semble` is not on `$PATH`, use `uvx --from "semble[mcp]" semble` in its place.

### Workflow

1. Start with `semble search` to find relevant chunks. The index is built and cached automatically.
2. Use `--content docs` for documentation, `--content config` for config files, or `--content all` for everything.
3. Navigate directly to the returned file and line — do not re-search or grep for the same content.
4. Optionally use `semble find-related` with a promising result's `file_path` and `line` to discover related implementations.
5. Use grep only when you need every occurrence of a literal string across the whole repo (e.g., all callers of a renamed function).

### Issue tracker

Issue và PRD của repo này sống trong GitHub Issues. PR ngoài không phải surface triage. Xem `docs/agents/issue-tracker.md`.

### Triage labels

5 role triage chuẩn map 1-1 sang label của repo: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. Xem `docs/agents/triage-labels.md`.

### Domain docs

Repo single-context. Đọc `CONTEXT.md` ở root và `docs/adr/` cho quyết định kiến trúc. Xem `docs/agents/domain.md`.

## Môi trường phát triển cục bộ

### Docker services

Services chạy qua `docker compose up -d`:
- **Backend**: `http://localhost:8000` (Swagger: `http://localhost:8000/docs`)
- **Frontend**: `http://localhost:3000`
- **PostgreSQL**: `localhost:5432`, user `postgres`, pass `postgres`, db `vroom_hr`
- **Redis**: `localhost:6379`
- **MinIO**: `http://localhost:9000` (console: `http://localhost:9001`)

### Tài khoản test

| Email | Vai trò | Mật khẩu | Ghi chú |
|---|---|---|---|
| `admin@vroomhr.com` | admin | `VroomAdmin!2026` | Admin dev, dùng để test UI local |
| `employee@vroomhr.com` | user | _(chưa đặt)_ | Nhân viên test |

Để reset mật khẩu trong DB:
```bash
cd backend && python3 -c "
from src.modules.identity.infrastructure.password_utils import hash_password
print(hash_password('NEW_PASSWORD'))
"
# Copy hash và chạy:
docker exec vroom-postgres psql -U postgres -d vroom_hr \
  -c "UPDATE users SET password_hash = 'PASTE_HASH' WHERE email = 'USER_EMAIL';"
```

### Reset database

Xoá toàn bộ dữ liệu và chạy lại migrations từ đầu:

```bash
docker exec vroom-postgres psql -U postgres \
  -c "SELECT pg_terminate_backend(pg_stat_activity.pid) FROM pg_stat_activity WHERE pg_stat_activity.datname = 'vroom_hr' AND pid <> pg_backend_pid();"

docker exec vroom-postgres psql -U postgres -c "DROP DATABASE vroom_hr;"
docker exec vroom-postgres psql -U postgres -c "CREATE DATABASE vroom_hr;"

cd backend && uv run alembic upgrade head
```

### Test cases

Test case AI testing nằm ở `docs/ai-testing/`. Mỗi thư mục là một module, mỗi file là một test case cụ thể.
Pytest backend: `cd backend && python -m pytest tests/ -v`.
