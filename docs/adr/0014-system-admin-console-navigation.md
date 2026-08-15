# System Admin Console — điều hướng bằng route và một trang chủ riêng

status: accepted

System Admin Console chuyển từ **một trang duy nhất với tab giữ trong state** sang **route thật cho từng đề mục, cộng một trang chủ riêng**. Sidebar mang bảy đề mục cấu hình, chia ba nhóm; `/settings` không còn là trang cấu hình AI mà trở thành Tổng quan hệ thống.

Lý do trực tiếp: trên deployment vừa setup xong, console nhìn như một trang trống. Sidebar chỉ có **đúng một** item, và trong đó ba nhãn gần trùng nhau xếp chồng — badge "Quản trị hệ thống", section label "QUẢN TRỊ HỆ THỐNG", item "Cấu hình AI & Hệ thống" — trong khi bảy đề mục thật nằm ở thanh tab ngang, không xuất hiện trong URL và không bookmark được.

**Base path giữ nguyên `/settings`.** Đây là chỗ ADR này cố ý đi ngược chữ trong [ADR 0009](./0009-system-admin-hr-role-separation.md) §3, vốn viết System Admin tạo tài khoản HR "tại `/system-admin/users`". Chuỗi URL không phải phần có giá trị của quyết định đó — phần có giá trị là **sự tách bạch bề mặt** giữa System Admin và HR, và sự tách bạch ấy đã được thực hiện đầy đủ bằng route group `(system-admin)` với guard riêng. Dời base path sang `/system-admin/*` sẽ phải sửa `middleware.ts:45-48`, `lib/auth/roles.ts:45` và hai assertion trong `lib/auth/session.test.ts:166,205`, làm gãy mọi bookmark hiện có, và đổi lại người dùng không nhận được gì.

**Trạng thái hoàn thành của Quick-Start Guide được suy ra, không lưu.** Ba Essential Setup Task của System Admin đọc trực tiếp từ dữ liệu sống: `getOAuthConfig()` (client_id rỗng hay không), `getOrganizationAIConfiguration().configured`, và `listUsers()` có tài khoản role `hr` hay không. Không có cờ dismissed nào được ghi ở đâu cả. Hệ quả có chủ đích: nếu API key AI bị thu hồi về sau, task đó **hiện lại** — checklist mô tả trạng thái thật của hệ thống chứ không mô tả việc người dùng đã bấm gì.

**Task chưa xác định được thì không nói là chưa làm.** Mỗi task có ba trạng thái `done` / `todo` / `unknown`; query đang chạy hoặc trả lỗi đều rơi vào `unknown`, và tiến độ "x/3" chỉ hiện khi cả ba đã resolve. Vẽ "☐ Chưa tạo tài khoản HR" khi `listUsers()` trả 500 là thông tin sai, và người đọc nó là người vừa cài xong hệ thống — chưa đủ ngữ cảnh để nghi ngờ màn hình.

## Considered Options

- **Giữ tab, chỉ restyle cho giống Dashboard HR**: bị loại. Nó không chạm vào nguyên nhân — sidebar vẫn một item và vẫn trống. Phần restyle vốn đã đi kèm miễn phí khi dùng `PageHeader` sẵn có trong `components/shared-ui.tsx:444`, không cần đánh đổi gì để lấy.

- **Chuyển tab sang sidebar nhưng vẫn giữ state, URL đứng yên ở `/settings`**: bị loại. Sidebar mà không đổi URL là cái bẫy: người dùng bookmark "Nhật ký hoạt động", quay lại thấy "Cấu hình AI".

  > Bản nháp đầu của ADR này còn viện dẫn một lý do thứ hai — rằng tách route mở ra khả năng prefetch React Query theo từng đề mục qua `ROUTE_QUERY_MAP` của app shell. **Lý do đó sai và đã bị gỡ.** Cơ chế ấy chưa bao giờ chạy: shell gọi `prefetchQuery` chỉ với `queryKey`, không có `queryFn`, và query client không đặt `queryFn` mặc định — nên `ensureQueryFn` của `@tanstack/query-core` trả về một hàm reject sẵn, khiến mỗi key **settle thành error** thay vì được warm. Việc gỡ hẳn cơ chế này là một quyết định riêng, không thuộc ADR này.

- **Dời sang `/system-admin/*` cho khớp chữ ADR 0009 §3**: bị loại, lý do ở trên. Đáng ghi lại vì đây đúng là thứ sẽ bị đảo ngược bởi người đọc ADR 0009 §3 rồi tưởng code đang sai.

- **Thêm endpoint `/api/system-admin/overview` gộp trạng thái setup**: bị loại ở vòng này. Bốn query của trang chủ đều đã tồn tại và đều đã nằm trong React Query cache; gộp endpoint là tối ưu hoá nên làm *sau* khi trang tồn tại và đo được, không phải trước.

- **Checklist ở lại vĩnh viễn sau khi đủ 3/3**: bị loại. Giải quyết "trống lúc mới setup" bằng một checklist rồi để nó chết trên màn hình là tạo ra khoảng trống thứ hai, tệ hơn cái ban đầu.

## Consequences

