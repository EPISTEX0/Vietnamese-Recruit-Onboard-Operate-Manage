# Contributing to Vroom HR

Thanks for your interest in contributing to **Vroom HR** — the self-hosted, open-source HR platform for Vietnamese businesses.

We welcome contributions of all kinds: bug fixes, new features, documentation, test coverage, and design feedback. This guide explains how to set up your environment, follow our conventions, and get your changes merged.

By participating in this project you agree to abide by our [Code of Conduct](./CODE_OF_CONDUCT.md).

---

## Table of Contents

- [Before You Start](#before-you-start)
- [Development Setup](#development-setup)
  - [Prerequisites](#prerequisites)
  - [Infrastructure (Docker)](#infrastructure-docker)
  - [Backend (Python / uv)](#backend-python--uv)
  - [Frontend (Node / pnpm)](#frontend-node--pnpm)
- [Repository Structure](#repository-structure)
- [Workflow](#workflow)
  - [Branching Strategy](#branching-strategy)
  - [Commit Message Standards](#commit-message-standards)
  - [Pull Request Process](#pull-request-process)
- [Quality Checklist](#quality-checklist)
  - [Backend](#backend)
  - [Frontend](#frontend)
  - [Architectural Changes (ADR)](#architectural-changes-adr)
- [Code of Conduct](#code-of-conduct)
- [Getting Help](#getting-help)

---

## Before You Start

- Read [`CONTEXT.md`](./CONTEXT.md) carefully. It defines the **canonical domain language** for the project (Organization, Candidate, Onboarding, Backbone Flow, Knowledge Base, AI Assistant, …). One concept must have one unambiguous name in code, specs, and docs. Using the wrong term is a review blocker.
- Check open [GitHub Issues](https://github.com/EPISTEX0/Vietnamese-Recruit-Onboard-Operate-Manage/issues) for existing work or discussion before opening a new one.
- For feature work, open an issue and discuss the approach **before** writing a large amount of code.
- Review the ADRs in [`docs/adr/`](./docs/adr/) — architectural decisions live there and may already constrain your approach.

---

## Development Setup

### Prerequisites

| Tool           | Version   | Purpose                            |
| -------------- | --------- | ---------------------------------- |
| Docker         | 24+       | PostgreSQL, Redis, MinIO, workers  |
| Docker Compose | v2        | Orchestrate local services         |
| Python         | 3.11+     | Backend runtime                    |
| `uv`           | latest    | Python package/project manager     |
| Node.js        | 20+       | Frontend toolchain                 |
| `pnpm`         | 9+        | Frontend package manager           |

> **Why `uv` / `pnpm`?** We standardize on `uv` for Python and `pnpm` for Node. Please do **not** introduce `pip`/`pipenv`/`venv` directly or switch the package manager. Respect the existing lockfiles (`backend/uv.lock`, `frontend/pnpm-lock.yaml`).

### Infrastructure (Docker)

Start the backing services:

```bash
docker compose up -d postgres redis minio
```

Or bring up the full stack (backend, frontend, workers, embedding service):

```bash
docker compose up -d
```

| Service   | Endpoint                              | Notes                          |
| --------- | ------------------------------------- | ------------------------------ |
| PostgreSQL | `localhost:5432` (`postgres/postgres`, db `vroom_hr`) | pgvector-enabled              |
| Redis     | `localhost:6379`                      |                               |
| MinIO     | API `localhost:9000` · Console `localhost:9001` | Object storage for CVs/KB   |
| Backend   | `http://localhost:8000` (Swagger at `/docs`) | FastAPI                     |
| Frontend  | `http://localhost:3000`               | Next.js                        |

### Backend (Python / uv)

```bash
cd backend

# Create .env from the template
cp .env.example .env
# ...edit .env with your local settings...

# Install dependencies and sync environment
uv sync

# Run database migrations
uv run alembic upgrade head

# Start the development server
uv run uvicorn src.main:app --reload --port 8000
```

**Test accounts** (local dev):

| Email                | Role  | Password         |
| -------------------- | ----- | ---------------- |
| `admin@vroomhr.com`  | admin | `VroomAdmin!2026` |
| `employee@vroomhr.com` | user | *(unset)*        |

### Frontend (Node / pnpm)

The canonical frontend lives in **`frontend/`** (package `vroom-hr`, Next.js 15, TypeScript, Tailwind v4).

```bash
cd frontend

# Create .env.local from the template
cp .env.example .env.local
# ...edit .env.local (NEXT_PUBLIC_API_URL, NEXT_PUBLIC_APP_URL)...

pnpm install
pnpm dev   # http://localhost:3000
```

---

## Repository Structure

```
├── CONTEXT.md            # Canonical domain language (read this first)
├── ARCHITECTURE.md       # System architecture (Mermaid diagrams)
├── docs/
│   ├── adr/              # Architecture Decision Records
│   └── agents/           # Agent/workflow conventions
├── backend/              # FastAPI + SQLModel + PostgreSQL (uv)
├── frontend/             # Next.js 15 frontend (package vroom-hr, pnpm)
└── vroom-embedding/      # Embedding microservice (Docker)
```

---

## Workflow

### Branching Strategy

We use a simple, trunk-based `main` strategy:

- **`main`** — the default and protected branch. Always deployable. Do **not** commit to `main` directly.
- **Feature branches** — create a short-lived branch off `main` for each piece of work:

  ```bash
  git checkout main
  git pull origin main
  git checkout -b feat/<short-description>
  ```

  Recommended prefixes: `feat/`, `fix/`, `docs/`, `refactor/`, `chore/`, `test/`.

- Keep branches short-lived and rebased. Before opening a PR, rebase on the latest `main`:

  ```bash
  git fetch origin
  git rebase origin/main
  ```

### Commit Message Standards

We follow **Conventional Commits**. This keeps history machine-readable and feeds automatic changelogs and release tooling.

```
<type>(<optional scope>): <description>

[optional body]

[optional footer(s)]
```

Common types:

| Type       | Description                                        |
| ---------- | -------------------------------------------------- |
| `feat:`    | A new feature                                      |
| `fix:`     | A bug fix                                          |
| `docs:`    | Documentation-only changes                         |
| `refactor:`| Code change that neither fixes a bug nor adds a feature |
| `test:`    | Adding or correcting tests                         |
| `chore:`   | Maintenance tasks (tooling, deps, config)          |
| `perf:`    | A performance improvement                          |
| `build:`   | Changes to the build system or dependencies        |

Examples:

```
feat(recruitment): add interview rescheduling endpoint
fix(attendance): reject check-in when already checked in
docs: add SECURITY.md vulnerability reporting guide
refactor(identity): consolidate OAuth config into single source
```

Guidelines:

- The **subject line** is imperative, lowercase, and ≤ 72 characters.
- Reference issues in the footer when relevant: `Closes #123`.
- Write the body explaining **what** and **why**, not how.

### Pull Request Process

1. **Create a PR** against `main` from your feature branch. Use the [Pull Request Template](./.github/PULL_REQUEST_TEMPLATE.md).
2. Fill in every section, link the related issue(s), and list the verification you performed.
3. **Run the quality gates** listed below before requesting review.
4. Maintainers and contributors review the PR. Address review feedback with additional commits (or rebase as requested).
5. A maintainer merges once CI passes and review is approved. The `main` branch is protected;
   squashing is preferred to keep history clean.

---

## Quality Checklist

Run every applicable check **before** opening a PR.

### Backend

```bash
cd backend

# Lint & format (Ruff — line-length 100)
ruff check src/ tests/
ruff format --check src/ tests/

# Static type check (MyPy — strict)
mypy src/

# Tests
pytest tests/ -v
```

Also confirm:

- New migrations are added under `alembic/versions/` with an incremental numeric prefix.
- Database changes are covered by tests.
- No secrets, keys, or personal data are committed (check `.env` handling).

### Frontend

```bash
cd frontend

pnpm lint
pnpm build
```

Also confirm:

- TypeScript compiles with no errors.
- New or changed UI follows the existing design system and the domain language in `CONTEXT.md`.
- No unused dependencies or imports.

### Architectural Changes (ADR)

**Any change that affects the system architecture — data model boundaries, module seams, cross-module flows, security posture, or public interfaces — requires an Architecture Decision Record.**

1. Add a new ADR under `docs/adr/` using the next numeric prefix (e.g. `0012-my-decision.md`).
2. Follow the ADR format of existing records: **context → decision → consequences** (and consider alternatives).
3. Reference the ADR from your PR description and commit message.

If your change is behavioral but not architectural, skip the ADR — but say so explicitly in the PR so reviewers can verify.

---

## Code of Conduct

Please note that this project is released with a [Contributor Code of Conduct](./CODE_OF_CONDUCT.md). By participating in this project you agree to abide by its terms.

---

## Getting Help

- Open an [issue](https://github.com/EPISTEX0/Vietnamese-Recruit-Onboard-Operate-Manage/issues) for bugs, questions, and feature requests.
- Review [`CONTEXT.md`](./CONTEXT.md) and [`AGENTS.md`](./AGENTS.md) for conventions.
- Use [`docs/adr/`](./docs/adr/) to understand prior architectural decisions.

We look forward to your contributions!