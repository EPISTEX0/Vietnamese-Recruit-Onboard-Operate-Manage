<p align="center">

  <a href="./docs/assets/hero-banner.png">
    <img src="./docs/assets/hero-banner.png" alt="Vroom HR — Nền tảng HRM mã nguồn mở tự host cho Việt Nam" width="100%" />
  </a>

</p>

<p align="center">
  <strong>Vroom HR</strong><br/>
  Nền tảng HRM mã nguồn mở, tự host cho doanh nghiệp Việt Nam (Recruit - Onboard - Operate - Manage)
</p>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="Giấy phép: MIT" /></a>
  <a href="./backend"><img src="https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?logo=python&logoColor=white" alt="Python 3.11+" /></a>
  <a href="./frontend"><img src="https://img.shields.io/badge/Next.js-15-black.svg?logo=next.js&logoColor=white" alt="Next.js 15" /></a>
  <img src="https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Docker-compose-2496ED.svg?logo=docker&logoColor=white" alt="Docker" />
  <img src="https://img.shields.io/badge/build-passing-brightgreen.svg" alt="Build Passing" />
  <img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg" alt="PRs Welcome" />
</p>

<p align="center">
  <b><a href="./README.md">English</a></b> |
  <a href="./README.vi.md">Tiếng Việt</a>
</p>

---

## Tổng quan

**Vroom HR** là nền tảng quản lý nhân sự (HRM) mã nguồn mở, tự host, được xây dựng dành riêng cho doanh nghiệp Việt Nam hiện nay. Mỗi công ty chạy một deployment được cách ly hoàn toàn — riêng server, riêng database — để dữ liệu nhân sự nhạy cảm không bao giờ rời khỏi hạ tầng của bạn.

Vroom HR vượt xa phần mềm HRM truyền thống bằng cách tích hợp **tự động hóa bằng AI và trợ lý RAG với hai kho tri thức (Dual-KB RAG)** trực tiếp vào vòng đời nhân viên: từ tìm kiếm và sàng lọc ứng viên, qua onboarding, đến vận hành và quản lý lực lượng lao động hằng ngày.

**Bốn trụ cột chính:**

1. **Tự host & Single-Tenant** — Riêng tư dữ liệu 100%. Mỗi deployment phục vụ đúng một công ty với database, object storage và dịch vụ embedding riêng. Không dùng chung tenancy, không phơi bày dữ liệu cho bên thứ ba.
2. **Tự động hóa bằng AI** — Tự động **parse CV**, **phân loại intent của email ứng tuyển (job application)**, và **tích hợp Gmail** liền mạch để đưa vào pipeline tuyển dụng với bước review có sự xác nhận của con người (human-in-the-loop).
3. **Dual-KB RAG Knowledge Base** — Hai **kho tri thức cách ly về mặt vật lý** (HR KB và Employee KB) được index bằng **pgvector** và **MinIO**, phục vụ các trợ lý AI hiểu rõ domain cho cả HR lẫn nhân viên.
4. **All-in-One HR Suite** — Một nền tảng bao trùm toàn bộ hành trình nhân sự: **Recruit → Onboard → Operate → Manage**, bao gồm chấm công, yêu cầu nhân viên và payslip.

> **Ghi chú thuật ngữ:** Để xem glossary domain chuẩn (Organization, Candidate, Backbone Flow, Knowledge Base, AI Assistant, v.v.), tham khảo [`CONTEXT.md`](./CONTEXT.md). Các quyết định kiến trúc nằm trong [`docs/adr/`](./docs/adr/).

<p align="center">
  <a href="./docs/assets/ai-features.png">
    <img src="./docs/assets/ai-features.png" alt="Vroom HR AI Features & RAG Architecture" width="100%" />
  </a>
</p>

---

## Tính năng

