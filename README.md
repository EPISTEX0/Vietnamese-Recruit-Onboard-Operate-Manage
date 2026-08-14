<p align="center">

  <a href="./docs/assets/hero-banner.png">
    <img src="./docs/assets/hero-banner.png" alt="Vroom HR — Open-Source Self-Hosted Vietnamese HRM Platform" width="100%" />
  </a>

</p>

<p align="center">
  <strong>Vroom HR</strong><br/>
  Open-Source Self-Hosted Vietnamese HRM Platform (Recruit - Onboard - Operate - Manage)
</p>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
  <a href="./backend"><img src="https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?logo=python&logoColor=white" alt="Python 3.11+" /></a>
  <a href="./frontend"><img src="https://img.shields.io/badge/Next.js-15-black.svg?logo=next.js&logoColor=white" alt="Next.js 15" /></a>
  <img src="https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Docker-compose-2496ED.svg?logo=docker&logoColor=white" alt="Docker" />
  <img src="https://img.shields.io/badge/build-passing-brightgreen.svg" alt="Build Passing" />
  <img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg" alt="PRs Welcome" />
</p>

<p align="center">
  <b><a href="./README.vi.md">Tiếng Việt</a></b> |
  <a href="./README.md">English</a>
</p>

---

## Overview

**Vroom HR** is an open-source, self-hosted Human Resource Management platform purpose-built for modern Vietnamese enterprises. Every company runs its own isolated deployment — its own server and its own database — so sensitive HR data never leaves your infrastructure.

Vroom HR goes beyond traditional HRM software by embedding **AI automation and a dual-knowledge-base RAG assistant** directly into the employee lifecycle: from sourcing and screening candidates, through onboarding, to the day-to-day operation and management of your workforce.

**Four key pillars:**

1. **Self-Hosted & Single-Tenant** — Each deployment serves exactly one company, with its own database and object storage running on your infrastructure. No shared tenancy. AI capabilities (LLM and embeddings) are reached through OpenAI-compatible endpoints that **you** configure — point them at a cloud API for a lighter deployment, or at an endpoint inside your own network to keep data local.
2. **AI Automation** — Automated **CV parsing**, **job-application intent classification** of inbound emails, and seamless **Gmail integration** that feeds the recruitment pipeline with human-in-the-loop review.
3. **Dual-KB RAG Knowledge Base** — Two **physically isolated** knowledge bases (HR KB and Employee KB) indexed with **pgvector** and **MinIO**, powering domain-aware AI assistants for both HR and employees.
4. **All-in-One HR Suite** — One platform covering the entire workforce journey: **Recruit → Onboard → Operate → Manage**, including attendance, employee requests, and payslips.

> **Terminology note:** For the canonical domain glossary (Organization, Candidate, Backbone Flow, Knowledge Base, AI Assistant, and more), see [`CONTEXT.md`](./CONTEXT.md). Architectural decisions live in [`docs/adr/`](./docs/adr/).

<p align="center">
  <a href="./docs/assets/ai-features.png">
    <img src="./docs/assets/ai-features.png" alt="Vroom HR AI Features & RAG Architecture" width="100%" />
  </a>
</p>

---

## Features

| Area | Capabilities |
| --- | --- |
| **Recruitment** | Email ingestion via Gmail, AI intent classification (`job_application` / `partner` / `event` / `internal` / `other`), AI CV parsing with human-in-the-loop review, Candidate pipeline (`new → reviewing → interview scheduled → accepted/rejected/archived`), Job Openings, Recruitment Inbox. |
| **AI Automation** | Event-driven background tasks — no chat. Classifies inbound emails, parses CVs into structured data, and moves accepted candidates straight into onboarding. |
| **AI Assistant (HR)** | Conversational assistant that *reads* recruitment & onboarding data and *drafts* proposals (e.g. interview invitations); it never writes to the database — HR confirms every write (human-in-the-loop). |
| **AI Assistant (Employee)** | Self-service assistant that reads only the asking employee's own data and can draft employee-owned requests (leave, overtime), never writing on its own. |
| **Dual-KB RAG** | Two physically isolated knowledge bases: **HR KB** (HR-only docs) and **Employee KB** (docs HR publishes). Ingestion is asynchronous via an ARQ worker: PDF/DOCX → chunk → embed (1024-dim vectors, via your configured embeddings endpoint) → pgvector, with raw files stored on MinIO. |
| **Onboarding** | Checklist-driven onboarding triggered by the `candidate_accepted` event. `OnboardingProcess` with fixed tasks; the employee becomes **active** once all mandatory tasks and department/position/manager/start-date are complete. |
| **Employee Self-Service** | Employee accounts activated after onboarding: profile, leave & overtime requests, attendance, payslips. |
| **Operate & Manage** | Attendance records, Employee Requests (leave/overtime) reviewed by HR, Payslip publishing (read-only for employees). |
| **Auth & Security** | Google OAuth2 + JWT cookies, role-based access (`SYSTEM_ADMIN` / `HR` / `Employee`), strict separation between infrastructure admin and HR business data. |
| **i18n** | Vietnamese-first interface (default), with a full English/`next-intl` setup. |

