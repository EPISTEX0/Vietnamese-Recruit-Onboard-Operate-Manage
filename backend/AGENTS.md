# Backend Agent Instructions

## Stack

- Python 3.11+ (`requires-python = ">=3.11"`), FastAPI, SQLModel, PostgreSQL 15 + pgvector (asyncpg), Redis
- Alembic migrations, MinIO storage, arq task queue
- Ruff (line-length=100, select E/F/I/N/W/UP), MyPy (`python_version = "3.11"`)
- pytest + pytest-asyncio, Hypothesis, respx, testcontainers

Môi trường là **uv-only**. Mọi lệnh Python chạy qua `uv run`, không `pip install`, không venv thủ công.

---

## Database Migrations

Migrations nằm ở `alembic/versions/`, đặt tên `NNN_<slug>.py` và chạy theo thứ tự số.

Đây là thư mục thay đổi mỗi tuần — **đừng chép danh sách migration vào doc**, hỏi thẳng repo:

```bash
# Migration mới nhất trên đĩa
ls alembic/versions/ | sort | tail -5

# Head revision theo Alembic (nguồn sự thật khi có nhiều nhánh)
uv run alembic heads

# Revision DB hiện đang ở
uv run alembic current

# Toàn bộ lịch sử kèm quan hệ down_revision
uv run alembic history
```

Muốn biết một bảng được tạo/sửa ở đâu thì `rg '<tên_bảng>' alembic/versions/` — nhanh và luôn đúng
hơn bảng tra tay.

### Luật migration

1. Một migration cho một thay đổi schema, đánh số tuần tự tiếp theo số lớn nhất đang có.
2. Autogenerate hiện **sạch**: drift đã đóng 113 → 0 (xem
   [`../docs/schema-drift-audit.md`](../docs/schema-drift-audit.md)), nên `alembic revision --autogenerate`
   không sinh ra gì trên schema đúng. Vì thế **mọi diff nó sinh ra đều là tín hiệu thật** — drift mới, hoặc
   ai đó đổi model mà quên viết migration. Vẫn không apply mù: đọc diff, xác định bên nào sai (model hay
   DB), rồi mới quyết.
3. Không sửa migration đã merge. Thay đổi bằng migration mới.

---

## Khôi phục / Reset database

Deployment self-hosted mất tài khoản `system_admin` (người dựng hệ thống nghỉ việc, quên mật khẩu, ...)
không cần `psql` tay nữa. `src/cli.py` (`python -m src.cli`) là CLI cứu hộ chạy trong container backend:

```bash
# Tạo system_admin mới -- CHỈ chạy được khi deployment chưa có system_admin nào đang active
docker compose exec backend uv run python -m src.cli create-admin --email admin@example.com --name "Admin"

# Reset mật khẩu một tài khoản đã tồn tại (còn tài khoản nhưng mất mật khẩu)
docker compose exec backend uv run python -m src.cli reset-password --email admin@example.com
```

Cả hai lệnh in mật khẩu tạm ra stdout đúng một lần (không lưu ở đâu khác) và đặt
`must_change_password=True` trên tài khoản. Không có `--password`: mật khẩu qua tham số dòng lệnh nằm
lại trong `ps`/shell history, mật khẩu tạm thì không.

`create-admin` **từ chối** (exit code khác 0) khi deployment còn ít nhất một `system_admin` đang
`is_active=true` -- đây là lệnh cứu hộ, không phải lệnh cấp phát. Muốn tạo thêm admin khi deployment
vẫn còn admin sống thì dùng `/api/system-admin` qua tài khoản đang có, không dùng CLI này. `reset-password`
mới là lệnh phục vụ đúng khoảng trống đó (còn tài khoản, mất mật khẩu).

