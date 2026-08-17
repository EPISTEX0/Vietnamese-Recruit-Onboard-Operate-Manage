# Enum backend là nguồn sự thật duy nhất cho audit action type; frontend suy ra

status: accepted

Backend `AuditActionType` (`backend/src/modules/identity/domain/entities.py`) là nguồn sự thật duy nhất cho những gì `audit_logs.action_type` thực sự ghi. `backend/scripts/gen_audit_action_types.py` import thẳng enum đó và sinh `frontend/lib/audit-action-types.generated.ts` — `AUDIT_ACTION_TYPES` (mảng giá trị) và type `AuditActionType` suy ra từ nó. `frontend/lib/api/admin.ts` re-export type này thay vì khai một union viết tay. Sinh lại là thao tác tay (`cd backend && uv run python scripts/gen_audit_action_types.py`); `backend/tests/modules/identity/test_audit_action_types_freshness.py` là gate báo khi file sinh ra lệch với enum.

## Bối cảnh

#331 đo được năm danh sách audit action type không có ràng buộc nào giữa nhau: enum backend (34 giá trị), một union TS viết tay (15, thiếu 19 giá trị thật), `AUDIT_ACTION_GROUPS` dựng dropdown lọc (44, trong đó 10 giá trị không enum nào ghi được), `AUDIT_ACTION_LABELS` (45), và namespace i18n `audit` ở hai file `vi.json`/`en.json` (44 mỗi file). Cột `audit_logs.action_type` là `varchar(50)`, không phải enum PostgreSQL, nên tầng DB không giữ ràng buộc nào.

Nặng nhất trong ba hệ quả: 10 giá trị ma trong `AUDIT_ACTION_GROUPS` khiến dropdown lọc ở `settings/audit/page.tsx` mời admin chọn những tuỳ chọn luôn ra danh sách rỗng — một admin chọn `login` nhìn thấy rỗng và kết luận hợp lý "không ai đăng nhập". Đó là kiểu nói dối mà System Admin Console sinh ra để ngăn.

## Considered Options

- **Test backend đọc file frontend** (đọc `shared-ui.tsx`/`messages/*.json` bằng regex, đối chiếu với enum). Canh được cả ba hệ quả (union hẹp, dropdown ma, nhãn lệch), nhưng xoá được **không** cái nào — union TS vẫn phải liệt kê tay 34 giá trị, và mỗi giá trị mới thêm vào enum vẫn cần một người nhớ cập nhật union rồi tin tưởng test bắt được nếu quên. Bị loại vì lệch-rồi-bị-test-bắt yếu hơn không-thể-lệch, và vì đường này lặp lại đúng lớp lỗi info vừa tự dính khi làm census: một bộ trích xuất regex đọc TSX bám vào hình dạng nguồn (xem phần "known-positive" dưới), không phải quan hệ kiểu.

- **Tạo tác sinh (đã chọn).** Một script backend import enum, ghi ra một file `.ts`; `admin.ts` re-export type từ file đó thay vì khai union tay. Union TS hẹp hơn enum — hệ quả nặng thứ hai của ticket — **biến mất theo cấu trúc** thay vì được canh: không còn chỗ nào để liệt kê tay 34 giá trị nên không còn chỗ nào để quên một giá trị mới. Hai hệ quả còn lại (dropdown ma, nhãn lệch) không tự xoá theo cách này — `AUDIT_ACTION_GROUPS` và `AUDIT_ACTION_LABELS` vẫn là hằng viết tay, chỉ là giờ được test đối chiếu với `AUDIT_ACTION_TYPES` sinh ra thay vì đối chiếu với enum qua một lớp regex trung gian.

Repo này **chưa từng có codegen** trước ticket này — không script sinh file nào tồn tại. Thêm một cơ chế mới chỉ đáng giá vì nó xoá được một hệ quả thật, nên đi kèm ba ràng buộc giữ nó nhỏ: **một** script sinh, **một** file sinh ra; không watch mode, không nối vào `build`/`dev`, không pre-commit hook; và freshness check sống trong gate `pytest` sẵn có, không thêm job CI mới.

