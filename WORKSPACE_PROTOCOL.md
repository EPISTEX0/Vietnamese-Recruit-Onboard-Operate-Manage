# Workspace Protocol

## Status
- owner: EPISTEX0 (ngocnt@aidia.vn)
- status: accepted
- version: 1
- last_reviewed: 2026-08-17
- approved: 2026-08-17 bởi owner
- applies_to: `/home/epistex/Projects/Vietnamese-Recruit-Onboard-Operate-Manage`
- readers: Lead; Supervisor khi được giao audit

> Section `Authority` **binding** kể từ 2026-08-17. Mọi rủi ro dưới đây đều dẫn ra artifact cụ thể;
> số đo ghi kèm ngày và commit. Đổi `Authority` cần owner duyệt lại.

## Project characteristics

- **criticality:** HRMS nội bộ — dữ liệu nhân sự, chấm công, bảng lương. Sai sót về ranh giới ngày
  hoặc quyền truy cập là sai về tiền lương và về quyền riêng tư, không chỉ về UI.
- **dominant risks:**
  - **Gate xanh cục bộ không đồng nghĩa CI xanh.** `node_modules` phẳng ở máy này cho phép import
    một transitive package; nó qua đủ 4 gate frontend cục bộ rồi chết riêng ở CI với `TS2307`.
    Import mới **phải** là direct dependency trong `frontend/package.json`.
  - **`ruff check` không kèm path lint cả `scripts/`,** thư mục CI không lint — sinh ~214 lỗi không
    liên quan. Lệnh CI là `ruff check src tests` (xem `.github/workflows/ci.yml` job `backend-quality`).
  - **`backend/.env` che root `.env`.** `find_dotenv()` dừng ở file đầu tiên tìm thấy, nên nếu
    `backend/.env` tồn tại nó thay thế root `.env` **toàn bộ**, không merge. Có tripwire test giữ chỗ này.
  - **Mọi lệnh `docker compose` cần root `.env` tồn tại trước,** kể cả `up postgres redis`: `env_file`
    được validate ở phạm vi project chứ không phải per-service.
  - **`docker-compose.yml:12` đặt `internal: true`.** postgres/redis/minio **không** tới được từ host
    bất kể khai `ports:` gì. Truy cập qua `docker compose exec`, đừng tin `localhost:5432` — cổng đó
    trên máy này thuộc một project khác.
  - **`vroom-backend` không bind-mount `src/`.** Container chạy code đã bake vào image; verify một bản
    sửa cần **rebuild**, `restart` không đổi gì.
  - **Image có thể được build từ branch khác.** Đã đo: image cũ mang revision `084` trùng số với một
    migration của `react-vite-migration`, khiến một migration bị bỏ qua im lặng sau `down -v`.
- **expensive-to-reverse decisions:**
  - **Alembic migration.** Head hiện tại `088_kb_embedding_hnsw_indexes` (đo 2026-08-17). Schema drift
    đã đóng về 0, nên **mọi** diff mà `alembic autogenerate` sinh ra bây giờ là tín hiệu thật, không
    phải nhiễu nền. CI job `schema-drift` gác chỗ này.
  - Đổi kiểu cột, đổi contract của public API, đổi ranh giới auth.
- **external side effects:** Gmail API (đọc và gửi thư thật), Google Calendar (tạo/sửa/xoá event thật),
  MinIO object storage. Một test chạy nhầm vào credential thật sẽ gửi thư thật.

## Authority

- **Lead may decide:** phân rã ticket, gộp/tách phạm vi, thứ tự hàng đợi, nội dung brief, chấp nhận
  hay trả lại candidate, mở GitHub issue, quyết định thiết kế trong phạm vi một ticket.
- **Human must decide:** thay đổi không đảo ngược được (migration đổi/xoá dữ liệu, đổi contract API
  công khai, đổi mô hình phân quyền), đổi múi giờ hiển thị, restart daemon Paseo, và **duyệt chính
  file này**.
- **prohibited without explicit authority:**
  - **Restart daemon Paseo** — giết mọi agent đang chạy.
  - **Mở lại quyết định múi giờ.** `APP_TIME_ZONE = 'Asia/Ho_Chi_Minh'` trong
    `frontend/i18n/request.ts` là hằng cố ý, owner đã chốt. `frontend/datetime-locale.test.ts` chốt
    cả tên zone; điều kiện kích hoạt việc mở lại nằm ở issue #345. **Không** đổi thành "giờ trình duyệt".
  - Chạy migration hoặc `down -v` trên dữ liệu không phải dữ liệu seed cục bộ.

## Ownership & workspaces

- **Một writer tại một thời điểm trên một working tree.** Repo này không dùng worktree: peer làm trên
  branch đang checkout. Hai writer cùng cây dùng chung index và `git status` — `git add`/commit của
  peer này nuốt thay đổi đang dở của peer kia, im lặng.
- Peer read-only (điều tra, đo đạc) chạy song song thoải mái.
- Lead cắt branch **trước** khi spawn peer; nhãn `branch` trên một peer là lịch sử, không phải hiện tại.
- Lead sở hữu integration. Peer handback candidate, Lead merge.

## Verification

### Lệnh gate — chạy **verbatim**, không biến thể

Một lần gọi gate sai lệch đã từng bịa ra 214 lỗi mà CI không hề thấy.

**Backend** (`cd backend`):
```
uv run --frozen --no-sync ruff check src tests
uv run --frozen --no-sync ruff format --check src tests
uv run --frozen --no-sync pytest
```

