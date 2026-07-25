# Vroom HR

Vroom HR (Vietnamese Recruit-Onboard-Operate-Manage) là nền tảng HR tự host, mã nguồn mở, cho doanh nghiệp Việt Nam. Mỗi công ty chạy một triển khai riêng, một DB riêng, một server riêng. Một triển khai chỉ phục vụ đúng một công ty. Mục lục này chốt nghĩa chuẩn của thuật ngữ domain để team dùng 1 từ cho 1 khái niệm trong spec, code, và docs.

## Agent skills

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