## Bất đối xứng cố ý: `AUDIT_ACTION_GROUPS` bằng đúng enum, `AUDIT_ACTION_LABELS` được phép rộng hơn

`AUDIT_ACTION_GROUPS` (dropdown lọc) phải khớp **chính xác** `AUDIT_ACTION_TYPES` — không thiếu, không thừa. Một tuỳ chọn lọc trỏ tới giá trị enum không có là hệ quả nặng nhất của ticket; một giá trị enum không có tuỳ chọn lọc là cùng một lỗi nhìn từ hướng ngược lại.

`AUDIT_ACTION_LABELS` (hiển thị dòng lịch sử) được phép **rộng hơn**, và mười một key thừa — mười giá trị ma của `AUDIT_ACTION_GROUPS` cộng `ai_policy_preset_update` — được **giữ nguyên**, không xoá. Lý do nằm ở chính giới hạn chứng cứ của ticket: `audit_logs.action_type` là `varchar(50)`, không phải enum PostgreSQL, và #331 chỉ chứng minh được *code hiện tại* không ghi mười giá trị đó — không chứng minh được production chưa từng ghi. Hai hằng này làm hai việc khác nhau: `AUDIT_ACTION_GROUPS` mời chọn một giá trị *sắp lọc*, nên trỏ tới giá trị không thể xảy ra là nói dối; `AUDIT_ACTION_LABELS` hiển thị một giá trị *đã xảy ra*, nên xoá label của nó làm dòng lịch sử cũ tụt xuống hiển thị chuỗi thô — đó là làm hỏng, không phải dọn dẹp.

Mười một key thừa được khai báo tường minh thành `LEGACY_AUDIT_ACTION_LABELS` (`frontend/components/shared-ui.tsx`), kèm comment nói rõ lý do trên, và `frontend/audit-action-types.test.ts` khẳng định `Object.keys(AUDIT_ACTION_LABELS) \ AUDIT_ACTION_TYPES == Object.keys(LEGACY_AUDIT_ACTION_LABELS)` — tức danh sách ngoại lệ là **đóng**: ai thêm một key lạ khác vào `AUDIT_ACTION_LABELS` sau này (không phải giá trị enum, không phải một trong mười một ngoại lệ được gọi tên) vẫn bị bắt. Khuôn "ngoại lệ đóng, gọi tên tường minh" này đã dùng ở repo cho `formatVND` trong `frontend/datetime-locale.test.ts`.

## Consequences

- `frontend/lib/audit-action-types.generated.ts` là artifact sinh, đầu file ghi rõ `DO NOT EDIT` và lệnh sinh lại. Sửa tay file này không có tác dụng lâu dài — lần sinh lại tiếp theo sẽ ghi đè.
- `backend/tests/modules/identity/test_audit_action_types_freshness.py` là gate duy nhất canh quan hệ enum ↔ file sinh; nó trích giá trị từ file `.ts` bằng regex (không import TS được từ Python), nên tự mang known-positive assert (`"whitelist_add" in ...`) và sàn số lượng (`>= 30`) để không xanh rỗng nếu bộ trích xuất hỏng.
- `frontend/audit-action-types.test.ts` đọc `AUDIT_ACTION_GROUPS`, `AUDIT_ACTION_LABELS`, `LEGACY_AUDIT_ACTION_LABELS` qua import trực tiếp (type-checked), không qua quét text — nên không mang rủi ro bộ trích xuất giống test backend; một import sai hình dạng đã đỏ ở `tsc --noEmit` trước khi test này chạy.
- Không gộp năm danh sách thành một trong PR này. `AUDIT_ACTION_GROUPS`/`AUDIT_ACTION_LABELS`/namespace i18n vẫn là hằng viết tay — ADR này chỉ đóng đường union TS lệch; hợp nhất triệt để hơn (nếu cần) là quyết định khác.
- Không đổi cột `audit_logs.action_type` sang enum PostgreSQL — đó là migration, ngoài phạm vi ticket, và giữ nguyên giới hạn chứng cứ nêu ở trên (varchar vẫn có thể mang giá trị lịch sử enum hiện tại không còn khai).