**Frontend** (`cd frontend`):
```
pnpm run lint
pnpm exec tsc --noEmit
pnpm test
pnpm run build
```

### Baseline đã đo — 2026-08-17 @ `c16f1b1`

| Gate | Kết quả |
|---|---|
| `ruff check src tests` | All checks passed! |
| `ruff format --check src tests` | 521 files already formatted |
| `pytest` | **2767 passed, 2 skipped** (180.53s) |
| `pnpm run lint` | 0 error, **2 warning** — `inbox/page.tsx:57`, `AiChat.tsx:547` |
| `pnpm exec tsc --noEmit` | sạch |
| `pnpm test` | **254 passed** (21 file) |
| `pnpm run build` | xanh |

Hai warning lint là baseline có chủ ý. **Warning thứ ba là hồi quy.**
`noUnusedLocals`/`noUnusedParameters` bật từ `40d6d5b` — một biến thừa làm gate đỏ.

Lưu ý: comment trong `.github/workflows/ci.yml:340-341` còn ghi "4 warnings, 55 tests passing".
Số đó đã rot; bảng trên là số đo thật. Comment không phải gate, nhưng đừng lấy nó làm chuẩn.

### CI

Tám job trong `.github/workflows/ci.yml`, **tất cả required trên `main`**: `smoke-import`,
`undefined-names`, `test-collection`, `repo-integrity`, `schema-drift`, `backend-quality`,
`backend-tests`, `frontend`.

Không còn `continue-on-error` nào có hiệu lực (8 lần xuất hiện trong file đều nằm trong comment giải
thích vì sao đã gỡ). Nên `gh run view --json` **đáng tin** — trước đây thì không.

Xanh vẫn không tự merge được: squash-only, cần 1 review, không được tự approve.
Đường merge thực tế: `gh pr merge --squash --admin --delete-branch`.

### Yêu cầu bằng chứng khi nhận candidate

- **Không nhận theo báo cáo.** Lead tự chạy lại mutation và known-positive của peer trước khi accept.
- **Guard mới phải được chứng minh là biết đỏ:** hoàn nguyên bản sửa → guard đỏ đúng chỗ; làm hỏng
  extractor → test chết to tiếng, **không** được xanh rỗng.
- **Đọc diff đối chiếu ticket.** Flow của peer không đọc ticket — việc đó là của Lead lúc acceptance.
- Peer chạy `/code-review` **trước** khi commit.

## Project-specific anti-patterns

### Số trong ticket/brief là **sàn**, không phải chỉ tiêu

- **signal:** một ticket hoặc brief nói "N chỗ" và peer đặt assertion của guard bằng đúng N.
- **evidence required:** census bằng AST (không phải `rg` theo tên), nêu **tính chất** chứ không nêu
  chuỗi, và chứng minh census trên một known-positive đã biết.
- **open question:** census có mù với biến thể nào (biến trung gian, `getattr`, viết xuống dòng)?
- **allowed response:** đo lại, đặt sàn **ngay dưới** số đo được, ghi xuất xứ ("tôi đếm N bằng
  phương pháp X"). **Census của peer bất đồng với Lead thì peer thắng.**

Đã xảy ra hai lần: brief #356 nói 4 map / 16 key, thực tế 6 map / 32 key. Ticket #359 nói 8 chỗ, AST
đo ra 12.

### `file:line` trong doc và ticket rữa im lặng

- **signal:** một citation trỏ đúng số dòng nhưng dòng đó đã là code khác.
- **evidence required:** `rg` lại và xác nhận citation trỏ vào **symbol còn sống**, không chỉ xác
  nhận dòng đó tồn tại.

### Test thay `get_db_session` che mất teardown

- **signal:** test dùng DI override thay `get_db_session` wholesale (đã đếm 10 file).
- **consequence:** commit sau `yield` của dependency thật **không bao giờ chạy** dưới test, nên một
  commit thất bại vẫn trả 200 cho client mà không test nào thấy.
- **allowed response:** test khẳng định hành vi phải ép qua seam thật; đừng chỉ mock lớp ngay dưới.

### Test cũ có thể đang giữ hành vi sai cho sống

Refactor `1dcb4a7` (tách `CandidateService`) **không** bảo toàn hành vi. Ở repo này, test đỏ sau khi
gỡ một coupling thường là **đúng**, không phải stale. Sửa thành khẳng định hành vi, đừng đổi tên mock.

### `log_audit` nuốt lỗi

Một `commit()` trần đặt sau `log_audit` biến một audit-failure vốn bị nuốt thành 500; bỏ nó đi thì
audit bị vứt. Cả hai đều sai — đọc contract trước khi sửa quanh chỗ này.

### zsh, không phải fish

Bash tool chạy **zsh non-interactive** (không đọc `.zshrc`). `$VAR` không word-split, và `$VAR:x` ăn
một history modifier — cả hai tự nguỵ trang thành "phát hiện về code". mise shims wire qua `~/.zshenv`;
nếu `uv`/`pnpm` báo not-found:

```
export PATH="$HOME/.local/share/mise/shims:$PATH"
```

Lệnh viết cho owner tự chạy phải là **cú pháp fish** (login shell của owner).

## Protocol evolution

- Supervisor ghi causal evidence vào notebook.
- Owner duyệt mọi thay đổi ở section `Authority`.
- Review lại sau mỗi pattern lặp lại hoặc mỗi thay đổi kiến trúc lớn; cập nhật bảng baseline mỗi khi
  một gate đổi số.