| Lĩnh vực | Khả năng |
| --- | --- |
| **Tuyển dụng (Recruitment)** | Tiếp nhận email qua Gmail, phân loại intent bằng AI (`job_application` / `partner` / `event` / `internal` / `other`), parse CV bằng AI với review human-in-the-loop, pipeline Candidate (`new → reviewing → interview scheduled → accepted/rejected/archived`), Job Opening, Recruitment Inbox. |
| **AI Automation** | Các tác vụ AI chạy nền theo event — không có hội thoại. Phân loại email đến, parse CV thành dữ liệu có cấu trúc, và đưa ứng viên được accepted thẳng vào onboarding. |
| **AI Assistant (HR)** | Trợ lý hội thoại *đọc* dữ liệu recruitment & onboarding và *soạn thảo* proposal (ví dụ email mời phỏng vấn); nó không bao giờ tự ghi database — HR xác nhận mọi write (human-in-the-loop). |
| **AI Assistant (Employee)** | Trợ lý self-service chỉ đọc dữ liệu của chính nhân viên đang hỏi và có thể soạn draft các request thuộc về nhân viên (nghỉ phép, làm thêm giờ), không bao giờ tự ghi dữ liệu. |
| **Dual-KB RAG** | Hai kho tri thức cách ly về vật lý: **HR KB** (tài liệu chỉ dành cho HR) và **Employee KB** (tài liệu do HR publish). Ingestion diễn ra bất đồng bộ qua ARQ worker: PDF/DOCX → chunk → embed (embedding tiếng Việt 1024 chiều) → pgvector, file gốc lưu trên MinIO. |
| **Onboarding** | Onboarding dựa trên checklist, được kích hoạt bởi event `candidate_accepted`. `OnboardingProcess` với các task cố định; nhân viên trở thành **active** khi mọi task bắt buộc cùng thông tin department/position/manager/start-date được hoàn tất. |
| **Employee Self-Service** | Tài khoản nhân viên được kích hoạt sau onboarding: hồ sơ, request nghỉ phép & làm thêm giờ, chấm công, payslip. |
| **Operate & Manage** | Bản ghi chấm công (Attendance), Employee Request (nghỉ phép/làm thêm giờ) do HR review, publish Payslip (chỉ đọc cho nhân viên). |
| **Auth & Security** | Google OAuth2 + JWT cookies, phân quyền theo vai trò (`SYSTEM_ADMIN` / `HR` / `Employee`), cách ly nghiêm ngặt giữa quản trị hạ tầng và dữ liệu nghiệp vụ HR. |
| **i18n** | Giao diện tiếng Việt mặc định, kèm thiết lập tiếng Anh/`next-intl` đầy đủ. |

---

## Bắt đầu nhanh

> **Điều kiện tiên quyết:** Docker + Docker Compose, `uv` (Python ≥ 3.11), và `pnpm` (Node ≥ 20).

### 1. Hạ tầng

```bash
git clone git@github.com:EPISTEX0/Vietnamese-Recruit-Onboard-Operate-Manage.git
cd Vietnamese-Recruit-Onboard-Operate-Manage

# Khởi động hạ tầng lõi (PostgreSQL+pgvector, Redis)
docker compose up -d postgres redis

# Tùy chọn — full RAG stack (dịch vụ embedding + object storage MinIO):
# docker compose up -d
```

### 2. Backend

```bash
cd backend
cp .env.example .env
# Edit .env — set at minimum AUTH_JWT_SECRET_KEY, AUTH_OAUTH_TOKEN_ENCRYPTION_KEY,
# và Google OAuth credentials nếu dùng tích hợp Gmail.

uv sync
uv run alembic upgrade head          # apply database migrations

# Start API server (http://localhost:8000 — Swagger UI tại /docs)
uv run uvicorn src.main:app --reload --port 8000
```

Chạy các background worker trong terminal riêng:

```bash
# Gmail sync + phân loại bằng AI (poll mỗi 5 phút)
uv run arq src.modules.gmail.worker.WorkerSettings

# Knowledge Base ingestion (PDF/DOCX → embeddings → pgvector)
uv run arq src.modules.knowledge_base.worker.KnowledgeBaseWorkerSettings

# Onboarding (consumes candidate_accepted, điều khiển OnboardingProcess)
uv run arq src.modules.onboarding.worker.OnboardingWorkerSettings
```

> **Lưu ý:** khi chạy qua Docker Compose, cả ba worker cùng dịch vụ `vroom-embedding` được khởi động tự động.

### 3. Frontend

```bash
cd ../frontend                       # package: vroom-hr
cp .env.example .env.local
# Đặt NEXT_PUBLIC_API_URL=http://localhost:8000 và NEXT_PUBLIC_APP_URL

pnpm install
pnpm dev                             # http://localhost:3000
```

> Tài khoản dev mặc định: `admin@vroomhr.com` / `VroomAdmin!2026` (xem `AGENTS.md`).

---

## Biến môi trường

### Backend (`backend/.env`)

| Biến | Mô tả | Mặc định |
| --- | --- | --- |
| `DATABASE_URL` | DSN PostgreSQL bất đồng bộ (chính DB, pgvector) | `postgresql+asyncpg://postgres:postgres@localhost:5432/vroom_hr` |
| `AUTH_DATABASE_URL` | DSN database cho module Auth | `postgresql+asyncpg://…/vroom_hr` |
| `AUTH_REDIS_URL` | DSN Redis cho cache & ARQ | `redis://localhost:6379/0` |
| `AUTH_GOOGLE_CLIENT_ID` / `AUTH_GOOGLE_CLIENT_SECRET` | Google OAuth2 credentials | — |
| `AUTH_GOOGLE_REDIRECT_URI` | OAuth redirect callback | `http://localhost:8000/api/auth/callback` |
| `AUTH_JWT_SECRET_KEY` | Secret kí JWT (**đổi khi lên prod**) | — |
| `AUTH_OAUTH_TOKEN_ENCRYPTION_KEY` | Khóa mã hóa AES-256-GCM base64 cho OAuth token | — |
| `AUTH_FRONTEND_URL` | URL Frontend cho CORS / redirects | `http://localhost:3000` |
| `RECRUITMENT_LLM_BASE_URL` / `RECRUITMENT_LLM_MODEL` | LLM cho parse CV & phân loại email | Endpoint tương thích OpenAI |
| `ASSISTANT_LLM_BASE_URL` / `ASSISTANT_LLM_MODEL` | LLM cho AI Assistant | Endpoint tương thích OpenAI |
| `KB_MINIO_BUCKET` / `KB_EMBEDDING_SERVICE_URL` / `KB_DATABASE_URL` | Storage, embedding & DB cho Knowledge Base | `knowledge-base` / `http://localhost:8080` |

