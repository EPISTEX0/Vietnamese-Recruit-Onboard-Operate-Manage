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

First create the project's single `.env`, at the **repo root**. `docker-compose.yml`
declares `env_file: - .env` for the backend and the three workers, and Compose refuses
to load the project without it — even when you only bring up `postgres redis minio`. So
this comes before any `docker compose` command:

```bash
cp .env.example .env
```

Start the backing services:

```bash
docker compose up -d postgres redis minio
```

Or bring up the full stack (backend, frontend, workers, embedding service):

```bash
docker compose up -d
```

The stack above runs the code baked into the images. To get hot-reload (source
bind-mounted into `backend` and `frontend`), pass the dev overrides explicitly —
Compose does **not** pick `docker-compose.dev.yml` up on its own:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

Only the backend and the frontend are published to your host. PostgreSQL, Redis and
MinIO are attached solely to `vroom-internal-net` (`internal: true`), and Docker never
publishes ports out of an internal network — `localhost:5432`, `:6379`, `:9000` and
`:9001` are connection-refused even while the stack is healthy. Reach those three with
`docker compose exec`:

| Service    | How to reach it                       | Notes                          |
| ---------- | ------------------------------------- | ------------------------------ |
| Backend    | `http://localhost:8000` (Swagger at `/docs`) | FastAPI                 |
| Frontend   | `http://localhost:3000`               | Next.js                        |
| PostgreSQL | `docker compose exec postgres psql -U postgres -d vroom_hr` | pgvector-enabled; db `vroom_hr`, user `postgres`, no password needed inside the container |
| Redis      | `docker compose exec redis redis-cli -a '<REDIS_PASSWORD>'` | Password is `REDIS_PASSWORD` from the root `.env` |
| MinIO      | `docker compose exec minio sh -c 'mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" && mc ls local'` | Object storage for CVs/KB; set the alias explicitly — see the note below |

**Why the MinIO command sets an alias instead of just running `mc ls local`.** The
container does ship a `local` alias with the correct root credentials — but `mc` only
writes `/tmp/.mc/config.json` asynchronously, roughly a minute after the container
starts. Run the bare `mc ls local` before that and it dies with `Access Denied`; run it
later and it exits 0. So the short form is a race, and it loses exactly when you are
most likely to try it — right after `docker compose up -d` — then appears to work when
you retry at leisure. That is what makes it tempting to shorten the command back; don't.
Setting the alias explicitly is time-independent.

Inside the Docker network the same three are `postgres:5432`, `redis:6379` and
`minio:9000` — those are the addresses `docker-compose.yml` hands to the backend and
the workers, and what you should use in any container-side config.

### Backend (Python / uv)

The backend reads the root `.env` you created above. There is no `backend/.env`, and
creating one would shadow the root file entirely rather than adding to it — python-dotenv
stops at the first `.env` it finds walking upward.

```bash
# ...edit the root .env with your local settings...

cd backend

# Install dependencies and sync environment (host-side, no database needed)
uv sync
```

Anything that talks to PostgreSQL, Redis or MinIO has to run **inside** the stack,
because those three are not published to your host (see the table above). Migrations
and the API server are both in that category:

```bash
# Run database migrations
docker compose exec backend uv run alembic upgrade head

# The API server is already running as the `backend` service; for hot-reload
# on source edits, bring the stack up with the dev overrides instead:
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

Running `uv run alembic upgrade head` or `uv run uvicorn src.main:app` directly on
your host will fail with `Connect call failed ('127.0.0.1', 5432)`: their default DSNs
point at `localhost:5432`, and nothing listens there. If you genuinely need a host-side
backend, publish the three services yourself first — the host-only section at the bottom
of `.env.example` explains what that takes.

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
└── vroom-embedding/      # Embedding microservice (Docker) — proxies to a configured OpenAI-compatible endpoint
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

- Reference issues in the footer when relevant: `Closes #123`.
- Write the body explaining **what** and **why**, not how.
- The subject line is imperative and lowercase.

**Subject length is a convention, not a gate** — no commit-msg hook, no CI check enforces it.
This repo merges squash-only, so the branch commit subject never reaches `main`; the string
that survives is the **PR title**, plus the ` (#<PR>)` suffix GitHub appends on merge. Aim
for **≤ 72 characters in the PR title**, counting only what you write — the GitHub-appended
suffix doesn't count against the limit, but an issue reference like `(#123)` that you typed
yourself does. As of 2026-08-17, 11 of the last 15 subjects on `main` exceed 72 characters
by this count (longest: 106); treat that as the current baseline to improve on, not a reason
to ignore the guideline.

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

1. Add a new ADR under `docs/adr/` using the next numeric prefix (e.g. `0013-my-decision.md`).
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