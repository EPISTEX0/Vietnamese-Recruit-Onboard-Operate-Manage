# Quick-Start Guide — mọi task đều có đích đến

status: accepted

`SetupTaskView.action` bỏ `| null`, và `SetupTaskActions` trở thành `Record<SetupTaskId, SetupTaskAction>` toàn phần. Invariant của module từ đây: **mọi task trong `SETUP_TASK_IDS` đều có đích đến**. Kèm theo là nhánh `task.action ? <Link> : <div>` trong `SetupTaskRow`, map `TASK_GUIDANCE_KEY`, tham số `actions` thứ hai của `buildSetupGuide`, và khoá i18n `settings.quickStart.taskGoogleOAuthGuidance` ở cả hai locale — tất cả đều chỉ tồn tại để phục vụ nhánh `null`.

Quyết định này thay thế mệnh đề *"Nhánh `action: null` vẫn ở lại"* trong đính chính của #307 tại [ADR 0014](./0014-system-admin-console-navigation.md). Phần còn lại của ADR 0014 — route thật cho từng đề mục, ba nhóm, base path `/settings`, trang chủ riêng, checklist suy trạng thái từ dữ liệu sống chứ không lưu cờ — vẫn nguyên giá trị.

## Considered Options

- **Gỡ nhánh `null`** (đã chọn). Lý do quyết định không phải là "hiện không ai đi qua nhánh đó" — mà là nhánh đó chưa bao giờ làm được việc nó được biện minh. Lập luận giữ nó là "nó ngăn checklist trỏ tới chỗ không làm được việc", nhưng một `href` sai vẫn qua kiểu, vẫn render thành `<Link>`, vẫn dẫn admin tới nơi không làm được việc. Trường hợp duy nhất nó bắt là ai đó *cố ý* khai `null` — tức là bắt đúng người đã biết mình đang làm gì. Đổi lại, một `Record` toàn phần bắt được đúng trường hợp nguy hiểm còn lại: thêm id vào `SETUP_TASK_IDS` mà quên khai đích là lỗi `tsc` ngay tại route map, nơi có thiếu sót, thay vì một dòng checklist render chết ở runtime mà không gì báo. Đây là đổi một nhánh runtime chết lấy một ràng buộc compile-time thật.

- **Giữ nhánh `null` và trả nốt phần còn thiếu**: viết test render `SetupTaskRow` với `action: null` (chỗ logic thật nằm, và hôm nay không được test), sửa docstring của `buildSetupGuide` cho khớp thứ tham số thứ hai thật sự chốt được. Bị loại vì nó trả tiền để giữ một cơ chế không mang lại điều đã hứa: sau khi trả xong, checklist vẫn có thể trỏ sai chỗ y như trước. Chi phí thì thật — một component test, một tham số production tồn tại chỉ vì test, và một chuỗi i18n phải viết lại cho đúng.

- **Để nguyên**: bị loại. Bốn mảnh không chạy — chuỗi i18n mà nội dung đã sai từ #307 ("Console chưa có màn hình cho mục này"), entry map chết, nhánh component không test nào đi qua, tham số production thêm vào chỉ để test — cộng một comment mô tả sai thứ nó canh. Đó là trạng thái tệ hơn cả hai đường trên.

## Consequences

- **Task chưa có đích đến không còn biểu diễn được, và đó là điểm chính.** Ngày một task như vậy xuất hiện là ngày mở lại thiết kế này, không phải ngày trả `null`. Vẽ nó thành một dòng không bấm được rồi coi như đã xử lý cũng là một kiểu nói dối người vừa cài xong deployment — cùng loại với việc trỏ tạm sang đề mục bên cạnh để làm tròn số đếm, thứ mà ADR 0014 đã loại vì đúng lý do đó.

- **Ràng buộc là compile-time thuần, không có test runtime tương ứng.** Một assertion runtime chỉ nhắc lại thứ không compile nổi. Đã chứng minh bằng cách thêm id thứ tư vào `SETUP_TASK_IDS`: `tsc` đỏ tại dòng khai `TASK_ACTIONS` trong `lib/system-admin/setup-guide.ts`. Việc các đích đến là route thật chứ không phải chuỗi nghe hợp lý vẫn do `app/[locale]/(system-admin)/settings/page.test.ts` canh, ở tầng route.

- **`SetupTaskActions` trở thành module-private.** Nó được export chỉ để test dựng route map inject vào `buildSetupGuide`; không còn tham số nhận nó thì không còn ai import. `SetupTaskAction` vẫn export vì nằm trong `SetupTaskView` mà caller đọc.

- **`SetupTaskRow` luôn là `<Link>`.** Component mất một nhánh rẽ, và cùng với nó là dòng guidance vốn không bao giờ hiện.
