# Vroom HR

Vroom HR (Vietnamese Recruit-Onboard-Operate-Manage) là nền tảng HR tự host, mã nguồn mở, cho doanh nghiệp Việt Nam. Mỗi công ty chạy một triển khai riêng, một DB riêng, một server riêng. Một triển khai chỉ phục vụ đúng một công ty.

File này là doc **điều hướng** cho agent: tìm gì ở đâu, chạy lệnh nào. Nghĩa chuẩn của thuật ngữ domain do [`CONTEXT.md`](./CONTEXT.md) chốt — đó là glossary canonical của repo, dùng 1 từ cho 1 khái niệm trong spec, code và docs.

## Agent skills

### Issue tracker

Issue và PRD của repo này sống trong GitHub Issues. PR ngoài không phải surface triage. Xem `docs/agents/issue-tracker.md`.

### Triage labels

5 role triage chuẩn map 1-1 sang label của repo: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. Xem `docs/agents/triage-labels.md`.

### Domain docs

Repo single-context: một [`CONTEXT.md`](./CONTEXT.md) ở root, một [`docs/adr/`](./docs/adr/) cho toàn hệ thống. Không có `CONTEXT-MAP.md`, không có ADR theo từng context.

- Dùng đúng vocab của `CONTEXT.md` khi viết issue title, tên test, đề xuất refactor — đừng trôi sang synonym mà glossary đã liệt ở `_Avoid_`. Khái niệm cần dùng chưa có trong glossary là tín hiệu: hoặc đang bịa ngôn ngữ repo không dùng, hoặc có gap thật cần ghi lại.
- **Đọc `docs/adr/` trước khi đổi một quyết định kiến trúc.** ADR đã merge không viết lại, nhưng "không viết lại" gộp hai việc khác nhau:
  - **Đổi một quyết định** → viết ADR mới supersede ADR cũ, không đụng vào bản cũ (xem ADR-0007 → [ADR-0012](./docs/adr/0012-operator-configured-embedding-endpoint.md) làm mẫu).
  - **Một mệnh đề dữ kiện trong ADR hoá ra sai** → đính chính tại chỗ bằng blockquote trỏ sang sự thật, nói rõ phần còn lại vẫn còn giá trị (xem [ADR-0006](./docs/adr/0006-ai-studio-design-system.md) dòng 13 làm mẫu). Supersede cả ADR chỉ vì vài câu sai dữ kiện sẽ để nguyên câu sai làm văn bản sống cho tới khi ai đó đọc tới văn bản thay thế.
- Nếu việc đang làm mâu thuẫn với một ADR hiện có, nói thẳng ra thay vì âm thầm ghi đè.

## Môi trường phát triển cục bộ

### Docker services

Services chạy qua `docker compose up -d`. Chỉ hai service mở ra host:
- **Backend**: `http://localhost:8000` (Swagger: `http://localhost:8000/docs`)
- **Frontend**: `http://localhost:3000`

PostgreSQL, Redis và MinIO **không** với tới được từ host. Ba service này chỉ nối
`vroom-internal-net` (`internal: true`) và Docker không publish port từ mạng đó ra
ngoài — `localhost:5432` / `:6379` / `:9000` / `:9001` đều bị connection refused,
kể cả khi stack đang chạy khoẻ. Vào bằng `docker compose exec`:

- **PostgreSQL** — db `vroom_hr`, pgvector; trong container không cần mật khẩu:
  `docker compose exec postgres psql -U postgres -d vroom_hr`
- **Redis** — cache & ARQ broker; mật khẩu là `REDIS_PASSWORD` trong `.env` gốc:
  `docker compose exec redis redis-cli -a '<REDIS_PASSWORD>'`
- **MinIO** — object storage cho CV/KB:
  `docker compose exec minio sh -c 'mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" && mc ls local'`

  Đừng rút gọn về `mc ls local` trần. Container **có** alias `local` với đúng root
  credential, nhưng `mc` chỉ ghi `/tmp/.mc/config.json` bất đồng bộ khoảng một phút
  sau khi container start. Trước mốc đó lệnh trần chết với `Access Denied`, sau mốc
  đó lại exit 0 — nghĩa là nó hỏng đúng lúc hay chạy nhất (vừa `up -d` xong, kiểm
  ngay) rồi tự "khỏi" khi thử lại lúc rảnh, nên rất dễ tưởng câu `mc alias set` là
  thừa. Set alias tường minh thì không phụ thuộc thời điểm.

Trong mạng Docker, ba service này là `postgres:5432`, `redis:6379`, `minio:9000` —
đó là các địa chỉ `docker-compose.yml` cấp cho backend và các worker.

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
docker compose exec postgres psql -U postgres -d vroom_hr \
  -c "UPDATE users SET password_hash = 'PASTE_HASH' WHERE email = 'USER_EMAIL';"
```

### Reset database

Xoá toàn bộ dữ liệu và chạy lại migrations từ đầu:

```bash
docker compose exec postgres psql -U postgres \
  -c "SELECT pg_terminate_backend(pg_stat_activity.pid) FROM pg_stat_activity WHERE pg_stat_activity.datname = 'vroom_hr' AND pid <> pg_backend_pid();"

docker compose exec postgres psql -U postgres -c "DROP DATABASE vroom_hr;"
docker compose exec postgres psql -U postgres -c "CREATE DATABASE vroom_hr;"

# Alembic phải chạy TRONG container: từ host, `cd backend && uv run alembic`
# dùng URL mặc định `localhost:5432` và sẽ chết vì connection refused.
docker compose exec backend uv run alembic upgrade head
```

### Test cases

Test backend nằm ở `backend/tests/`, soi theo module: `cd backend && uv run pytest tests/ -v`.
Chạy một module: `cd backend && uv run pytest tests/modules/<module>/ -q`.
