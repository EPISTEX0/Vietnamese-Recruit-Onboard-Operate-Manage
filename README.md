# Vroom HR

Vietnamese Recruit-Onboard-Operate-Manage — HRM platform for Vietnamese businesses.

## Project status & features

Status reports are dated snapshots and are not the current source of truth. To see where things stand now, inspect the wired routers in `backend/src/main.py`, the backend and frontend tests, the ADRs in `docs/adr/`, and open GitHub Issues.

Start with [`CONTEXT.md`](./CONTEXT.md) for the domain language (Organization,
Candidate, Onboarding, Backbone Flow...). For contribution conventions (skills
workflow, git/branch/commit/PR), see [`AGENTS.md`](./AGENTS.md).

## Tech Stack

- **Backend**: FastAPI + SQLModel + PostgreSQL 15 + Redis 7
- **Frontend** (`vroom-hr/`, chính): Next.js 15 + React 19 + TypeScript + Tailwind v4 + TanStack Query + lucide-react
- **Frontend legacy** (`frontend/`, backup): Next.js 14 + Tailwind 3 + shadcn/ui (Heritage theme) — không phát triển tính năng mới
- **Auth**: Google OAuth2 + JWT (cookies)
- **Storage**: MinIO
- **AI**: OpenAI-compatible APIs (CV parsing)

## Quick Start

```bash
# Clone and setup
git clone https://github.com/your-org/Vietnamese-Recruit-Onboard-Operate-Manage.git
cd Vietnamese-Recruit-Onboard-Operate-Manage

# Start infrastructure
docker compose up -d postgres redis

# Backend
cd backend
cp .env.example .env
# Edit .env with your settings
uv sync
uv run alembic upgrade head
uvicorn src.main:app --reload --port 8000

# Frontend (vroom-hr — chính)
cd ../vroom-hr
cp .env.example .env.local
# Edit .env.local (NEXT_PUBLIC_API_URL, GEMINI_API_KEY, APP_URL)
pnpm install
pnpm dev   # http://localhost:3000
#
# (Legacy) frontend/ cũ — Next.js 14 / Tailwind 3, chỉ backup:
#   cd ../frontend && pnpm install && pnpm dev
```

## Environment Variables

### Backend (.env)

```env
# Database & Redis
AUTH_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/vroom_hr
AUTH_REDIS_URL=redis://localhost:6379/0

# Google OAuth
AUTH_GOOGLE_CLIENT_ID=your-client-id
AUTH_GOOGLE_CLIENT_SECRET=your-client-secret
AUTH_GOOGLE_REDIRECT_URI=http://localhost:8000/api/auth/callback

# JWT
AUTH_JWT_SECRET_KEY=your-secret-key
AUTH_JWT_ALGORITHM=HS256
AUTH_ACCESS_TOKEN_EXPIRE_MINUTES=15
AUTH_REFRESH_TOKEN_EXPIRE_DAYS=7

# OAuth token encryption (base64 32-byte key)
AUTH_OAUTH_TOKEN_ENCRYPTION_KEY=your-encryption-key

# Frontend URL
AUTH_FRONTEND_URL=http://localhost:3000

# Recruitment LLM (optional)
RECRUITMENT_LLM_BASE_URL=https://gemma4.aibuddy.vn/v1
RECRUITMENT_LLM_MODEL=bg-digitalservices/Gemma-4-26B-A4B-it-NVFP4
```

### Frontend (.env.local)

```env
# Backend API URL (FastAPI) — base cho mọi API call từ vroom-hr/lib/api/client.ts
NEXT_PUBLIC_API_URL=http://localhost:8000

# AI Studio / Gemini (legacy — Phase 3 thay bằng /api/assistant)
GEMINI_API_KEY=""

# URL hosting app
APP_URL="http://localhost:3000"
```

## Modules

| Module        | Description                                      |
| ------------- | ------------------------------------------------ |
| `identity`    | Auth, OAuth, JWT, roles, whitelist, audit        |
| `employee`    | Employee CRUD, departments, positions, documents |
| `recruitment` | Candidate pipeline, CV processing (AI)           |
| `gmail`       | Gmail connection, sending, sync metadata         |

Attendance đã có backend và UI HR chính; các trang schedule/holiday/leave/overtime còn chưa hoàn thiện. Payslip đã có CRUD/publish cho HR và read-only cho Employee. Payroll calculation engine, cấu hình phụ cấp và thuế chưa được triển khai.

## Development

```bash
# Backend linting
cd backend
ruff check src/ && ruff format src/
mypy src/
pytest tests/

# Frontend linting
cd vroom-hr
pnpm lint
pnpm build
pnpm test
```

## License

MIT