- **`/settings` đổi nghĩa.** Trước: trang cấu hình AI. Sau: Tổng quan hệ thống. `lib/auth/roles.ts:45` và hai test tại `lib/auth/session.test.ts:166,205` **không cần sửa** — chúng khẳng định System Admin đáp xuống `/settings`, và điều đó vẫn đúng. Không có deep link nào gãy vì tab trước đây vốn không nằm trong URL.

  Nhưng "không cần sửa" không có nghĩa là "được canh". Cả ba assertion — hai cái trên cộng `lib/auth/roles.test.ts:79` — chỉ khẳng định **chuỗi** `"/settings"`, không khẳng định route đó resolve tới đâu. Xoá `settings/page.tsx` thì cả ba vẫn xanh và `next build` vẫn sạch, vì page thiếu chỉ là route không được emit chứ không phải lỗi build; trong khi mọi System Admin 404 ngay lúc đăng nhập. Seam đó giờ do `app/[locale]/(system-admin)/settings/page.test.ts` canh — nó *import* chính module nó canh, nên module biến mất là suite đỏ với exit code 1, và nó kiểm luôn đích redirect có tồn tại để không 404 chậm một nhịp. Đã chứng minh bằng mutation cả hai chiều, không phải bằng khẳng định.

- **`middleware.ts` không cần sửa.** `middleware.ts:50` lọc bằng `startsWith`, nên `/settings/ai`, `/settings/users`… đã được bảo vệ sẵn ngay khi route ra đời.

- **Nội dung thường trực của trang chủ là trạng thái vận hành, không phải checklist.** Bốn thẻ bento (AI provider, runtime health, tài khoản, nhật ký) cộng mười dòng audit gần nhất. `getAuditLogs({ start_date, page_size: 10 })` trả cả `total` lẫn `items` (`lib/api/admin.ts:141-146`), nên một request phục vụ cả thẻ đếm lẫn danh sách.

  > Bản đầu của gạch đầu dòng này kết luận "trang chủ vẫn đúng **bốn** query". **Con số đó sai.** Thẻ runtime health cần `services[]`, và trường đó chỉ tồn tại trên `RuntimeHealthResponse` từ `getRuntimeHealth()` (`lib/api/admin.ts:157-160, 521`); không payload nào trong bốn cái được liệt kê mang nó. Trang chủ chạy **năm** query. Ràng buộc thật mà con số ấy định diễn đạt vẫn nguyên vẹn và là thứ đáng giữ: **không thêm endpoint backend nào**, đặc biệt không dựng endpoint gộp kiểu `/api/system-admin/overview`. Cả năm query đều đã tồn tại. Phát hiện bởi peer làm #302, trước khi viết dòng code nào.

- **Task "Cấu hình Google OAuth" của Quick-Start Guide không có đích điều hướng, và đó là sự thật về hệ thống chứ không phải thiếu sót của trang chủ.**

  > Spec và #300 viết "mỗi task navigate tới đề mục tương ứng khi click". **Mệnh đề đó sai với một trong ba task.** Console chưa từng có màn hình cấu hình OAuth: `rg -ni 'oauth' frontend/app/` không trả một hit nào trong *toàn bộ* `app/`, và bản trước khi tách route cũng đúng bảy tab (`TabId` không có `oauth`). Backend thì đủ — `GET`/`POST /oauth/config` (`identity/api/admin_router.py:1057, 1085`) — nên phần *suy trạng thái* của task chạy được đúng như mô tả; chỉ đích đến là không tồn tại.
  >
  > Cách xử lý đã chọn: view-model của task mang **action nullable** — điều hướng khi có đề mục, và một dòng hướng dẫn khi không. Ba lối ra khác đều bị loại: dựng đề mục thứ tám ngay trong ticket trang chủ phá "bảy đề mục" của ADR này và nhét một bề mặt chạm client secret vào một PR không được review cho việc đó; bỏ task đi thì trái ADR-0009 §4, vốn nói ba; trỏ tạm sang một đề mục có sẵn thì chính là kiểu nói dối người vừa cài xong mà cả trang này sinh ra để chặn — `/settings/domains` là tên miền email được phép đăng nhập, không phải OAuth client credentials.
  >
  > Đề mục OAuth là một quyết định riêng, đã tách thành ticket của nó. Khi nó có, task này chuyển thành điều hướng bằng một thay đổi trong module thuần, không đụng component.

- **Hàm suy trạng thái checklist phải nhận `QueryResult` từ ngoài vào**, không được tự gọi `useQuery` bên trong. Đây là ràng buộc thiết kế do yêu cầu test đặt ra: đó là chỗ duy nhất trong thay đổi này có thể sai âm thầm, và nó chỉ test được như hàm thuần nếu không tự đi lấy dữ liệu.

- **"Công cụ AI" đổi tên thành "Công cụ Assistant"** (đổi value, giữ key `settings.aiTools`). Đề mục đó chỉ bật/tắt `readTools` và `draftTools` của AI Assistant; `CONTEXT.md` định nghĩa Tool / Read-Tool / Draft-Tool là khái niệm nội bộ AI Assistant. Tên cũ rộng hơn nội dung thật và khiến người dùng tưởng tìm thấy AI Automation ở đó, trong khi AI Automation nằm bên "Cấu hình AI".

- **`sidebarBadge` bị gỡ khỏi shell admin nhưng prop vẫn còn** — `app/[locale]/(employee)/layout.tsx:35` vẫn dùng nó.

- **`SectionCard`, `ErrorBox`, `Empty`, `StatusBadge` chuyển sang `settings/_components/`, không nhập vào `components/shared-ui.tsx`.** Chúng trông như bản trùng lặp của `Card`/`ErrorBanner`/`EmptyState` nhưng không phải: `ErrorBox` có nút thử lại mà `ErrorBanner` không có, và `SectionCard` có header strip mà `Card` không có. Đổi sang đồ shared-ui là hạ cấp khoác áo dọn dẹp.

- **ADR 0009 §3 không sửa tại chỗ.** Theo precedent của repo (ADR 0006 ← ADR 0013, ADR 0007 ← ADR 0012), ADR là bản ghi tại thời điểm ra quyết định; chỗ `/system-admin/users` được để nguyên và cắm pointer trỏ sang đây.
