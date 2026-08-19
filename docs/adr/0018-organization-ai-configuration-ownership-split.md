# 18. Cấu hình AI cấp Organization: credential ở System Admin, consent và công tắc ở HR

status: accepted

Trang `/settings/ai` từng gộp hai loại quyết định khác bản chất dưới một guard `require_system_admin` duy nhất: cấu hình kỹ thuật (provider, `base_url`, model, API key) và quyết định nghiệp vụ (đồng ý chính sách dữ liệu, bật/tắt AI Automation và AI Assistant, chọn AI Policy Preset). Quyết định này tách đôi theo đúng ranh giới đó — không sửa tại chỗ [ADR 0009](./0009-system-admin-hr-role-separation.md) §2, vì đây là *đảo* một xếp loại đang ở trạng thái `accepted` (toàn bộ `ai-config/*` từng được liệt kê là của System Admin).

## Bối cảnh

Người bấm "đồng ý gửi nội dung email tuyển dụng và CV ra nhà cung cấp AI bên ngoài" đang cam kết thay công ty rằng dữ liệu cá nhân của ứng viên được phép rời khỏi hệ thống. Người đó là System Admin — chính vai trò mà ADR-0009 cấm tuyệt đối khỏi dữ liệu HR (`require_hr` từ chối họ với 403). Trao quyền ký cho người không được phép xem thứ mình đang ký là ngược; HR, bên sở hữu và chịu trách nhiệm với hồ sơ ứng viên, trước đó không có màn hình nào để thấy.

`CONTEXT.md` từng tự mâu thuẫn đúng chỗ này: mục *Organization AI Configuration* ghi "do HR quản lý", mục *Essential Setup Task* ghi "Cấu hình AI thuộc System Admin, không thuộc HR". Code theo vế sau. Quyết định ở đây: vế sau đúng với *credential*, vế trước đúng với *consent và công tắc*.

## Quyết định

**9 route nghiệp vụ chuyển hẳn** sang `/api/hr/organization/ai-config/*`, gác `require_hr`, không nhân đôi với namespace cũ: `POST accept-data-policy`, `GET data-policy`, `POST automation/consent`, `POST assistant/consent`, `POST automation/enable`, `POST automation/disable`, `POST assistant/enable`, `POST assistant/disable`, `PUT policy-preset`. Chuyển hẳn, không giữ đường vào song song — hai đường vào cùng một state với hai guard khác nhau đúng là loại bẫy ADR-0009 dựng ra để tránh.

**2 route đọc mới cho HR**, cùng namespace, response model hẹp và không có credential (`hr_ai_config_schemas.py`): `GET ""` (`HRAIConfigurationResponse` — trạng thái consent/toggle/preset) và `GET /provider-status` (`HRAIProviderStatusResponse` — đúng một boolean "đã kết nối / chưa cấu hình"). Không cho HR đọc ké `GET /organization/ai-config` của System Admin: route đó trả nguyên `OrganizationAIConfigurationResponse` gồm cả phần credential, và đó chính là lỗ rò 6a phải chặn.

**System Admin giữ 13 route** ở `/api/system-admin/organization/ai-config/*`: `GET ai-config` + alias `GET ai-configuration`, `PUT ai-config`, `POST test`, `PUT source`, `PUT provider`, `POST activate-key`, `POST revoke-key`, `POST test-deployment-key`, và 4 route `classification-rollout/*`.

**Thứ tự kiểm tra khi bật AI đã đảo**: consent + data policy trước, credential và health check provider sau (`organization_ai_config_service.py`, `enable_automation`/`enable_assistant`). Trước khi đảo, HR sẽ nhận lỗi "provider health check failed" về một cấu hình họ không được xem, không được sửa — lỗi ngoài phạm vi của mình trước cả khi chạm lỗi thuộc phạm vi mình.

**Frontend**: HR có route group `(dashboard)/settings/ai-config` riêng (đích của Quick-Start Guide task mới của HR — xem [ADR 0015](./0015-quick-start-guide-task-always-has-destination.md)). Đây **không phải** `/settings/ai` — đường đó vẫn là trang credential của System Admin, không đổi. Hai trang cùng tên sẽ collision khi build vì route group của Next không đổi path.

## Considered Options

- **Mở rộng route System Admin hiện có bằng guard thứ hai (`require_hr` hoặc `require_system_admin`)**: bị loại. Đó đúng là hai đường vào cùng một state — bẫy mà chính sách cách ly nghiêm ngặt của ADR-0009 dựng ra để chặn.
- **Cho HR đọc thẳng `GET /organization/ai-config` của System Admin**: bị loại. Response model đó mang credential; hẹp hoá ở tầng client (ẩn field trên UI) không chặn được response thật đã rời server.
- **Giữ `GET data-policy` ở namespace System Admin** (đọc-không-ghi nên có vẻ vô hại): bị loại. Sau khi tách, HR là người ký "đồng ý", nên HR phải đọc được chính văn bản mình ký — để nguyên là tái tạo đúng vấn đề ADR này sinh ra để sửa. Nội dung route chỉ là văn bản tĩnh (`version` + `items`), không mang credential, nên chuyển không mở thêm bề mặt rò rỉ nào.

## Consequences

- [ADR 0009](./0009-system-admin-hr-role-separation.md) §2 (bảng namespace `/api/system-admin/*`) và §4 (Quick-Start Guide 3+3) nay có khối "Đã thay đổi" trỏ về đây.
- [ADR 0003](./0003-organization-ai-configuration.md) mô tả "AI được cấu hình ở cấp Organization bởi HR" — nay chỉ đúng với phần consent/công tắc; khối trỏ về đây làm rõ credential thuộc System Admin.
- `backend/tests/test_role_isolation.py` mang case cho cả 9 route mới (HR gọi được, System Admin nhận 403) và ngược lại (System Admin gọi 13 route giữ lại được, HR nhận 403 nếu còn thử namespace cũ).
- Audit trail: `accept_data_policy` và các route consent/toggle ghi audit với actor là user HR thay vì System Admin — màn hình Nhật ký hoạt động (thuộc System Admin) hiển thị email HR ở các dòng này. Đây là kết quả đúng: trách nhiệm ký nay thuộc về HR.
- Không đổi mô hình phân quyền (`SYSTEM_ADMIN`/`HR`/`USER` vẫn rời nhau, không phân cấp) và không đổi route `classification-rollout/*` — cả hai ngoài phạm vi quyết định này.