---

## Quick Start

> **Prerequisites:** Docker + Docker Compose, `uv` (Python ≥ 3.11), and `pnpm` (Node ≥ 20).

### 1. Infrastructure

```bash
git clone git@github.com:EPISTEX0/Vietnamese-Recruit-Onboard-Operate-Manage.git
cd Vietnamese-Recruit-Onboard-Operate-Manage

# Create the one .env this project uses, at the repo root. Required, and required
# *first*: docker-compose.yml declares `env_file: - .env` for the backend and the
# three workers, and Compose refuses to load the project at all when it is missing
# — even for `up postgres redis`. Running the backend on the host reads this same file.
cp .env.example .env

# Start core infrastructure (PostgreSQL+pgvector, Redis)
docker compose up -d postgres redis

# Optional — full RAG stack (embedding service + MinIO object storage):
# set EMBEDDING_API_BASE_URL / EMBEDDING_API_KEY in .env, then:
# docker compose up -d
```

> `vroom-embedding` is a thin proxy: it calls whichever OpenAI-compatible
> `/embeddings` endpoint you configure, the same way `RECRUITMENT_LLM_BASE_URL`
> and `ASSISTANT_LLM_BASE_URL` work. It verifies at startup that the endpoint
> returns `EMBEDDING_DIMENSIONS`-wide vectors and refuses to start otherwise,
> so a misconfigured model cannot corrupt the pgvector index.

### 2. Backend

```bash
# No .env is created here. Edit the root .env from step 1 — set at minimum
# AUTH_JWT_SECRET_KEY and AUTH_OAUTH_TOKEN_ENCRYPTION_KEY, plus your Google OAuth
# credentials if you use the Gmail integration. Do not create backend/.env: it
# would shadow the root .env entirely rather than adding to it.

cd backend
uv sync
uv run alembic upgrade head          # apply database migrations

# Start the API server (http://localhost:8000 — Swagger UI at /docs)
uv run uvicorn src.main:app --reload --port 8000
```

Run the background workers in separate terminals:

```bash
# Gmail sync + AI classification (polls every 5 minutes)
uv run arq src.modules.gmail.worker.WorkerSettings

# Knowledge Base ingestion (PDF/DOCX → embeddings → pgvector)
uv run arq src.modules.knowledge_base.worker.KnowledgeBaseWorkerSettings

# Onboarding (consumes candidate_accepted, drives OnboardingProcess)
uv run arq src.modules.onboarding.worker.OnboardingWorkerSettings
```

> **Note:** when run via Docker Compose, all three workers plus the `vroom-embedding` service are launched automatically.

### 3. Frontend

```bash
cd ../frontend                       # package: vroom-hr
cp .env.example .env.local
# Set NEXT_PUBLIC_API_URL=http://localhost:8000 and NEXT_PUBLIC_APP_URL

pnpm install
pnpm dev                             # http://localhost:3000
```

> Default dev account: `admin@vroomhr.com` / `VroomAdmin!2026` (see `AGENTS.md`).

---

## Environment Variables

> There is **one** backend-side env file: **`.env` at the repo root**, created with
> `cp .env.example .env`. The next two tables are two views of that same file, split by
> concern — they are not two files. Do not create `backend/.env`: python-dotenv stops at
> the first `.env` it finds walking upward, so that file would shadow the root one
> entirely rather than adding to it.

### Backend application settings (in the root `.env`)

| Variable | Description | Default |
| --- | --- | --- |
| `DATABASE_URL` | Async PostgreSQL DSN (main DB, pgvector) | `postgresql+asyncpg://postgres:postgres@localhost:5432/vroom_hr` |
| `AUTH_DATABASE_URL` | Auth module database DSN | `postgresql+asyncpg://…/vroom_hr` |
| `AUTH_REDIS_URL` | Redis DSN for cache & ARQ | `redis://localhost:6379/0` |
| `AUTH_GOOGLE_CLIENT_ID` / `AUTH_GOOGLE_CLIENT_SECRET` | Google OAuth2 credentials | — |
| `AUTH_GOOGLE_REDIRECT_URI` | OAuth redirect callback | `http://localhost:8000/api/auth/callback` |
| `AUTH_JWT_SECRET_KEY` | JWT signing secret (**change in prod**) | — |
| `AUTH_OAUTH_TOKEN_ENCRYPTION_KEY` | AES-256-GCM base64 key for OAuth tokens | — |
| `AUTH_FRONTEND_URL` | Frontend URL for CORS / redirects | `http://localhost:3000` |
| `RECRUITMENT_LLM_BASE_URL` / `RECRUITMENT_LLM_MODEL` | LLM for CV parsing & classification | OpenAI-compatible endpoint |
| `ASSISTANT_LLM_BASE_URL` / `ASSISTANT_LLM_MODEL` | LLM for the AI assistants | OpenAI-compatible endpoint |
| `KB_MINIO_BUCKET` / `KB_EMBEDDING_SERVICE_URL` / `KB_DATABASE_URL` | Knowledge Base storage, embedding & DB | `knowledge-base` / `http://localhost:8080` |

