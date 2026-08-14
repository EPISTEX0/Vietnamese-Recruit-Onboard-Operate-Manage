# Vroom HR

Vroom HR (Vietnamese Recruit-Onboard-Operate-Manage) là nền tảng HR tự host, mã nguồn mở, cho doanh nghiệp Việt Nam. Mỗi công ty chạy một triển khai riêng, một DB riêng, một server riêng. Một triển khai chỉ phục vụ đúng một công ty.

File này là doc **điều hướng** cho agent: tìm gì ở đâu, chạy lệnh nào. Nghĩa chuẩn của thuật ngữ domain do [`CONTEXT.md`](./CONTEXT.md) chốt — đó là glossary canonical của repo, dùng 1 từ cho 1 khái niệm trong spec, code và docs.

## Agent skills

IMPORTANT: When applicable, prefer using intellij-index MCP tools for code navigation and refactoring.

## Code Search

Tìm code theo mô tả hoặc theo symbol bằng `semble search <query> <path>`; cần *mọi* occurrence của một chuỗi literal (đổi tên hàm, gỡ field) thì dùng `rg`.

### Issue tracker

Issue và PRD của repo này sống trong GitHub Issues. PR ngoài không phải surface triage. Xem `docs/agents/issue-tracker.md`.

### Triage labels

5 role triage chuẩn map 1-1 sang label của repo: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. Xem `docs/agents/triage-labels.md`.

### Domain docs

Repo single-context: một [`CONTEXT.md`](./CONTEXT.md) ở root, một [`docs/adr/`](./docs/adr/) cho toàn hệ thống. Không có `CONTEXT-MAP.md`, không có ADR theo từng context.

- Dùng đúng vocab của `CONTEXT.md` khi viết issue title, tên test, đề xuất refactor — đừng trôi sang synonym mà glossary đã liệt ở `_Avoid_`. Khái niệm cần dùng chưa có trong glossary là tín hiệu: hoặc đang bịa ngôn ngữ repo không dùng, hoặc có gap thật cần ghi lại.
- **Đọc `docs/adr/` trước khi đổi một quyết định kiến trúc.** ADR là bất biến: không sửa ADR đã merge, muốn đổi thì viết ADR mới supersede ADR cũ (xem ADR-0007 → ADR-0012 làm mẫu).
- Nếu việc đang làm mâu thuẫn với một ADR hiện có, nói thẳng ra thay vì âm thầm ghi đè.

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
cd backend && uv run python -c "
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

Test backend nằm ở `backend/tests/`, soi theo module: `cd backend && uv run pytest tests/ -v`.
Chạy một module: `cd backend && uv run pytest tests/modules/<module>/ -q`.