Cả hai ghi `audit_logs` với `admin_user_id`/`admin_email` là **chính tài khoản bị tác động** (tạo mới
hoặc bị reset) và đánh dấu nguồn gốc bằng `details = {"actor": "cli"}` -- `AuditLog.admin_user_id` là FK
NOT NULL nên không có actor "ẩn danh" nào biểu diễn được (QĐ-02, issue #423).

Không bind-mount `src/` trong container (xem §Database Session ở dưới) -- sửa `src/cli.py` rồi verify
phải **rebuild** image (`docker compose build backend && docker compose up -d backend`), `restart`
không đổi gì.

---

## Modules

`src/modules/` có đúng 10 module:

| Module             | Prefix                                                                                                                                                          | Mô tả                                            |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `identity`         | `/api/auth`, `/api/system-admin`, `/api/hr/organization/ai-config`                                                                                                | Local auth, JWT, roles, audit log                |
| `employee`         | `/api/employees`, `/api/departments`, `/api/positions`, `/api/documents`                                                                                          | CRUD, Excel import, document vault               |
| `recruitment`      | `/api/recruitment`, `/api/recruitment/candidates`, `/api/recruitment/job-openings`, `/api/recruitment/job-applications`, `/api/recruitment/cv-review`, `/api/recruitment/evaluation`, `/api/recruitment/inbox`, `/api/recruitment/calendar-conflicts`, `/api/system-admin/runtime` | Candidate pipeline, CV parsing, job openings     |
| `gmail`            | `/api/gmail`, `/api/outbound-emails`                                                                                                                              | Gmail API integration, outbound email            |
| `attendance`       | `/api/attendance`                                                                                                                                                 | Check-in/out, leave, overtime, work schedules    |
| `onboarding`       | `/api/onboarding`                                                                                                                                                 | Onboarding flow (có `worker.py` chạy nền)        |
| `payslip`          | `/api/payslips`, `/api/hr/payslips`                                                                                                                               | Payslip lưu trữ và phát hành cho nhân viên       |
| `employee_request` | `/api/employee-requests`, `/api/hr/employee-requests`                                                                                                             | Nhân viên gửi yêu cầu, HR duyệt                  |
| `assistant`        | `/api/assistant`, `/api/ess/assistant`                                                                                                                            | Trợ lý LLM cho HR và cho nhân viên               |
| `knowledge_base`   | `/api/knowledge-base`                                                                                                                                             | RAG knowledge base (có `worker.py` chạy nền)     |

Không có module `payroll` và không có module `self_service`. Việc tính lương không nằm trong backend
(xem §Domain reference), phần self-service của nhân viên nằm ở `employee_request`, `payslip`
và `/api/ess/assistant`.

---

## Cross-Module Data Flow

```
Gmail Incoming Email → Gmail Module → Recruitment Pipeline
                                                    ↓
                                         Candidate (new)
                                                    ↓
                              promote_candidate() → Employee (new)
                                                    ↓
                                         Employee Module → Onboarding
                                                    ↓
                                         Attendance Module
                                                    ↓
                                    Payslip (HR nhập số) → Outbound Email
```

### Key Flows

1. **Recruitment → Employee**: `recruitment_service.promote_candidate()` tạo employee từ candidate
   được hire.

2. **Attendance**: `attendance_records` (check-in/out), `overtime_requests` (đã approve),
   `leave_requests` (đã approve, trừ leave balance).

3. **Employee self-service**: nhân viên đọc payslip của mình qua `/api/payslips`, gửi đơn qua
   `/api/employee-requests`, hỏi trợ lý qua `/api/ess/assistant`.

4. **Gmail Integration**:
   - Inbound: sync email labels → recruitment pipeline.
   - Outbound: gửi email tuyển dụng, thông báo.

---

## Shared Infrastructure

### Database Session

Session async là FastAPI dependency, sống ở `src/modules/identity/container.py` và được mọi module
import lại:

```python
from src.modules.identity.container import get_db_session

# Trong container.py của module:
async def get_employee_repository(
    session: AsyncSession = Depends(get_db_session),
) -> EmployeeRepository:
    return EmployeeRepository(session)
```

`get_db_session` commit khi thoát sạch, rollback khi có exception. Nhưng phần sau `yield` chỉ chạy lúc
FastAPI tháo dependency stack, tức là **sau** khi response đã gửi (#312): client cầm 200 cho một lần ghi
chưa bền, và một `commit()` hỏng ở teardown không còn đường nào báo về.

Nên teardown là **lưới đỡ**, không phải ranh giới transaction. Endpoint ghi phải tự `await
session.commit()` trước khi handler trả về — đặt sau lời gọi audit, để bản ghi audit cũng bền trước khi
response đi. Đa số use case đã commit tường minh ở tầng application/infrastructure; nơi nào chưa thì
commit ở handler. `identity/api/admin_router.py` theo quy ước này cho cả 25 endpoint ghi của nó,
`identity/api/router.py` cho cả 12 endpoint ghi của nó (#320).

Phân loại "có ghi hay không" phải truy tới tầng thật sự chạm DB, **không** suy theo HTTP method:
`GET /api/auth/callback` hoàn tất OAuth consent và `GET /api/auth/organization-google-connection`
thu hồi legacy grant, nên guard chống-bỏ-sót phải phân hoạch mọi route chứ không lọc
`POST/PUT/PATCH/DELETE`. Ngược lại `POST /api/auth/refresh` chỉ đọc rồi ký JWT, không cần commit.
Endpoint mà tầng application đã commit **vô điều kiện** (`AuthService.setup_first_run`,
`PasswordResetService.reset_password`) thì **không** thêm commit thứ hai ở handler. Commit **có điều
kiện** thì ngược lại: `PasswordResetService.create_reset_token` chỉ commit trên nhánh gửi được email,
nhánh gửi hỏng trả về sớm với các row đã flush — nên `/forgot-password` vẫn commit ở handler để cả hai
nhánh đều bền, chấp nhận commit thứ hai (không có gì để ghi) trên nhánh thành công.

Nếu audit đi qua `log_audit` (`recruitment`, nuốt lỗi theo R17.5) thì **không** viết `commit()` trần —
dùng dạng `InterviewSchedulerService._commit_audit()`, vì một flush audit hỏng để lại session cần
rollback và `commit()` kế tiếp sẽ ném `PendingRollbackError`. `identity` dùng
`AuditService.log_action`, hàm này không nuốt lỗi nên `commit()` trần là đúng.

`src/database.py` có một `get_session()` **sync** (SQLModel `Session`) cho script/tooling chạy ngoài
request cycle. Không dùng nó trong đường API.

### MinIO Client

Không có MinIO client dùng chung — mỗi module có client riêng, cấu hình theo settings của module đó:

- `src/modules/employee/infrastructure/minio_client.py`
- `src/modules/recruitment/infrastructure/minio_client.py`

```python
from src.modules.employee.infrastructure.minio_client import MinIOClient

client = MinIOClient(settings)
url = await client.upload_file(path, file_data, content_type)
data = await client.download_file(path)
link = await client.generate_presigned_url(path, expires_seconds=900)
await client.delete_file(path)
```

Lấy instance qua `container.py` của module, đừng tự dựng trong router.

### Redis (Cache & Rate Limit)

```python
from src.modules.identity.container import get_redis_client
from src.modules.identity.infrastructure.rate_limiter import RateLimiter
```

`RateLimiter` được wire sẵn trong `src/modules/identity/container.py` (Redis client + rate limit settings) —
lấy qua DI thay vì tự khởi tạo.

### Dependency Injection

Mỗi module có `container.py` khai các provider `Depends()`. Router import provider từ container,
không tự instantiate service.

Router **không** được export từ `container.py`. `main.py` import từng router object theo tên và
include trực tiếp — 24 lời gọi `app.include_router(...)`, prefix đã nằm sẵn trên chính `APIRouter`:

```python
# src/main.py
from src.modules.identity.api.router import router as auth_router

app.include_router(auth_router)
```

### Error Handling Pattern

FastAPI đăng ký exception handler trên **app**, không trên router (`APIRouter` không có
`exception_handler`). Mỗi module export một hàm `register_<module>_error_handlers(app)`:

```python
# 1. Domain exception (domain/exceptions.py) — kế thừa base error của module.
#    Base nhận đúng một tham số: message override tuỳ chọn.
class EmployeeNotFoundError(EmployeeError):
    status_code = 404
    error_code = "EMPLOYEE_NOT_FOUND"
    message = "Employee not found"

# 2. Service raise domain exception, không raise HTTPException.
#    Không truyền kwargs ngữ cảnh — exception chỉ mang code + message.
raise EmployeeNotFoundError()

# 3. api/error_handler.py bắt base class, trả JSON đồng nhất
def register_employee_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(EmployeeError)
    async def _employee_error_handler(request: Request, exc: EmployeeError) -> JSONResponse:
        lang = get_request_language(request)
        log_domain_exception(exc, module="employee")
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.error_code, "message": resolve_error_message(exc, lang)}},
        )

# 4. main.py gọi hàm đăng ký
register_employee_error_handlers(app)
```

Body lỗi luôn có dạng `{"error": {"code": ..., "message": ...}}`.

---

## Error Codes & Messages

Toàn bộ message hướng tới người dùng nằm **tập trung** ở `src/shared/messages.py` — một dict
`MESSAGES` map error code → `{"vi": ..., "en": ...}`. Không có registry per-module;
`src/modules/<module>/domain/exceptions.py` chỉ khai `error_code` trỏ vào catalog này.

```python
from src.shared.messages import get_message

get_message("AUTH_INVALID_CREDENTIALS")            # tiếng Việt (mặc định)
get_message("AUTH_INVALID_CREDENTIALS", lang="en") # fallback tiếng Anh
```

Thêm error code mới = thêm entry vào `MESSAGES` (cả `vi` và `en`) rồi trỏ `error_code` của exception
vào đó. Danh sách code hiện có đọc thẳng từ file, đừng chép ra doc:

```bash
rg -o '"[A-Z][A-Z0-9_]+":' src/shared/messages.py | sort -u
```

`resolve_error_message` + `get_request_language` (cùng file) là thứ error handler dùng để chọn ngôn
ngữ theo request.

---

## Module Structure (MANDATORY)

Mọi module trong `src/modules/` PHẢI theo:

```
src/modules/<name>/
├── api/
│   ├── router.py          # APIRouter, prefix khai ngay tại đây
│   ├── schemas.py         # Pydantic request/response models
│   └── error_handler.py   # register_<name>_error_handlers(app)
├── application/
│   └── <name>_service.py  # Business logic (no framework deps)
├── domain/
│   ├── entities.py        # SQLModel table classes
│   ├── enums.py           # str Enums
│   └── exceptions.py      # Domain-specific exceptions (not HTTP)
├── infrastructure/
│   ├── config.py          # pydantic-settings với env prefix
│   └── <name>_repository.py  # Async DB operations
└── container.py           # FastAPI Depends() wiring
```

Module có tác vụ nền (`gmail`, `knowledge_base`, `onboarding`) thêm `worker.py` ở gốc module.
Module có nhiều bề mặt API tách thêm router file (`admin_router.py`, `employee_router.py`, …) —
vẫn trong `api/`.

## Key Rules

1. **Async-first:** mọi thao tác DB trong đường API dùng `AsyncSession`.
2. **DI via container.py:** không instantiate service trong router.
3. **Domain exceptions:** service raise domain exception, `error_handler` map sang HTTP.
4. **No raw SQL in services:** dùng repository.
5. **Auth:** import `get_current_user` từ `src.modules.identity.container`.
6. **Schemas:** Pydantic v2, `model_config = {"from_attributes": True}`.
7. **Messages:** mọi chuỗi hướng người dùng đi qua `src/shared/messages.py`.

## Commands

Chạy từ `backend/`:

```bash
# Run server
uv run uvicorn src.main:app --reload --port 8000

# Migrations
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"

# Lint & format
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Type check
uv run mypy src/

# Test
uv run pytest tests/
uv run pytest tests/modules/payslip/ -q   # một module

# Smoke import (Gate 1 của CI)
uv run python -c "import src.main"
```

## Domain reference — Vietnamese HR

Các hằng số dưới đây là **tham chiếu nghiệp vụ**, không phải mô tả code: backend hiện **không** tính
lương. `payslip` lưu các số HR nhập vào (`gross_salary`, `deductions`, `insurance_employee`,
`taxable_income`, `pit_amount`, `net_salary` — đều `Decimal`) và không suy ra chúng từ công thức nào.
Nếu sau này có module tính lương, đây là luật nó phải theo:

- Personal tax deduction: 11,000,000 VND/tháng
- Dependent deduction: 4,400,000 VND/người/tháng
- Insurance (employee): BHXH 8% + BHYT 1.5% + BHTN 1% = 10.5%
- Insurance (employer): BHXH 17.5% + BHYT 3% + BHTN 1% = 21.5%
- Work days per month: 26 (dùng cho daily rate)
- Progressive tax: 7 bậc (5%, 10%, 15%, 20%, 25%, 30%, 35%)
- OT rates: ngày thường 150%, cuối tuần 200%, ngày lễ 300%