### Hạ tầng & Docker Compose (`.env` ở root)

| Biến | Mô tả | Mặc định |
| --- | --- | --- |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Thông tin quản trị container PostgreSQL | `postgres` / `postgres` / `vroom_hr` |
| `REDIS_PASSWORD` | Mật khẩu container Redis | — |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | Mật khẩu quản trị container MinIO | `vroomminio` / `vroomminio` |
| `EMBEDDING_MODEL_NAME` | Model embedding tiếng Việt cho `vroom-embedding` | `AITeamVN/Vietnamese_Embedding_v2` |
| `EMBEDDING_PORT` | Port dịch vụ `vroom-embedding` | `8080` |
### Frontend (`frontend/.env.local`)

| Biến | Mô tả | Mặc định |
| --- | --- | --- |
| `NEXT_PUBLIC_API_URL` | Base URL backend (FastAPI) cho mọi API call | `http://localhost:8000` |
| `NEXT_PUBLIC_APP_URL` | URL công khai nơi frontend được host | `http://localhost:3000` |

> Xem `backend/.env.example` để có đầy đủ các setting theo tiền tố module (`AUTH_`, `GMAIL_`, `EMPLOYEE_`, `RECRUITMENT_`, `ASSISTANT_LLM_`, `KB_`) và giá trị mặc định.

---

## Kiến trúc tổng quan

```
Client (Next.js 15)  ──►  FastAPI Backend  ──►  PostgreSQL (pgvector)
        ▲                      │   │   │
        │                      │   │   └──► Redis (cache + ARQ queue)
        │                      │   └──────► MinIO (object storage)
        │                      └──────────► ARQ Workers (gmail · kb · onboarding)
        │                                   │
        │                                   └──► LLM Adapters ──► vroom-embedding (1024-dim)
        └────────────────────────────────────────────┘
```

Để tìm hiểu sâu với ba sơ đồ Mermaid — **System High-Level Architecture**, **Dual-KB RAG**, và **luồng Backbone Flow** — xem [`ARCHITECTURE.md`](./ARCHITECTURE.md).

---

## Phát triển & Kiểm thử

```bash
# Backend — lint & format
cd backend
ruff check src/ && ruff format src/
mypy src/

# Backend — tests
pytest tests/ -v

# Frontend — lint
cd ../frontend
pnpm lint
```

Tái lập môi trường test:

```bash
# Reset database và chạy lại migration (cần hạ tầng Docker đang chạy)
docker exec vroom-postgres psql -U postgres -c "SELECT pg_terminate_backend(pg_stat_activity.pid) FROM pg_stat_activity WHERE pg_stat_activity.datname = 'vroom_hr' AND pid <> pg_backend_pid();"
docker exec vroom-postgres psql -U postgres -c "DROP DATABASE vroom_hr;"
docker exec vroom-postgres psql -U postgres -c "CREATE DATABASE vroom_hr;"
cd backend && uv run alembic upgrade head
```

> Hướng dẫn đóng góp, issue template, và chuẩn PR được quản lý trong `.github/` theo chuẩn [Repository Governance](https://github.com/EPISTEX0/Vietnamese-Recruit-Onboard-Operate-Manage/blob/main/docs/adr/0011-github-repository-governance-and-documentation-standard.md). Ngôn ngữ domain và các khái niệm AI: [`CONTEXT.md`](./CONTEXT.md).

---

## Lộ trình

- **Scope hiện tại** — Backbone Flow đầu cuối (Recruit → Onboard → Operate → Manage), Dual-KB RAG, HR & Employee AI Assistants, chấm công, yêu cầu nhân viên, payslip.
- **Tương lai** — **AI Agent** tự chủ (tự quyết định, tự thực thi write) nằm ngoài scope hiện tại và chỉ được ghi nhận như một định hướng tương lai.

## Giấy phép

Vroom HR được phát hành theo [MIT License](./LICENSE). Bạn được tự do sử dụng, chỉnh sửa, và tự host Vroom HR cho tổ chức của mình.