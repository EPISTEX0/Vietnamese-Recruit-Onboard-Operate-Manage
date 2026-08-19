# Quick-Start Guide thay thế Feature Tour

Sau First-Run Setup, HR cần được hướng dẫn bằng tác vụ thực tế (kết nối Gmail, cấu hình AI, tạo Job Opening, upload Knowledge Base) thay vì tour giới thiệu sidebar 19 bước. Quyết định này thay thế `FeatureTour` component đang dùng bằng Guide Widget persistent trên Dashboard + trang `/guide` riêng, với auto-detect completion và dismiss tạm thời.

## Trạng thái

proposed

## Các phương án đã cân nhắc

- **Feature tour 19 bước hiện tại**: loại bỏ vì không persistent, không actionable, hardcoded tiếng Việt, không thể xem lại.
- **Interactive walkthrough với spotlight từng trang**: loại bỏ vì chi phí implement cao hơn giá trị mang lại; HR cần làm việc thực tế, không phải xem demo.
- **Task list thuần manual (không auto-detect)**: loại bỏ vì nếu HR connect Gmail thành công mà vẫn thấy task "kết nối Gmail" chưa xong → gây khó hiểu.
- **Tooltip / hint trên từng trang riêng lẻ**: dành cho tương lai, không phải scope này.

## Hệ quả

- Backend: thêm field `guide_progress` JSON vào `organization_settings` table (dismissed, completed_tasks, seen). API `GET /guide/progress` và `PATCH /guide/progress`.
- Backend: auto-detect hook khi Gmail connected, AI configured, Job Opening created, KB document uploaded → tự động cập nhật completed_tasks.
- Frontend: Guide Widget trên Dashboard (dismissible), `/guide` page với detail từng task.
- Xoá `feature-tour.tsx` và `?tour=true` param.
- Guide tự động ẩn khi 4/4 task completed; HR có thể dismiss tạm thời và xem lại sau.

KHÔNG phát triển tour dạng slideshow nữa. Nếu cần hướng dẫn chi tiết trên từng trang, làm hint/tooltip nhúng trong trang đó — không phải tour riêng.

> **Đã thay đổi.** "4/4 task" ở trên mô tả một danh sách task duy nhất, trước khi [ADR 0009](./0009-system-admin-hr-role-separation.md) §4 tách Quick-Start Guide theo vai trò (System Admin ba, HR ba). Sau [ADR 0018](./0018-organization-ai-configuration-ownership-split.md), tập của HR lên bốn — tổng hai vai trò là ba cộng bốn, không còn một con số "N/N" duy nhất. Nguyên tắc auto-detect completion và Guide Widget persistent vẫn nguyên giá trị, chỉ áp dụng độc lập cho từng vai trò.
