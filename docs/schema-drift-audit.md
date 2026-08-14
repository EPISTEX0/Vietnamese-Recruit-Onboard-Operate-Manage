# Báo cáo drift giữa model SQLModel và schema thật

**Đo lần đầu:** 2026-08-14 (head `084`, **113 diff**) · **Đo lại:** 2026-08-14 sau khi dọn
(head `085`, **64 diff**) · **Phạm vi:** toàn bộ `backend/src/modules/*/domain/entities.py`

> ## Trạng thái: P0, P1, P2 đã đóng. P3 còn 6 diff JSONB. Còn P4.
>
> | Mức | Lúc báo cáo | Hiện tại | |
> |---|---:|---:|---|
> | **P0** | 9 | **0** | ✅ đóng |
> | **P1** | 13 | **0** | ✅ đóng (+1 diff mới lộ ra cũng đã đóng) |
> | **P2** | 3 | **0** | ✅ đóng |
> | **P3** | 20 | 6 | 14 index đã khai vào model; còn 6 `JSON → JSONB` |
> | **P4** | 68 | 58 | chưa làm |
> | | **113** | **64** | |
>
> **Số quả mìn còn lại: 0.** 64 diff còn lại đều không gây mất dữ liệu và không mất ràng buộc toàn vẹn —
> chứng minh ở [§5.4](#54-p3--20-diff--6--hiệu-năng-và-rewrite) và [§5.5](#55-p4--58-diff--vô-hại).
>
> Khuyến nghị "không chạy autogenerate rồi apply thẳng" ở [§7](#7-khuyến-nghị-vận-hành) **vẫn còn hiệu lực**,
> chỉ là lý do đã đổi: giờ là vì nhiễu, không còn vì nguy hiểm.

Bản gốc của tài liệu này chỉ **liệt kê và phân loại**, không sửa gì ngoài một FK trỏ sai tên bảng
(mục [Điều kiện tiên quyết](#điều-kiện-tiên-quyết-một-fk-trỏ-sai-tên-bảng)). Các mục §5.1–§5.3 bên dưới
giữ nguyên phần mô tả gốc và bổ sung phần **đã xử lý thế nào** — cố ý không xoá phần chẩn đoán, vì
lập luận mới là thứ đáng đọc lại chứ không phải kết quả.

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

| Mức | Số diff | Nội dung | Trạng thái |
|---|---:|---|---|
| **P0** | **9** | DROP 3 bảng (+6 index của chúng) | ✅ đóng |
| **P1** | **13** (+1) | 9 FK mất `ON DELETE CASCADE`, 1 mất UNIQUE, 3 cột mất timezone | ✅ đóng |
| **P2** | **3** | 1 `NOT NULL`, 1 UNIQUE mới, 1 FK mới | ✅ đóng |
| **P3** | **20** → 6 | ~~14 mất index hiệu năng~~ ✅, 6 rewrite JSON→JSONB còn | một phần |
| **P4** | **68** → 58 | Vô hại (chi tiết §5.5) | còn |
| | **113** → **64** | | |

**Số quả mìn = 25** (P0 + P1 + P2) — tức khoảng **22%** số diff sẽ gây hại nếu autogenerate được apply.
**Hiện tại con số đó là 0.**

> **Dọn P0 làm lộ thêm một quả mìn P1 mà báo cáo này không đếm được.** `attendance_records` vô hình với
> autogenerate nên alembic chỉ nói đúng một câu "drop cả bảng" và không hề so từng cột. Import bảng vào
> metadata xong thì diff thứ 114 hiện ra: `uq_attendance_employee_date` — UNIQUE `(employee_id, work_date)`
> có trên DB, model không khai, **và không có gì thay thế**. Cùng hạng với `oauth_configs` ở §5.2.b. Đã đóng
> luôn cùng đợt.
>
> Bài học đáng ghi: **số diff của một bảng bị `drop_table` che là không tin được** — nó luôn bằng 1 + số
> index, bất kể bên trong lệch bao nhiêu. Nếu sau này còn bảng nào rơi vào diện đó, đừng coi con số của nó
> là đã khảo sát xong.

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

#### ✅ Đã xử lý — nguyên nhân gốc, không chỉ triệu chứng

Sửa hai dòng import thì hết P0, nhưng lần sau vẫn tụt lại y như vậy. Nên thay hẳn cơ chế:

`backend/src/shared/model_registry.py` quét AST toàn bộ `src/` tìm class `table=True` rồi import đúng những
module đó; `alembic/env.py` gọi `import_all_entity_modules()` thay cho danh sách tay. Quét **tĩnh** chứ không
import dò, vì module đáng lo nhất chính là module chưa ai import.

`backend/tests/test_alembic_metadata_complete.py` chốt bất biến. Test chạy trong **tiến trình con** — chi tiết
này không phải cho đẹp: `SQLModel.metadata` là global, chạy chung suite thì các test khác đã import hộ entity,
khoảng trống tự lấp và assert sẽ xanh oan trong khi autogenerate vẫn hỏng. Trước khi sửa, test đỏ và gọi đúng
tên `['attendance_records', 'employee_requests']`.

**`gmail_label_mappings`: loại khỏi phạm vi so sánh, không drop.** Bằng chứng truy được:

| Câu hỏi | Trả lời |
|---|---|
| Bảng từ đâu ra | migration `008_create_gmail_tables.py` (commit `79b73cd`, 20/05/2026) |
| Model từng tồn tại chưa | **Có** — `GmailLabelMapping` trong `gmail/domain/entities.py`, kèm `label_repository.py` và `label_service.py` |
| Mất khi nào | commit `76e9143` (12/07/2026) xoá trọn slice: model, repository, service 352 dòng, test 514 dòng, route, và dòng import trong `env.py`. **Không migration nào dọn bảng.** |
| Còn code nào đọc không | Không — `rg` toàn repo chỉ ra tài liệu này |
| Dữ liệu trên dev | 0 dòng |

Feature bị xoá có chủ đích, nên bảng đúng là rác. Nhưng **không drop**, vì "0 dòng trên dev" không phải bằng
chứng về production: DB dev hiện trống ở gần như mọi bảng (`cv_documents`, `assistant_chat_sessions`,
`organization_ai_configurations` đều 0 dòng), trong khi feature label chạy thật từ 05/2026 đến 07/2026 nên DB
triển khai có thể còn dữ liệu. Drop thì không hồi phục; loại khỏi so sánh thì không mất gì.

Cách làm: `include_object` trong `alembic/env.py` với tập `UNMANAGED_TABLES` kèm ghi chú lý do.
**Việc còn lại cho owner:** xác nhận bảng trên DB triển khai cũng trống, rồi mới viết migration drop và bỏ
entry khỏi `UNMANAGED_TABLES`.

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

> **Đính chính:** hạn chế đó đã hết. SQLModel trong repo là **0.0.38**, và `Field()` có sẵn tham số
> `ondelete`. Không cần dựng `sa_column` — giữ nguyên `Field` thì `index`/`nullable`/kiểu cột không phải
> khai lại bằng tay, đỡ một nguồn drift mới.

Các FK bị ảnh hưởng: `calendar_conflicts.candidate_id`, `employee_knowledge_base_chunks.document_id`,
`hr_knowledge_base_chunks.document_id`, `interview_participants.interview_id`, `interviews.candidate_id`,
`job_application_link_proposals.{target_job_application_id, recruitment_inbox_item_id}`,
`oauth_grants.user_id`, `refresh_tokens.user_id`.

Hậu quả sau khi apply: xoá một `User` sẽ **fail** vì còn `refresh_tokens` tham chiếu, thay vì dọn theo như
thiết kế. Xoá một `Interview` để lại `interview_participants` mồ côi. Luồng xoá cascade của KB chunk gãy.
Migration chạy xong sạch sẽ, không cảnh báo gì.

##### ✅ Đã xử lý

Cả 9 khai `Field(..., ondelete="CASCADE")`. Đã đối chiếu `pg_constraint.confdeltype` **từng cái một** thay vì
giả định cả 9 giống nhau — và hoá ra không giống: `calendar_conflicts` có hai FK cạnh nhau,
`candidate_id` là `'c'` (CASCADE) còn `interview_id` là `'a'` (NO ACTION). Chỉ sửa cái đầu, và để lại comment
ngay tại chỗ để lần sau không ai "dọn cho đối xứng".

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
>
> *Kiểm chứng lại từng cái khi dọn — kết luận đúng, nhưng `outbound_emails` đúng vì lý do khác với 4 cái kia.*
> *Bốn cái kia có `add_index … unique=True` bù ngay trong cùng diff set. `outbound_emails` thì không có dòng*
> *`add_index` nào; nó an toàn vì trên DB **đã sẵn** cả unique index `ix_outbound_emails_idempotency_key`*
> *lẫn unique constraint `uq_outbound_emails_idempotency_key` trên cùng cột, nên bỏ cái constraint thừa vẫn*
> *còn index giữ tính duy nhất.*

##### ✅ Đã xử lý

`__table_args__` của `OAuthConfig` khai lại `UniqueConstraint("provider", "is_active",
name="uq_oauth_config_provider_active")`.

Cùng đợt đóng luôn `uq_attendance_employee_date` — quả mìn P1 lộ ra sau khi sửa P0 (xem hộp cảnh báo ở §5).
Chỗ đó `__table_args__` của `AttendanceRecord` là **tuple rỗng chỉ chứa hai dòng comment mô tả ràng buộc**;
constraint thật chỉ tồn tại trong migration. Mất nó nghĩa là một nhân viên chấm công được nhiều bản ghi
trong cùng một ngày.

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

##### ✅ Đã xử lý

Cả ba khai `sa_column=Column(DateTime(timezone=True))` đúng như các model khác trong repo vẫn làm.

### 5.3 P2 — 3 diff — fail lúc apply

| Diff | Model hay DB sai? | Ghi chú |
|---|---|---|
| `organization_ai_configurations.api_key_enc` NULL → NOT NULL | **Chưa rõ** | Fail nếu có dòng nào đang NULL. Có thể model mới là bên đúng về mặt nghiệp vụ — cần owner xác nhận rồi backfill trước |
| `cv_documents.gmail_message_id` thêm UNIQUE | **Model sai (nhiều khả năng)** | Model khai `unique=True` (`recruitment/domain/entities.py:89`), DB để non-unique. Một email có thể mang **nhiều** file CV đính kèm — bảng `EmailAttachment` tồn tại chính vì lý do đó. Ép unique sẽ chặn ca đó |
| `assistant_chat_sessions.employee_id` thêm FK | **DB sai (nhiều khả năng)** | FK có trong model mà thiếu trên DB — dấu hiệu một migration quên tạo. Apply sẽ fail nếu đang có dòng mồ côi |

Nhóm này ồn ào nên ít nguy hiểm hơn P1, nhưng **cả 3 đều cần owner quyết**, không sửa máy móc được.

#### ✅ Đã xử lý — owner duyệt, ba quyết định đi ba hướng khác nhau

Lưu ý chung: **dữ liệu trên DB dev không giúp được gì ở đây** — cả ba bảng đều 0 dòng. Nên căn cứ là code và
lịch sử migration, không phải `count(*)`.

**1. `cv_documents.gmail_message_id` — model sai, bỏ `unique=True`.**
Lập luận của báo cáo kiểm chứng được bằng code chứ không chỉ bằng suy đoán:
`CVProcessor.process_cv_from_email()` lặp qua `attachments`, gọi `process_single_attachment()` cho từng cái,
và mỗi lần ghi một `CVDocument` với **cùng một** `gmail_message_id`. Kiểu trả về nó khai là
`list[CVDocument]`, và `CVDocumentRepository.get_by_gmail_message_id()` cũng trả về list. Ép UNIQUE là chặn
thẳng email hai CV ngay từ đính kèm thứ hai. Giữ `unique=True` ở `RecruitmentInboxItem.gmail_message_id` —
chỗ đó một email đúng là một item, và repo của nó trả về một object.

**2. `organization_ai_configurations.api_key_enc` — DB sai, siết NOT NULL (migration 085).**
`056` tạo cột NOT NULL; `057` nới ra nullable **trong lúc** thêm `server_default ''`. Ý định ở `057` là
"mặc định rỗng", còn bỏ NOT NULL chỉ là đi kèm — bằng chứng là chính `downgrade()` của `057` khôi phục
NOT NULL, tức nó chưa bao giờ coi nullable là trạng thái đúng. Về nghiệp vụ, config **được phép** tồn tại
trước khi có key (tổ chức còn dùng deployment key), nhưng trạng thái đó viết là `''` chứ không phải NULL:
service ghi `config.api_key_enc = ""`, và mọi chỗ đọc đều kiểm tra falsy (`if not config.api_key_enc`) nên
`''` với NULL đã đồng nghĩa với ứng dụng. NOT NULL chỉ bỏ đi cách viết thứ hai của cùng một ý.

**3. `assistant_chat_sessions.employee_id` — DB sai, thêm FK (migration 085).**
`075` tạo bảng với FK trên `user_id` và index — nhưng không FK — trên `employee_id`, trong khi model khai
`foreign_key="employees.id"` ngay từ đầu. Là quên, không phải quyết định: không bảng nào khác trong schema
tham chiếu `employees` mà không có FK.

**Migration `085` idempotent hai chiều.** Backfill `NULL → ''` chạy *trước* khi siết NOT NULL, nên DB nào
đang có NULL thì được sửa chứ không bị từ chối. Dòng mồ côi thì **set NULL chứ không xoá** — session là lịch
sử hội thoại thật, và cột vốn nullable theo thiết kế. Tạo FK có kiểm tra `pg_constraint` trước.
`downgrade()` viết đủ cả hai chiều — không bỏ mặc như `082`. Đã chạy thật:
`upgrade head` → `downgrade -1` → `upgrade head` sạch.

### 5.4 P3 — 20 diff → 6 — hiệu năng và rewrite

- ~~**14 index hiệu năng bị mất.**~~ **✅ Đã xử lý.** DB có, model không khai. **DB đúng** — index được
  thêm có chủ đích qua migration, model chỉ đơn giản không mô tả chúng. Mất index không mất dữ liệu, nhưng
  `audit_logs` và `payslips` là bảng lớn dần theo thời gian, mất index ở đó là truy vấn tụt thành seq scan.
- **6 cột JSON → JSONB.** `cv_documents.{confirmed_fields, field_provenance}` và
  `recruitment_inbox_items.{attachments_metadata, evidence, source_hints, correction_history}`. Ở đây
  **model đúng** — JSONB là kiểu nên dùng. Nhưng chuyển kiểu là **rewrite toàn bảng**, khoá bảng theo kích
  thước dữ liệu. Không mất dữ liệu; xếp P3 vì chi phí vận hành, không vì rủi ro.

#### ✅ Đã xử lý — 14 index, sửa model chứ không thêm migration

Danh sách được xác minh lại từ `compare_metadata` chứ không lấy từ bản mô tả trên, và nó khớp: đúng 14
`remove_index`. Định nghĩa thật đọc từ `pg_indexes` cho từng cái — **cả 14 đều là btree, non-unique, không
partial**. Toàn schema có **0 index non-btree và 0 index partial** (kiểm bằng `pg_am` / `pg_index.indpred`),
nên lo ngại "nhóm KB có index pgvector không diễn đạt nổi" **không xảy ra**: không có index vector nào tồn
tại để mà khai. Không có index nào phải bỏ lại vì SQLAlchemy không mô tả được.

| Index | Bảng | Định nghĩa thật trên DB | Khai lại bằng |
|---|---|---|---|
| `ix_audit_logs_action_type` | `audit_logs` | btree `(action_type)` | `Column(..., index=True)` |
| `ix_audit_logs_created_at` | `audit_logs` | btree `(created_at)` | `Column(..., index=True)` |
| `ix_employees_department_id` | `employees` | btree `(department_id)` | `Field(index=True)` |
| `ix_employees_position_id` | `employees` | btree `(position_id)` | `Field(index=True)` |
| `ix_job_openings_created_at` | `job_openings` | btree `(created_at)` | `Column(..., index=True)` |
| `ix_job_openings_id` | `job_openings` | btree `(id)` | `Field(index=True)` |
| `ix_payslips_employee_status` | `payslips` | btree `(employee_id, status)` | `Index()` trong `__table_args__` |
| `ix_whitelist_entries_type` | `whitelist_entries` | btree `(entry_type)` | `Index()` trong `__table_args__` |
| `ix_kb_documents_kb_type` | `hr_knowledge_base_documents` | btree `(kb_type)` | `Index()` trong `__table_args__` |
| `ix_kb_documents_status` | `hr_knowledge_base_documents` | btree `(status)` | `Index()` trong `__table_args__` |
| `ix_kb_chunks_document_id` | `hr_knowledge_base_chunks` | btree `(document_id)` | `Index()` trong `__table_args__` |
| `ix_emp_kb_documents_kb_type` | `employee_knowledge_base_documents` | btree `(kb_type)` | `Index()` trong `__table_args__` |
| `ix_emp_kb_documents_status` | `employee_knowledge_base_documents` | btree `(status)` | `Index()` trong `__table_args__` |
| `ix_emp_kb_chunks_document_id` | `employee_knowledge_base_chunks` | btree `(document_id)` | `Index()` trong `__table_args__` |

**Vì sao ba cách khai khác nhau, chứ không phải một.** Cả ba đều phải ra đúng cái tên đang có trên DB —
tên lệch thì autogenerate đề nghị drop cái cũ rồi tạo cái mới, tức là vẫn hỏng, chỉ ồn ào hơn.

- `Field(index=True)` chỉ dùng được khi tên mặc định (`ix_<bảng>_<cột>`) **trùng đúng** tên thật. Đúng với
  6 cái đầu.
- `Column(..., index=True)` cho các field có `sa_column`: **`Field(index=True)` bị bỏ qua khi có
  `sa_column`** — khai ở đó trông đúng nhưng không sinh index nào. Đây là cái bẫy dễ dính nhất trong nhóm.
- `Index()` trong `__table_args__` cho 8 cái còn lại, vì tên thật **không** theo quy ước mặc định:
  `ix_whitelist_entries_type` (cột là `entry_type`), nhóm KB (`ix_kb_*` có từ khi tên bảng khác), và
  `ix_payslips_employee_status` là **composite** nên không có dạng field nào diễn đạt được.

**Thứ tự cột của composite được giữ nguyên chủ ý.** `ix_payslips_employee_status` là `(employee_id, status)`.
Đảo thành `(status, employee_id)` vẫn là index hợp lệ, vẫn trả đúng dữ liệu, nhưng vô dụng cho truy vấn
"payslip đã publish của nhân viên này" — tức đúng cái nó sinh ra để phục vụ. `compare_metadata` có so thứ tự
cột, nên việc diff về 0 cũng chính là bằng chứng thứ tự khớp.

**`ix_job_openings_id` là index thừa** — nó lặp lại index mà chính primary key đã có. Nhưng `035` tạo nó và
DB đang có nó, nên model khai lại cho khớp. Bỏ nó đi là một quyết định riêng, cần migration đứng sau, không
phải thứ mà một sửa đổi model làm bằng cách im lặng.

**Không có migration nào được thêm.** DB đang đúng; chỉ model được sửa. Cùng khuôn với P1
(`10b5f3f` / `91e0f96` / `bb632d4`).

**P3 (index): 14 diff → 0. Tổng 78 → 64.**

### 5.5 P4 — 58 diff — vô hại

Ghi lại để không ai phải điều tra lại nhóm này. Con số tụt từ 68 xuống 58 vì **9 `add_fk`** (vế "recreate"
của các cặp ở P1.a) và **1 diff UNIQUE** biến mất theo khi P1 được đóng — không phải vì có gì mới được dọn ở
đây.

- **24 `TEXT` → `VARCHAR`.** SQLModel render `str` thành `AutoString`, ra `VARCHAR` **không có độ dài**. Trong
  PostgreSQL, `VARCHAR` không độ dài là **không giới hạn, tương đương `TEXT`**. Đã kiểm chứng thực nghiệm:
  ghi chuỗi 100 000 ký tự vào cột `TEXT`, chạy `ALTER COLUMN TYPE varchar`, đọc lại vẫn đủ 100 000 ký tự.
  **Không cắt cụt.** Đây là nhóm dễ bị báo động nhầm nhất — nhìn thì giống hệt lỗi `users.role`, nhưng khác
  hẳn về bản chất, vì `users.role` có **độ dài cụ thể** còn nhóm này thì không.
- ~~**9 FK add**~~ — vế "recreate" của 9 cặp drop+recreate đã tính ở P1.a. **Hết cùng P1.a.**
- **18 diff đổi cách biểu diễn UNIQUE** — unique index ↔ unique constraint, tính duy nhất giữ nguyên
  (xem cảnh báo ở §5.2.b). Trước là 19; `oauth_configs` tách khỏi nhóm này khi được xác định là mất thật.
  Phân rã đầy đủ: `email_messages.gmail_message_id`, `evaluation_sets.version`, `whitelist_entries.value`
  mỗi cái 3 diff (`remove_index` non-unique + `add_index` unique + `remove_constraint`);
  `users.employee_id` 2; `outbound_emails.idempotency_key` 1; `departments.name`,
  `employees.employee_code`, `positions.name` mỗi cái 2 (`remove_index` unique + `add_constraint`).
- **6 diff đổi tên index** — `ix_link_proposals_*` → `ix_job_application_link_proposals_*`, cùng cột.
- **5 comment** trên bảng/cột.
- **4 index thêm mới** trên `interviews` / `interview_participants` — có ích, không hại.
- **1 nới rộng cột** — `email_messages.processing_status` `VARCHAR(20)` → `VARCHAR(30)`. Nới rộng an toàn.
  Model nhiều khả năng đúng: có giá trị status đang dài quá 20 ký tự thì đây chính là `users.role` tập hai,
  chỉ khác là lần này model đã đi trước. **Đáng kiểm tra riêng.**

---

## 6. Ước lượng khối lượng dọn

Ước lượng cho một người đã quen repo. Đã tách theo nhóm để có thể mở phiên riêng từng phần.

| Nhóm | Việc phải làm | Ước lượng | Trạng thái |
|---|---|---:|---|
| **P0** | Thêm 2 model thiếu vào `env.py`; quyết số phận `gmail_label_mappings` | 0.5–1 ngày | ✅ xong |
| **P1.a** | 9 FK → `ondelete="CASCADE"` | 1 ngày | ✅ xong |
| **P1.b** | `__table_args__` UNIQUE cho `oauth_configs` (+`attendance_records`) | 0.5 giờ | ✅ xong |
| **P1.c** | 3 field → `Column(DateTime(timezone=True))` | 0.5 giờ | ✅ xong |
| **P2** | 3 quyết định nghiệp vụ + backfill nếu cần | 0.5 ngày | ✅ xong (migration `085`) |
| **P3** (index) | Khai 14 index vào model | 1 ngày | ✅ xong |
| **P3** (JSONB) | Migration đổi kiểu 6 cột `JSON → JSONB` | 0.5 ngày | còn |
| **P4** | 24 cột → `Column(Text)`; đồng bộ tên index/constraint | 1–1.5 ngày | còn |
| | **Còn lại** | **1.5–2 ngày** | |

**P4 tuy vô hại nhưng nên dọn cuối cùng** vì nó chiếm 91% số diff còn lại. Còn 58 dòng nhiễu thì không ai
đọc nổi output autogenerate để phát hiện mìn thật mới xuất hiện.

Lưu ý cho ai làm nốt P3: **6 diff JSON→JSONB là bên model đúng**, nên dọn chúng nghĩa là viết migration đổi
kiểu cột thật — rewrite toàn bảng, khoá theo kích thước dữ liệu — chứ không phải sửa model. Đây là nhóm P3
**duy nhất còn lại**; 14 index đã đi hướng ngược lại và chỉ cần khai vào model.

---

## 7. Khuyến nghị vận hành

> **Không chạy `alembic revision --autogenerate` rồi apply thẳng trên repo này.**
> Viết migration bằng tay, hoặc nếu dùng autogenerate thì phải đọc lại từng dòng và đối chiếu với §5.

**Khuyến nghị này giữ nguyên, nhưng lý do đã đổi.** Trước đây apply thẳng là mất dữ liệu; giờ 64 diff còn lại
không cái nào làm mất dữ liệu hay mất ràng buộc toàn vẹn. Rủi ro bây giờ là **nhiễu**: một migration
autogenerate vẫn kéo theo 6 diff P3 rewrite toàn bảng, và giữa 64 dòng đó thì một quả mìn mới xuất hiện rất
dễ trôi qua mắt reviewer.

Đã có sẵn một phần hàng rào: `tests/test_alembic_metadata_complete.py` chặn đúng cơ chế đã gây ra P0 — bảng
có model mà không ai import. Nó không bắt được các loại drift khác.

> **Cập nhật:** hàng rào này **đã dựng** — `backend/tests/test_schema_drift_ceiling.py`, chạy trong suite.
> Hai đoạn dưới đây giữ nguyên vì lập luận vẫn đúng; chỉ phần "chưa có" là hết hạn.

Vẫn nên cân nhắc CI gate chặn merge nếu số diff vượt ngưỡng — biến **64** thành trần không được phép tăng.
Ngoài phạm vi ticket này, nêu ra để owner cân nhắc.

**Nhóm 14 index vừa đóng đang không có test nào giữ.** Xoá một dòng `index=True` thì suite vẫn xanh, và diff
sẽ lặng lẽ quay lại 65 — đúng cái cơ chế đã tạo ra chính nhóm này. Ticket giới hạn phạm vi sửa trong
`entities.py` + `docs/` nên không thêm test được trong đợt này. Cái trần diff ở trên chính là hàng rào rẻ
nhất phủ được cả nhóm này lẫn mọi drift mới, và nên làm trước khi P4 được dọn — sau đó ngưỡng sẽ về gần 0
và mọi thay đổi ngoài ý muốn đều lộ ngay.

### 7.1. Hàng rào đã dựng: tập diff được chấp nhận, không phải con số

Đề xuất ở trên là một **con số trần**. Cái đã làm là một **tập fingerprint**:
`backend/tests/schema_drift_baseline.txt` liệt kê từng diff trong 64 diff dưới dạng
`operation table object`. Cùng chi phí chạy, nhưng con số không nói được điều gì cả — "65 > 64" bắt người
đọc tự dựng lại harness mới biết cái gì vừa trôi — còn tập thì gọi đúng tên. Và con số đỏ y hệt nhau khi ai
đó **dọn** drift, thứ mà cả tài liệu này đang cố khuyến khích.

Nên hai chiều được đối xử khác nhau, có chủ ý:

- fingerprint **mới** không có trong baseline → **fail**, kèm tên đối tượng và việc autogenerate sẽ làm gì
  với DB (`would DROP this index -- silent performance loss`);
- fingerprint trong baseline **biến mất** → chỉ **warning** kèm danh sách dòng cần xoá. Chặn merge vì ai đó
  trả bớt nợ là hàng rào người ta sẽ tìm cách đi vòng.

Cách đo giống hệt §8: container pgvector riêng → `alembic upgrade head` → `compare_metadata` trong **tiến
trình con** (`tests/schema_drift_probe.py`) đọc metadata và `include_object` bằng cách `exec` đúng khối
import của `env.py`, nên hai bên không thể lệch nhau âm thầm. Container riêng chứ không dùng
`postgres_async_url` dùng chung: mọi test khác đều với tới DB đó, một `CREATE TABLE` sót lại sẽ hiện ra ở
đây thành drift ma. Giá của sự cô lập đó là ~5s.

Với `modify_*`, fingerprint mang theo cả **giá trị cũ và mới** chứ không chỉ tên cột. Nếu chỉ khoá theo cột
thì `users.avatar_url` vốn đã là `TEXT -> VARCHAR` được chấp nhận, nên thu model về `VARCHAR(10)` — một
`ALTER` cắt cụt dữ liệu thật — sẽ rơi trúng dòng baseline cũ và **lọt**. 35 trong 64 dòng là `modify_*` nên
đây không phải trường hợp hiếm. Ngoại lệ duy nhất là `modify_comment`: đổi cách nào cũng vô hại, và văn bản
tự do thì sẽ làm baseline nhiễu mỗi lần sửa chữ.

Chi phí: **~6s** cho cả 4 test.

**Nó chạy ở hai nơi, và cần cả hai.** Trong suite (rẻ, lập trình viên thấy ngay khi chạy pytest), và ở
**`Gate 6 - schema drift`** — job CI riêng, *không* `continue-on-error`. Lý do phải có job riêng chính là
Gate 4b: đó là job duy nhất chạy pytest và nó đang `continue-on-error: true`, còn Gate 3 chỉ `--collect-only`.
Một hàng rào chỉ nằm trong suite thì GitHub vẫn báo `success` cho PR làm mất index. Gate 6 chưa phải required
status check — đó là quyết định của owner và của ruleset — nhưng khác 4a/4b, nó đỏ thật trong
`gh run view --json`.

Đã mutation-test: xoá `index=True` của `employees.department_id` → toàn bộ 2574 test còn lại vẫn xanh, chỉ
`test_no_new_drift_between_models_and_migrated_schema` đỏ, và nó nêu đúng
`remove_index employees ix_employees_department_id`. Đó là bằng chứng cho khẳng định ở đoạn trên: trước hàng
rào này, không có gì trong repo giữ nhóm 14 index đó.

Một chỗ hở còn lại, ghi ra thay vì giấu: từ lúc drift được dọn đến lúc dòng baseline tương ứng bị xoá, tái
tạo đúng diff đó là miễn phí. Không phép đo đơn lẻ nào phân biệt được "đã dọn, chưa cập nhật baseline" với
"dọn rồi lại làm hỏng"; chặn merge lúc dọn là đánh đổi tệ hơn. Warning nêu đúng dòng cần xoá, xoá là đóng.

---

## 8. Cách đo lại

```bash
cd backend

# 1. Dựng schema sạch từ migration (đừng đo trên DB dev nếu chưa chắc nó khớp head)
createdb drift_audit
DATABASE_URL='postgresql+asyncpg://<user>:<pass>@<host>:5432/drift_audit' \
  .venv/bin/alembic upgrade head

# 2. So sánh. Phải dùng đúng metadata và đúng tuỳ chọn của alembic/env.py,
#    nếu không con số sẽ không khớp với autogenerate thật.
#    compare_metadata(MigrationContext.configure(conn, opts={...}), SQLModel.metadata)
```

Hai chỗ dễ đo lệch:

- **Metadata.** `env.py` không còn danh sách import tay; nó gọi
  `src.shared.model_registry.import_all_entity_modules()`. Gọi đúng hàm đó (hoặc `exec` khối import của
  `env.py`) trước khi đọc `SQLModel.metadata`.
- **`include_object`.** `env.py` truyền `include_object` để loại `gmail_label_mappings` (§5.1). Không truyền
  nó vào `MigrationContext.configure(..., opts=...)` thì sẽ đo dư đúng 2 diff (`remove_table` + `remove_index`)
  mà autogenerate thật không sinh.

Lưu ý khi đếm: các diff `modify_*` trả về dưới dạng **list lồng trong list**, nên `len()` trên kết quả thô
cho ra số top-level chứ không phải số diff thật. Phải trải phẳng trước khi đếm. Ở lần đo đầu chênh lệch này
là 112 với 113; ở trạng thái hiện tại thì không còn diff lồng nào nên cả hai đều là 64.

Postgres của stack dev **không publish port ra host**. Hai đường vào: `docker exec vroom-postgres psql -U
postgres -d <db>`, hoặc nối thẳng TCP tới IP container (`docker inspect vroom-postgres` →
`NetworkSettings.Networks.*.IPAddress`, bridge network định tuyến được từ host trên Linux) — đường thứ hai
cần thiết vì alembic và `create_engine` nói TCP chứ không chạy qua `docker exec`. Không phải recreate
container.

---

## 9. Nguồn tham chiếu

- `backend/alembic/versions/048_create_outbound_emails_table.py` — FK gốc, đúng ngay từ đầu
- `backend/alembic/versions/003_create_refresh_tokens_table.py` — `ondelete="CASCADE"` gốc
- `backend/alembic/versions/084_*.py` — sự cố `users.role`, tiền lệ của cả báo cáo này
- `backend/alembic/versions/085_repair_ai_config_and_assistant_session_fk.py` — hai sửa chữa phía DB của P2
- `backend/alembic/env.py` — nay import động qua `model_registry`, và `UNMANAGED_TABLES` /
  `include_object` quyết định bảng nào nằm ngoài phạm vi so sánh
- `backend/src/shared/model_registry.py` — quét AST tìm mọi class `table=True` trong `src/`
- `backend/tests/test_alembic_metadata_complete.py` — bất biến chặn tái diễn P0
- `backend/alembic/versions/008_create_gmail_tables.py` — nơi `gmail_label_mappings` ra đời;
  commit `76e9143` là nơi model của nó biến mất mà bảng thì không
