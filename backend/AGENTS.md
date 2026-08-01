# Backend Agent Instructions

## Stack

- Python 3.11+, FastAPI, SQLModel, PostgreSQL 15 (asyncpg), Redis 7
- Alembic migrations, MinIO storage, arq task queue
- Ruff (line-length=100, select E/F/I/N/W/UP), MyPy strict
- pytest + pytest-asyncio, Hypothesis, respx, testcontainers

---

## Database Migrations Map

Alembic migrations trong `alembic/versions/`, chạy theo thứ tự số:

### Identity Module

| Migration | Tables Created/Modified                         |
| --------- | ----------------------------------------------- |
| 001       | `users` — bảng users cơ bản                     |
| 002       | `oauth_grants` — OAuth authorization codes      |
| 003       | `refresh_tokens` — JWT refresh tokens           |
| 010       | `users` — thêm cột `role`                       |
| 011       | `whitelist_entries` — email whitelist cho login |
| 012       | `oauth_configs` — OAuth provider configs        |
| 013       | `audit_logs` — audit log cho mọi action         |

### Employee Module

| Migration | Tables Created/Modified                                        |
| --------- | -------------------------------------------------------------- |
| 004       | `departments` — phòng ban                                      |
| 005       | `positions` — chức vụ                                          |
| 006       | `employees` — nhân viên (liên kết departments, positions)      |
| 007       | `employee_documents` — tài liệu nhân viên (hợp đồng, CMND,...) |

### Gmail Module

| Migration | Tables Created/Modified                                          |
| --------- | ---------------------------------------------------------------- |
| 008       | `gmail_credentials`, `gmail_labels` — lưu OAuth tokens và labels |

### Recruitment Module

| Migration | Tables Created/Modified                                          |
| --------- | ---------------------------------------------------------------- |
| 009       | `candidates`, `candidate_statuses`, `candidate_notes` — ứng viên |

### Attendance Module

| Migration | Tables Created/Modified                                  |
| --------- | -------------------------------------------------------- |
| 014       | `leave_types` — loại nghỉ phép (annual, sick, unpaid...) |
| 015       | `leave_balances` — số ngày phép còn lại                  |
| 016       | `leave_requests` — đơn xin nghỉ                          |
| 017       | `work_schedules` — ca làm việc                           |
| 018       | `attendance_records` — chấm công (check-in/out)          |
| 019       | `overtime_requests` — đơn xin OT                         |
| 020       | `holidays` — ngày lễ                                     |

### Payroll Module

| Migration | Tables Created/Modified                       |
| --------- | --------------------------------------------- |
| 021       | `salary_configs` — cấu hình lương cơ bản      |
| 022       | `allowances` — phụ cấp (xăng, điện thoại,...) |
| 023       | `dependents` — người phụ thuộc                |
| 024       | `payroll_periods` — kỳ lương (tháng/năm)      |
| 025       | `payslips` — bảng lương                       |
| 026       | `position_salaries` — lương theo chức vụ      |

---

## Cross-Module Data Flow

```
Gmail Incoming Email → Gmail Module → Recruitment Pipeline
                                                    ↓
                                         Candidate (new)
                                                    ↓
                              promote_candidate() → Employee (new)
                                                    ↓
                                         Employee Module
                                                    ↓
                                         Attendance Module
                                                    ↓
                                         Payroll Module
                                                    ↓
                                         Payslip Email → Gmail Outbound
```

### Key Flows

1. **Recruitment → Employee**: `recruitment_service.promote_candidate()` tạo employee từ candidate được hire

2. **Attendance → Payroll**:
   - `attendance_records` (check-in/out) → tính work hours
   - `overtime_requests` (đã approve) → tính OT hours
   - `leave_requests` (đã approve) → trừ leave balance

3. **Employee → Self-Service**: ESS đọc employee data (profile, leave balance, payslips)

4. **Gmail Integration**:
   - Inbound: Sync email labels → recruitment pipeline
   - Outbound: Gửi payslip, thông báo nghỉ phép

---

## Shared Infrastructure

### Database Session

```python
# Sync session (dùng trong migrations, seed scripts)
from src.database import get_session, engine

# Async session (dùng trong API services)
from src.modules.common.database import get_db_session

# Cách dùng trong service:
async def my_service(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(Employee).where(...))
```

### MinIO Client

```python
from src.infrastructure.minio_client import minio_client

# Upload file
await minio_client.upload(
    bucket="employees",
    object_name=f"documents/{employee_id}/{filename}",
    content=file_content,
    content_type="application/pdf"
)

# Download file
content = await minio_client.download(
    bucket="employees",
    object_name=f"documents/{employee_id}/{filename}"
)

# Presigned URL (download)
url = await minio_client.get_presigned_url(
    bucket="employees",
    object_name=f"documents/{employee_id}/{filename}",
    expires=3600
)
```

### Redis (Cache & Rate Limit)

```python
from src.infrastructure.redis_client import redis_client

# Cache
await redis_client.set("key", "value", expire=3600)
value = await redis_client.get("key")

# Rate limit
from src.modules.identity.application.rate_limiter import RateLimiter
limiter = RateLimiter(redis_client)
await limiter.check_rate_limit(user_id, "login", max_attempts=5, window=300)
```