### Infrastructure & Docker Compose (in the same root `.env`)

| Variable | Description | Default |
| --- | --- | --- |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | PostgreSQL container credentials | `postgres` / `postgres` / `vroom_hr` |
| `REDIS_PASSWORD` | Redis container password | — |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | MinIO container admin credentials | `vroomminio` / `vroomminio` |
| `EMBEDDING_API_BASE_URL` | OpenAI-compatible embeddings endpoint `vroom-embedding` calls. No default — the service refuses to start until you set it. Point it at a hosted API, or at an endpoint on your own network (vLLM, TEI, LocalAI) to keep document text in-house | — **(required)** |
| `EMBEDDING_API_KEY` | API key for that endpoint (leave empty if yours needs none) | — |
| `EMBEDDING_MODEL_NAME` | Embedding model name exactly as your endpoint spells it (e.g. `BAAI/bge-m3` locally, `text-embedding-3-small` hosted). No default — unset, it would surface as an upstream 502 instead of a config error | — **(required)** |
| `EMBEDDING_DIMENSIONS` | Vector width; must match the `Vector(1024)` pgvector column | `1024` |
| `EMBEDDING_BATCH_SIZE` | Max texts per upstream call (the default provider caps at 10) | `10` |
| `EMBEDDING_SEND_DIMENSIONS` | Send an explicit `dimensions` field; set `false` for endpoints that reject it (e.g. vLLM with a non-Matryoshka model) | `true` |
| `EMBEDDING_PORT` | `vroom-embedding` service port | `8080` |
### Frontend (`frontend/.env.local`)

| Variable | Description | Default |
| --- | --- | --- |
| `NEXT_PUBLIC_API_URL` | Backend (FastAPI) base URL for all API calls | `http://localhost:8000` |
| `NEXT_PUBLIC_APP_URL` | Public URL where the frontend is hosted | `http://localhost:3000` |

> `backend/.env.example` is the reference catalogue — look up the complete set of module-prefixed settings (`AUTH_`, `GMAIL_`, `EMPLOYEE_`, `RECRUITMENT_`, `ASSISTANT_LLM_`, `KB_`) and their defaults there, then write the ones you need into the root `.env`. It is not a file to copy.

---

## High-Level Architecture

```
Client (Next.js 15)  ──►  FastAPI Backend  ──►  PostgreSQL (pgvector)
        ▲                      │   │   │
        │                      │   │   └──► Redis (cache + ARQ queue)
        │                      │   └──────► MinIO (object storage)
        │                      └──────────► ARQ Workers (gmail · kb · onboarding)
        │                                   │
        │                                   └──► LLM Adapters ──► vroom-embedding ──► embeddings endpoint (1024-dim)
        └────────────────────────────────────────────┘
```

For a deep dive including the three Mermaid diagrams — **System High-Level Architecture**, **Dual-KB RAG**, and the **Backbone Flow sequence** — see [`ARCHITECTURE.md`](./ARCHITECTURE.md).

---

## Development & Testing

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

Reproduce the test environment:

```bash
# Reset the database and re-apply migrations (Docker infra must be running)
docker exec vroom-postgres psql -U postgres -c "SELECT pg_terminate_backend(pg_stat_activity.pid) FROM pg_stat_activity WHERE pg_stat_activity.datname = 'vroom_hr' AND pid <> pg_backend_pid();"
docker exec vroom-postgres psql -U postgres -c "DROP DATABASE vroom_hr;"
docker exec vroom-postgres psql -U postgres -c "CREATE DATABASE vroom_hr;"
cd backend && uv run alembic upgrade head
```

> Contribution guide, issue templates, and PR standards are managed under `.github/` per our [Repository Governance](https://github.com/EPISTEX0/Vietnamese-Recruit-Onboard-Operate-Manage/blob/main/docs/adr/0011-github-repository-governance-and-documentation-standard.md) standard. Domain language and AI concepts: [`CONTEXT.md`](./CONTEXT.md).

---

## Roadmap

- **Current scope** — Backbone Flow end-to-end (Recruit → Onboard → Operate → Manage), Dual-KB RAG, HR & Employee AI Assistants, attendance, employee requests, payslips.
- **Future** — Autonomous **AI Agent** (self-decided, self-executed writes) is explicitly out of current scope and tracked as a forward-looking direction only.

## License

Vroom HR is released under the [MIT License](./LICENSE). You are free to use, modify, and self-host Vroom HR for your own organization.