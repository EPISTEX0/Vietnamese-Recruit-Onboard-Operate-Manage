# Báo cáo drift giữa model SQLModel và schema thật

**Ngày đo:** 2026-08-14 · **Alembic head:** `084` · **Phạm vi:** toàn bộ `backend/src/modules/*/domain/entities.py`

Tài liệu này chỉ **liệt kê và phân loại**. Không có migration nào được sinh ra, và không entity nào bị
sửa ngoài một FK trỏ sai tên bảng (mục [Điều kiện tiên quyết](#điều-kiện-tiên-quyết-một-fk-trỏ-sai-tên-bảng)).

---

## 1. Dành cho người đọc lần đầu

Dự án có hai mô tả về cùng một cơ sở dữ liệu, và chúng **không khớp nhau**:

| | Nguồn | Ai đọc nó |
|---|---|---|
| **Model** | các class SQLModel trong `backend/src/modules/*/domain/entities.py` | code ứng dụng lúc runtime |
| **Schema thật** | chuỗi migration trong `backend/alembic/versions/` | PostgreSQL |

Hai thứ này lẽ ra phải mô tả cùng một cái bảng. Khi lệch nhau thì gọi là **drift**.

Drift phần lớn thời gian vô hại — code vẫn chạy, vì PostgreSQL mới là bên quyết định, còn model chỉ là
cách Python nhìn vào đó. **Nguy hiểm chỉ xuất hiện khi ai đó chạy:**

```
alembic revision --autogenerate
```

Lệnh này so model với DB rồi **sinh migration để kéo DB về giống model**. Nếu model là bên sai, migration
sinh ra sẽ phá DB đang đúng. Nó không hỏi lại, và diff của nó trông rất giống một migration bình thường.

> **Tiền lệ có thật trong repo này.** `users.role` từng khai `String(10)` trong model, trong khi DB là
> `VARCHAR(20)`. Giá trị `'system_admin'` dài 12 ký tự. Migration `082` để lại cột `VARCHAR(10)` khiến role
> này *không ghi được* — first-run setup và mọi thao tác promote đều fail với `value too long`. Migration
> `084` phải sửa lại. Nếu lúc đó ai chạy autogenerate rồi apply, nó sẽ sinh đúng lệnh thu cột về 10 và
> **tái tạo lại nguyên con bug mà 084 vừa dập**.

Báo cáo này trả lời: *còn bao nhiêu quả mìn như vậy, và chúng nằm ở đâu.*

---

## 2. Điều kiện tiên quyết: một FK trỏ sai tên bảng

Trước ngày đo, **không ai đo được drift** vì phép so sánh chết ngay từ đầu:

```
sqlalchemy.exc.NoReferencedTableError: Foreign key associated with column
'outbound_emails.candidate_id' could not find table 'recruitment_candidates'
```

`OutboundEmail` khai `foreign_key="recruitment_candidates.id"`. Bảng tên đó **chưa từng tồn tại**.

Ba nguồn độc lập đều chỉ về cùng một kết luận — **DB đúng, model sai**:

| Bằng chứng | Kết quả |
|---|---|
| `information_schema.tables` | có `candidates`, **không** có `recruitment_candidates` |
| `Candidate.__tablename__` | `"candidates"` |
| Constraint thật trên DB | `outbound_emails_candidate_id_fkey` → `candidates.id` (tồn tại, đúng đích) |
| Migration `048` tạo bảng | `ForeignKeyConstraint(["candidate_id"], ["candidates.id"])` — đúng ngay từ đầu |

Vì constraint trên DB **có thật và trỏ đúng**, đây không phải trường hợp "constraint chưa từng được tạo".
Chỉ mỗi model gõ sai tên. Nên model được sửa cho khớp DB, và **không cần migration**.

Sau khi sửa, `alembic revision --autogenerate` chạy tới cùng. Đó là điều kiện để có mọi con số bên dưới.

---

## 3. Con số: 112 diff, không phải 105

Phiên đo trước báo **105**. Con số đo lại là **112 diff top-level (113 khi trải phẳng)**. Chênh lệch đã được
truy nguyên, không phải hồi quy:

- Phiên trước phải **gỡ hẳn bảng `outbound_emails` khỏi metadata** để né lỗi FK ở trên. Chạy lại đúng cách
  gỡ đó trên schema hôm nay ra **111 top-level** — tức bản thân việc gỡ bảng chỉ giải thích được 1 diff,
  không phải 7.
- **Sáu diff còn lại chưa quy được nguyên nhân chắc chắn.** Muốn biết chính xác thì phải đo lại tại đúng
  commit của phiên trước, mà mốc đó không được ghi lại. Nêu ra ở đây thay vì đoán bừa.

  Loại trừ được một khả năng: hai commit sửa enum (`users.role` và 9 cột enum còn lại, chuyển từ `str` sang
  `Enum`) **không phải nguồn**. Trong 113 diff hiện tại **không có diff nào liên quan tới enum** — các commit
  đó làm model khớp DB, tức chúng **giảm** drift chứ không tăng. Ứng viên khả dĩ hơn là
  `feat(auth): implement forgot/reset password flow (#291)`, commit tạo ra bảng `password_reset_tokens` —
  bảng này một mình đóng góp 3 diff P1 (§5.2.c).

Con số dùng cho toàn bộ báo cáo là **113 (trải phẳng)**, đo bằng `compare_metadata` với đúng metadata và
đúng tuỳ chọn mặc định mà `alembic/env.py` dùng, nên nó khớp với những gì autogenerate thật sự sinh ra.

Kiểm chứng phụ đáng ghi nhận: **DB dev hiện tại và schema dựng mới tinh bằng `alembic upgrade head` cho ra
cùng 113 diff giống hệt nhau.** Nghĩa là DB dev không bị ai sửa tay ngoài migration — chuỗi migration là
nguồn sự thật đáng tin.

---

## 4. Thang phân loại

Alembic phân loại theo *hình thức* diff (`modify_type`, `remove_index`, …). Cách đó vô dụng để xếp ưu tiên:
một `modify_type` thu hẹp cột có thể xoá dữ liệu, còn một `modify_type` khác chỉ đổi tên kiểu mà PostgreSQL
coi là như nhau.

Thang dưới đây xếp theo **hậu quả thật nếu ai đó lỡ chạy autogenerate rồi apply**:

| Mức | Định nghĩa | Hồi phục được không |
|---|---|---|
| **P0** | Mất dữ liệu vĩnh viễn | Không — chỉ còn đường restore backup |
| **P1** | Mất ràng buộc toàn vẹn, hoặc đổi ngữ nghĩa dữ liệu **âm thầm** | Có, nhưng dữ liệu hỏng sinh ra ở giữa thì không |
| **P2** | Migration **fail lúc apply** | Có — ồn ào, dễ thấy, dễ rollback |
| **P3** | Không mất dữ liệu, mất hiệu năng hoặc rewrite bảng nặng | Có |
| **P4** | Không đổi gì về mặt vật lý, hoặc chỉ đổi cách biểu diễn | — |

Ranh giới quan trọng nhất là **P1 với P2**. P2 nổ ngay và ai cũng biết. P1 apply thành công, không báo gì,
và hậu quả chỉ lộ ra hàng tuần sau — nên P1 **nguy hiểm hơn P2 trên thực tế**, dù nghe tên có vẻ ngược.

---

## 5. Kết quả

| Mức | Số diff | Nội dung |
|---|---:|---|
| **P0** | **9** | DROP 3 bảng (+6 index của chúng) |
| **P1** | **13** | 9 FK mất `ON DELETE CASCADE`, 1 mất UNIQUE, 3 cột mất timezone |
| **P2** | **3** | 1 `NOT NULL`, 1 UNIQUE mới, 1 FK mới |
| **P3** | **20** | 14 mất index hiệu năng, 6 rewrite JSON→JSONB |
| **P4** | **68** | Vô hại (chi tiết §5.5) |
| | **113** | |

**Số quả mìn = 25** (P0 + P1 + P2) — tức khoảng **22%** số diff sẽ gây hại nếu autogenerate được apply.

**Riêng loại "thu hẹp cột" giống hệt `users.role`: còn 0.** Đã quét toàn bộ 34 `modify_type`; chỉ đúng một
diff có mang độ dài ở cả hai phía là `email_messages.processing_status` (DB `VARCHAR(20)` → model
`VARCHAR(30)`), và đó là **nới rộng**, an toàn. Không còn quả mìn nào cùng cơ chế với sự cố `users.role`.

Tin xấu là các mìn còn lại **thuộc loại khác và phần lớn nặng hơn**.

### 5.1 P0 — 9 diff — DROP TABLE

**Model sai.** DB đúng tuyệt đối.

Autogenerate thấy 3 bảng có trong DB mà không có trong metadata, nên kết luận chúng "đã bị xoá khỏi model"
và sinh `op.drop_table()`:

| Bảng | Có model trong `src/`? | Dữ liệu trong DB dev |
|---|---|---|
| `employee_requests` | **Có** — `src/modules/employee_request/domain/entities.py:25` | **3 dòng** |
| `attendance_records` | **Có** — `src/modules/attendance/domain/entities.py:32` | 0 dòng |
| `gmail_label_mappings` | **Không có ở đâu cả** | 0 dòng |

Hai bảng đầu là **cùng một lỗi với FK ở §2, chỉ khác chỗ**: model tồn tại đầy đủ, chỉ là
`alembic/env.py` không import chúng. Danh sách import trong `env.py` được duy trì bằng tay, và nó đã tụt lại
so với code. Bảng nào không được import thì với autogenerate là bảng "cần xoá".

`gmail_label_mappings` là ca khác: không có model nào, ở bất kỳ đâu. Hoặc model đã bị xoá mà bảng không được
dọn, hoặc bảng chưa bao giờ có model. Cần chủ sở hữu quyết định giữ hay bỏ — **không tự suy diễn được**.

> `employee_requests` đang có dữ liệu thật trên DB dev. Trên production, đây là nhóm duy nhất mất dữ liệu
> không hồi phục.

### 5.2 P1 — 13 diff — hỏng âm thầm

Cả ba nhóm dưới đây: **model sai, DB đúng.**

#### a) 9 FK mất `ON DELETE CASCADE`

Autogenerate sinh ra một cặp drop + recreate cho mỗi FK. Kiểm tra kỹ thì cặp này **không phải no-op**: bản
trên DB có `ondelete='CASCADE'`, bản sinh từ model thì **không có gì cả** — tức về mặc định `NO ACTION`.

Bằng chứng, lấy `refresh_tokens.user_id` làm ví dụ:

| Nguồn | Nội dung |
|---|---|
| Migration `003` | `ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE")` |
| DB (`pg_constraint.confdeltype`) | `c` — tức CASCADE |
| Model (`identity/domain/entities.py:142`) | `Field(foreign_key="users.id", …)` — **không có `ondelete`** |

Nguyên nhân là hạn chế của SQLModel: `Field(foreign_key=…)` không có tham số `ondelete`. Muốn khai CASCADE
phải viết `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))`. Không ai làm, nên **cả 9 FK trong repo đều
thiếu** — đây là lỗi hệ thống, không phải 9 lần sơ suất riêng lẻ.

Các FK bị ảnh hưởng: `calendar_conflicts.candidate_id`, `employee_knowledge_base_chunks.document_id`,
`hr_knowledge_base_chunks.document_id`, `interview_participants.interview_id`, `interviews.candidate_id`,
`job_application_link_proposals.{target_job_application_id, recruitment_inbox_item_id}`,
`oauth_grants.user_id`, `refresh_tokens.user_id`.

Hậu quả sau khi apply: xoá một `User` sẽ **fail** vì còn `refresh_tokens` tham chiếu, thay vì dọn theo như
thiết kế. Xoá một `Interview` để lại `interview_participants` mồ côi. Luồng xoá cascade của KB chunk gãy.
Migration chạy xong sạch sẽ, không cảnh báo gì.

#### b) 1 UNIQUE bị mất trắng — `oauth_configs (provider, is_active)`

DB có `uq_oauth_config_provider_active`. Model không khai. Autogenerate xoá nó, và **không có index unique
nào thay thế** — đã kiểm tra `pg_index` trên bảng này, chỉ còn lại primary key.

Ràng buộc này đảm bảo *mỗi provider chỉ có một config đang active*. Mất nó, hệ thống có thể rơi vào trạng
thái hai OAuth config cùng active cho một provider, và bên chọn config sẽ lấy cái nào là không xác định.

> **Cẩn thận khi đọc output thô của alembic ở đây.** Có tổng cộng 6 `remove_constraint`, trông đều giống
> nhau. Nhưng 5 trong số đó (`email_messages.gmail_message_id`, `evaluation_sets.version`,
> `outbound_emails.idempotency_key`, `users.employee_id`, `whitelist_entries.value`) chỉ là **đổi cách biểu
> diễn**: UNIQUE constraint bị bỏ nhưng một unique *index* tương đương được thêm vào ngay trong cùng diff
> set, nên tính duy nhất vẫn được giữ. Đó là lý do chúng nằm ở P4. Chỉ `oauth_configs` là mất thật.
> Nếu xếp loại theo tên diff của alembic thì 6 cái này bị gộp chung và kết luận sẽ sai.

#### c) 3 cột mất timezone — `password_reset_tokens`

| Cột | DB | Model (`identity/domain/entities.py:360-362`) |
|---|---|---|
| `expires_at` | `timestamp with time zone` | `datetime` trần → `DATETIME` naive |
| `used_at` | `timestamp with time zone` | `datetime` trần |
| `created_at` | `timestamp with time zone` | `datetime` trần |

Các model khác trong repo khai đúng bằng `sa_column=Column(DateTime(timezone=True))`; ba field này thì không.

Apply sẽ chuyển `timestamptz` → `timestamp`, **vứt bỏ thông tin múi giờ** của dữ liệu đang có. Đây là bảng
token reset mật khẩu: `expires_at` bị dịch múi giờ nghĩa là token hết hạn sai thời điểm — sống lâu hơn thiết
kế (rủi ro bảo mật) hoặc chết sớm (hỏng tính năng). Server chạy UTC thì có thể không ai nhận ra cho tới khi
đổi hạ tầng.

Đây là nhóm mà mình xếp **nguy hiểm nhất trong toàn bộ P1**: hậu quả thầm lặng, nằm trên đường bảo mật, và
gần như không thể truy ngược sau khi dữ liệu đã bị chuyển đổi.

### 5.3 P2 — 3 diff — fail lúc apply

| Diff | Model hay DB sai? | Ghi chú |
|---|---|---|
| `organization_ai_configurations.api_key_enc` NULL → NOT NULL | **Chưa rõ** | Fail nếu có dòng nào đang NULL. Có thể model mới là bên đúng về mặt nghiệp vụ — cần owner xác nhận rồi backfill trước |
| `cv_documents.gmail_message_id` thêm UNIQUE | **Model sai (nhiều khả năng)** | Model khai `unique=True` (`recruitment/domain/entities.py:89`), DB để non-unique. Một email có thể mang **nhiều** file CV đính kèm — bảng `EmailAttachment` tồn tại chính vì lý do đó. Ép unique sẽ chặn ca đó |
| `assistant_chat_sessions.employee_id` thêm FK | **DB sai (nhiều khả năng)** | FK có trong model mà thiếu trên DB — dấu hiệu một migration quên tạo. Apply sẽ fail nếu đang có dòng mồ côi |

Nhóm này ồn ào nên ít nguy hiểm hơn P1, nhưng **cả 3 đều cần owner quyết**, không sửa máy móc được.

### 5.4 P3 — 20 diff — hiệu năng và rewrite

- **14 index hiệu năng bị mất.** DB có, model không khai. Gồm `ix_audit_logs_{action_type,created_at}`,
  `ix_employees_{department_id,position_id}`, `ix_payslips_employee_status`, `ix_job_openings_*`, nhóm KB
  (`ix_kb_*`, `ix_emp_kb_*`), `ix_whitelist_entries_type`. **DB đúng** — index được thêm có chủ đích qua
  migration, model chỉ đơn giản không mô tả chúng. Mất index không mất dữ liệu, nhưng `audit_logs` và
  `payslips` là bảng lớn dần theo thời gian, mất index ở đó là truy vấn tụt thành seq scan.
- **6 cột JSON → JSONB.** `cv_documents.{confirmed_fields, field_provenance}` và
  `recruitment_inbox_items.{attachments_metadata, evidence, source_hints, correction_history}`. Ở đây
  **model đúng** — JSONB là kiểu nên dùng. Nhưng chuyển kiểu là **rewrite toàn bảng**, khoá bảng theo kích
  thước dữ liệu. Không mất dữ liệu; xếp P3 vì chi phí vận hành, không vì rủi ro.

### 5.5 P4 — 68 diff — vô hại

Ghi lại để không ai phải điều tra lại nhóm này:

- **24 `TEXT` → `VARCHAR`.** SQLModel render `str` thành `AutoString`, ra `VARCHAR` **không có độ dài**. Trong
  PostgreSQL, `VARCHAR` không độ dài là **không giới hạn, tương đương `TEXT`**. Đã kiểm chứng thực nghiệm:
  ghi chuỗi 100 000 ký tự vào cột `TEXT`, chạy `ALTER COLUMN TYPE varchar`, đọc lại vẫn đủ 100 000 ký tự.
  **Không cắt cụt.** Đây là nhóm dễ bị báo động nhầm nhất — nhìn thì giống hệt lỗi `users.role`, nhưng khác
  hẳn về bản chất, vì `users.role` có **độ dài cụ thể** còn nhóm này thì không.
- **9 FK add** — vế "recreate" của 9 cặp drop+recreate đã tính ở P1.a. Đếm riêng ở đây để tổng khớp 113;
  tác hại thật đã tính một lần ở P1 rồi.
- **19 diff đổi cách biểu diễn UNIQUE** — unique index ↔ unique constraint, tính duy nhất giữ nguyên
  (xem cảnh báo ở §5.2.b).
- **6 diff đổi tên index** — `ix_link_proposals_*` → `ix_job_application_link_proposals_*`, cùng cột.
- **5 comment** trên bảng/cột.
- **4 index thêm mới** trên `interviews` / `interview_participants` — có ích, không hại.
- **1 nới rộng cột** — `email_messages.processing_status` `VARCHAR(20)` → `VARCHAR(30)`. Nới rộng an toàn.
  Model nhiều khả năng đúng: có giá trị status đang dài quá 20 ký tự thì đây chính là `users.role` tập hai,
  chỉ khác là lần này model đã đi trước. **Đáng kiểm tra riêng.**

---

## 6. Ước lượng khối lượng dọn

Ước lượng cho một người đã quen repo. Đã tách theo nhóm để có thể mở phiên riêng từng phần.

| Nhóm | Việc phải làm | Ước lượng | Chặn bởi |
|---|---|---:|---|
| **P0** | Thêm 2 model thiếu vào `env.py`; quyết số phận `gmail_label_mappings` | 0.5–1 ngày | Cần owner quyết bảng thứ 3 |
| **P1.a** | 9 FK → `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))` | 1 ngày | Không |
| **P1.b** | 1 `__table_args__` UNIQUE cho `oauth_configs` | 0.5 giờ | Không |
| **P1.c** | 3 field → `Column(DateTime(timezone=True))` | 0.5 giờ | Không |
| **P2** | 3 quyết định nghiệp vụ + backfill nếu cần | 0.5 ngày | **Cần owner cả 3** |
| **P3** | Khai 14 index vào model; xác nhận 6 JSONB | 1 ngày | Không |
| **P4** | 24 cột → `Column(Text)`; đồng bộ tên index/constraint | 1–1.5 ngày | Không |
| | **Tổng** | **4–6 ngày** | |

**Thứ tự đề xuất nếu chỉ làm được một phần:** `P1.c` → `P1.b` → `P0` → `P1.a`. P1.c và P1.b cộng lại đúng
một giờ và gỡ được hai mối nguy âm thầm nhất. P0 tuy nặng nhất về hậu quả nhưng hai trong ba bảng chỉ cần
thêm dòng import.

**P4 tuy vô hại nhưng nên dọn cuối cùng** vì nó chiếm 60% số diff. Còn 68 dòng nhiễu thì không ai đọc nổi
output autogenerate để phát hiện mìn thật mới xuất hiện.

---

## 7. Khuyến nghị vận hành

Cho tới khi P0–P2 được dọn:

> **Không chạy `alembic revision --autogenerate` rồi apply thẳng trên repo này.**
> Viết migration bằng tay, hoặc nếu dùng autogenerate thì phải đọc lại từng dòng và đối chiếu với §5.

Có thể cân nhắc thêm một CI gate chặn merge nếu số diff vượt ngưỡng hiện tại — biến 113 thành trần
không được phép tăng. Ngoài phạm vi ticket này, nêu ra để owner cân nhắc.

---

## 8. Cách đo lại

```bash
cd backend

# 1. Dựng schema sạch từ migration (đừng đo trên DB dev nếu chưa chắc nó khớp head)
createdb drift_audit
DATABASE_URL='postgresql+asyncpg://<user>:<pass>@<host>:5432/drift_audit' \
  .venv/bin/alembic upgrade head

# 2. So sánh. Dùng đúng danh sách import và tuỳ chọn mặc định của alembic/env.py,
#    nếu không con số sẽ không khớp với autogenerate thật.
#    compare_metadata(MigrationContext.configure(conn), SQLModel.metadata)
```

Lưu ý khi đo: các diff `modify_*` trả về dưới dạng **list lồng trong list**, nên `len()` trên kết quả thô
cho ra số top-level (112) chứ không phải số diff thật (113). Phải trải phẳng trước khi đếm.

---

## 9. Nguồn tham chiếu

- `backend/alembic/versions/048_create_outbound_emails_table.py` — FK gốc, đúng ngay từ đầu
- `backend/alembic/versions/003_create_refresh_tokens_table.py` — `ondelete="CASCADE"` gốc
- `backend/alembic/versions/084_*.py` — sự cố `users.role`, tiền lệ của cả báo cáo này
- `backend/alembic/env.py` — danh sách import quyết định bảng nào "tồn tại" với autogenerate