### Dependency Injection

```python
# container.py trong mỗi module
from src.modules.<module>.container import router

# Trong main.py, đăng ký:
app.include_router(router, prefix="/api/<module>")
```

### Error Handling Pattern

```python
# 1. Domain exception (trong domain/exceptions.py)
class EmployeeNotFoundError(Exception):
    status_code = 404
    error_code = "EMPLOYEE_NOT_FOUND"
    message = "Employee not found"

# 2. Service raise exception
raise EmployeeNotFoundError(employee_id=id)

# 3. API layer catch và convert (trong api/error_handler.py)
@router.exception_handler(EmployeeNotFoundError)
async def handle_employee_not_found(request: Request, exc: EmployeeNotFoundError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error_code": exc.error_code, "message": exc.message}
    )
```

---

## Error Codes Registry

### Identity Module (Auth)

| Error Code                | HTTP | Message                                |
| ------------------------- | ---- | -------------------------------------- |
| `AUTH_ERROR`              | 500  | An authentication error occurred       |
| `AUTH_INVALID_STATE`      | 400  | Invalid authentication state           |
| `AUTH_GOOGLE_ERROR`       | 502  | Failed to authenticate with Google     |
| `AUTH_ACCESS_DENIED`      | 403  | Access denied. Contact administrator.  |
| `AUTH_INSUFFICIENT_SCOPE` | 400  | Please grant all requested permissions |
| `AUTH_INVALID_TOKEN`      | 401  | Invalid or expired token               |
| `AUTH_INVALID_RESET_TOKEN` | 400  | Liên kết đặt lại mật khẩu không hợp lệ hoặc đã hết hạn |
| `AUTH_RATE_LIMITED`       | 429  | Too many login attempts                |

### Employee Module

| Error Code                 | HTTP | Message                                        |
| -------------------------- | ---- | ---------------------------------------------- |
| `EMPLOYEE_ERROR`           | 500  | An employee module error occurred              |
| `EMPLOYEE_DUPLICATE_EMAIL` | 409  | Employee with this email already exists        |
| `EMPLOYEE_NOT_FOUND`       | 404  | Employee not found                             |
| `DEPARTMENT_NOT_FOUND`     | 404  | Department not found                           |
| `POSITION_NOT_FOUND`       | 404  | Position not found                             |
| `DEPARTMENT_HAS_EMPLOYEES` | 409  | Cannot delete department with active employees |
| `POSITION_HAS_EMPLOYEES`   | 409  | Cannot delete position with active employees   |
| `FILE_TOO_LARGE`           | 413  | File exceeds maximum size of 10MB              |
| `UNSUPPORTED_FILE_TYPE`    | 415  | File type not supported                        |

### Recruitment Module

| Error Code                    | HTTP | Message                             |
| ----------------------------- | ---- | ----------------------------------- |
| `RECRUITMENT_ERROR`           | 500  | A recruitment module error occurred |
| `CANDIDATE_NOT_FOUND`         | 404  | Candidate not found                 |
| `CV_DOCUMENT_NOT_FOUND`       | 404  | CV document not found               |
| `INVALID_STATUS_TRANSITION`   | 409  | Invalid status transition           |
| `CV_FILE_MISSING`             | 404  | CV file not found in storage        |
| `STORAGE_SERVICE_UNAVAILABLE` | 502  | Storage service is unavailable      |
| `GMAIL_NOT_CONNECTED`         | 409  | Gmail is not connected              |
| `PIPELINE_TIMEOUT`            | 504  | CV processing pipeline timed out    |
| `OCR_EXTRACTION_FAILED`       | 502  | OCR text extraction failed          |
| `LLM_PARSE_FAILED`            | 502  | LLM CV parsing failed               |

### Attendance Module

| Error Code                        | HTTP | Message                                      |
| --------------------------------- | ---- | -------------------------------------------- |
| `ATTENDANCE_ERROR`                | 500  | An attendance module error occurred          |
| `LEAVE_TYPE_NOT_FOUND`            | 404  | Leave type not found                         |
| `LEAVE_REQUEST_NOT_FOUND`         | 404  | Leave request not found                      |
| `INSUFFICIENT_LEAVE_BALANCE`      | 400  | Insufficient leave balance                   |
| `LEAVE_OVERLAP`                   | 409  | Leave request overlaps with existing request |
| `INVALID_LEAVE_STATUS_TRANSITION` | 400  | Invalid leave status transition              |
| `LEAVE_DATE_IN_PAST`              | 400  | Cannot cancel leave that has already started |
| `ALREADY_CHECKED_IN`              | 400  | Already checked in today                     |
| `NOT_CHECKED_IN`                  | 400  | Not checked in today                         |
| `ALREADY_CHECKED_OUT`             | 400  | Already checked out today                    |
| `ATTENDANCE_RECORD_NOT_FOUND`     | 404  | Attendance record not found                  |
| `OVERTIME_REQUEST_NOT_FOUND`      | 404  | Overtime request not found                   |
| `OVERTIME_LIMIT_EXCEEDED`         | 400  | Overtime limit exceeded                      |
| `SCHEDULE_NOT_FOUND`              | 404  | Work schedule not found                      |

### Gmail Module

| Error Code                  | HTTP | Message                                   |
| --------------------------- | ---- | ----------------------------------------- |
| `GMAIL_ERROR`               | 500  | A Gmail module error occurred             |
| `UNAUTHORIZED`              | 401  | Missing or invalid authentication session |
| `GMAIL_NOT_CONNECTED`       | 403  | Gmail is not connected                    |
| `GMAIL_CONNECT_FAILED`      | 400  | Gmail connection failed                   |
| `LABEL_NAMESPACE_VIOLATION` | 400  | Label must be within VroomHR/ namespace   |
| `GMAIL_FETCH_ERROR`         | 502  | Failed to fetch data from Gmail API       |
| `MESSAGE_NOT_FOUND`         | 404  | Gmail message not found                   |
| `GMAIL_LABEL_REMOVE_FAILED` | 502  | Failed to remove label                    |
| `GMAIL_SEND_FAILED`         | 502  | Failed to send email                      |
| `RATE_LIMITED`              | 429  | Rate limit exceeded                       |

### Payroll Module

| Error Code               | HTTP | Message                              |
| ------------------------ | ---- | ------------------------------------ |
| `PAYROLL_ERROR`          | 500  | A payroll module error occurred      |
| `PERIOD_NOT_FOUND`       | 404  | Payroll period not found             |
| `PERIOD_ALREADY_CLOSED`  | 409  | Payroll period already closed        |
| `EMPLOYEE_NOT_IN_PERIOD` | 404  | Employee not found in payroll period |
| `SALARY_NOT_CONFIGURED`  | 400  | Salary not configured for employee   |
| `TAX_CALCULATION_ERROR`  | 500  | Tax calculation failed               |

### Self-Service Module

| Error Code      | HTTP | Message                     |
| --------------- | ---- | --------------------------- |
| `ESS_ERROR`     | 500  | An ESS error occurred       |
| `ESS_FORBIDDEN` | 403  | Cannot access this resource |
| `ESS_NOT_FOUND` | 404  | Resource not found          |

---

## Module Structure (MANDATORY)

Every module in `src/modules/` MUST follow:

```
src/modules/<name>/
├── api/
│   ├── router.py          # FastAPI router with prefix /api/<name>
│   ├── schemas.py         # Pydantic request/response models
│   └── error_handler.py   # Domain exception → HTTPException mapping
├── application/
│   └── <name>_service.py  # Business logic (no framework deps)
├── domain/
│   ├── entities.py        # SQLModel table classes
│   ├── enums.py           # str Enums
│   └── exceptions.py      # Domain-specific exceptions (not HTTP)
├── infrastructure/
│   ├── config.py          # pydantic-settings with env prefix
│   └── <name>_repository.py  # Async DB operations
└── container.py           # FastAPI Depends() wiring
```

## Key Rules

1. **Async-first:** All DB operations use `AsyncSession`
2. **DI via container.py:** Never instantiate services in routers directly
3. **Domain exceptions:** Raise domain exceptions in services, map to HTTP in error_handler
4. **No raw SQL in services:** Use repository pattern
5. **Auth:** Import `get_current_user` from `src.modules.identity.container`
6. **Schemas:** Use Pydantic v2 models with `model_config = {"from_attributes": True}`
7. **Migrations:** One migration per table/change, numbered sequentially (001, 002...)

## Commands

```bash
# Run server
uvicorn src.main:app --reload --port 8000

# Migrations
alembic upgrade head
alembic revision --autogenerate -m "description"

# Lint & format
ruff check src/ tests/
ruff format src/ tests/

# Type check
mypy src/

# Test
pytest tests/
pytest tests/modules/payroll/ -q  # specific module
```

## Existing Modules

| Module       | Prefix                                           | Description                                 |
| ------------ | ------------------------------------------------ | ------------------------------------------- |
| identity     | /api/auth                                        | Local auth, JWT, roles, whitelist, audit    |
| employee     | /api/employees, /api/departments, /api/positions | CRUD, import, documents                     |
| gmail        | /api/gmail                                       | Gmail API integration                       |
| recruitment  | /api/recruitment                                 | Candidate pipeline, CV parsing              |
| attendance   | /api/attendance                                  | Check-in/out, leave, overtime, schedules    |
| payroll      | /api/payroll, /api/salary                        | Salary config, payslips, tax                |
| self_service | /api/ess                                         | Employee self-service portal                |

## Business Rules (Vietnamese HR)

- Personal tax deduction: 11,000,000 VND/month
- Dependent deduction: 4,400,000 VND/person/month
- Insurance (employee): BHXH 8% + BHYT 1.5% + BHTN 1% = 10.5%
- Insurance (employer): BHXH 17.5% + BHYT 3% + BHTN 1% = 21.5%
- Work days per month: 26 (for daily rate calculation)
- Progressive tax: 7 brackets (5%, 10%, 15%, 20%, 25%, 30%, 35%)
- OT rates: weekday 150%, weekend 200%, holiday 300%
