# Định dạng ngày giờ qua `useFormatter()`, không luồn tham số `locale`

status: accepted

Ngày giờ hiển thị trên UI chuyển sang đọc locale qua **`useFormatter()` của next-intl** thay vì nhận `locale` làm tham số. `formatDateTime`/`formatDate` trong `components/shared-ui.tsx` đổi từ hàm thường thành **hook** (`useFormatDateTime`/`useFormatDate`), đọc locale từ context thay vì từ đối số gọi hàm. Ba helper còn lại giữ nguyên dạng hàm thường (`formatRuntimeDetail`, `formatLatency`, `formatAuditDetails`) nhưng **bỏ giá trị mặc định** của tham số `locale` — thiếu tham số giờ là lỗi biên dịch, không còn là một nhánh runtime âm thầm chạy sai.

## Bối cảnh

#313 phát hiện định dạng ngày giờ hỏng theo **hai cơ chế khác nhau**, và ticket gốc cùng comment đầu của tôi trên đó chỉ thấy cơ chế thứ nhất:

1. **Literal ngay trong lời gọi** — `new Date(x).toLocaleString('vi-VN', …)`, 16 chỗ / 11 file. Tìm được bằng `rg 'vi-VN'`.
2. **Tham số `locale` có giá trị mặc định `'vi-VN'`, không ai truyền** — sáu helper trong `shared-ui.tsx`. 27/43 call site ngày giờ rơi vào nhánh này, và **không dòng nào trong số đó viết ra chữ `'vi-VN'`** — vô hình với mọi phép tìm theo chuỗi, kể cả phép đếm 28 lần đầu của tôi trên chính ticket này.

`components/shared-ui.tsx:666-668` (trước sửa) còn khẳng định sai rằng "every call site feeds them `useLocale()`" — mệnh đề đó đúng cho ba helper health/audit nhưng sai cho `formatDateTime`/`formatDate` (0/26 call site truyền locale) và cho một trong ba call site của `formatAuditDetails`. Đã đính chính tại chỗ bằng blockquote theo đúng khuôn `AGENTS.md` §"Domain docs", không viết ADR riêng cho việc đính chính đó vì đây là sửa một mệnh đề dữ kiện sai, không phải đổi quyết định.

## Considered Options

- **Luồn `locale` qua 43 call site** (hướng ticket gốc đề xuất, dùng `useLocale()` truyền tay). Bị loại: giữ nguyên đúng cơ chế đã sinh ra lỗi — một tham số có mặc định mà lời gọi có thể quên, và call site thứ 44 lại lặng lẽ sai như 27 call site trước nó. Sửa triệu chứng, không sửa cơ chế.

- **Bỏ giá trị mặc định, bắt buộc mọi call site tự gọi `useLocale()` rồi truyền tay** (đã áp dụng cho `formatRuntimeDetail`/`formatLatency`/`formatAuditDetails`). Biến một mặc định runtime im lặng thành ràng buộc compile-time — đúng khuôn [ADR 0015](./0015-quick-start-guide-task-always-has-destination.md). Vẫn phải sửa toàn bộ call site, và vẫn có một tham số để mỗi lời gọi mới phải nhớ truyền đúng — chỉ là quên nó giờ là lỗi `tsc` thay vì lỗi runtime câm lặng.

- **Dùng `useFormatter()` của next-intl cho phần ngày giờ** (đã chọn cho `formatDateTime`/`formatDate`). `next-intl@4.13.2` có sẵn (`use-intl/dist/types/react/useFormatter.d.ts`), repo chưa dùng ở đâu. Hook đọc locale đang hoạt động trực tiếp từ context, nên **không còn tham số `locale` để quên** — không phải canh cơ chế lỗi bằng ràng buộc compile-time, mà gỡ hẳn cơ chế đó. `formatDateTime`/`formatDate` đổi tên thành `useFormatDateTime`/`useFormatDate` vì React yêu cầu tên hook bắt đầu bằng `use`; mỗi call site chỉ đổi phần import và thêm một dòng gọi hook, phần gọi hàm tại JSX giữ nguyên.

Ba helper còn lại (`formatRuntimeDetail`, `formatLatency`, `formatAuditDetails`) **không** chuyển sang `useFormatter()` — chúng không gọi API `Intl` nào cả, mà rẽ nhánh `isVietnamese(locale)` để chọn giữa hai bảng chuỗi Việt/Anh viết sẵn. `useFormatter()` không có chỗ cho hình dạng đó, nên ba helper này đi theo đường thứ hai (bỏ mặc định, giữ dạng hàm thường).

`formatVND` **giữ nguyên dạng hàm thường và giữ nguyên mặc định `locale = 'vi-VN'`** — cố ý, không phải bỏ sót. Định dạng tiền tệ (phân tách hàng nghìn `1.000` so với `1,000`) là một quyết định khác với định dạng ngày/tháng, và 16 call site của nó không được đếm vào phạm vi #313. Gộp vào cùng một PR sẽ làm review không kiểm được hết. `docs/frontend-review-notes` (nếu có) hoặc issue mới nên mở riêng nếu ai muốn dọn nốt.

## Consequences

- **Named `dateTime` formats tập trung ở `i18n/request.ts`.** `getRequestConfig` khai `formats.dateTime` với bốn preset (`full`, `short`, `shortWithYear`, `time`) — năm call site trước đây lặp y hệt cùng một object `{ hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' }` hoặc tương tự; gom vào một chỗ theo đúng tinh thần Tailwind-style utility duplication mà `tailwind-color-shades.test.ts` đã đặt tên cho lớp lỗi này ở #317. `formats` được export riêng (`export const formats = {...}`) để test dùng lại nguyên bản, không viết lại.

- **Hai chỗ không phải component/hook phải nhận `format` làm tham số**, vì chúng không thể tự gọi `useFormatter()`: `gmail/helpers.ts`'s `fmtDate` (giữ nguyên logic phân biệt Unix timestamp giây / ISO 8601, chỉ đổi phần locale) và `AiChat.tsx`'s `nowTime`. Cả hai đổi chữ ký để nhận `format: ReturnType<typeof useFormatter>` từ caller.

- **Lưới chặn mới, `frontend/datetime-locale.test.ts`**, cùng hình dạng `tailwind-color-shades.test.ts` (#317): suy tập cần kiểm từ cây mã, không hard-code danh sách file. Bắt hai hình dạng — literal locale trong lời gọi `toLocale*`, và tham số `locale` có mặc định string literal trong khai báo hàm — với một ngoại lệ đặt tên tường minh (`formatVND`) thay vì cố suy ra "helper nào là ngày giờ" một cách cấu trúc, vì `formatAuditDetails` (đúng phạm vi) không gọi API `Intl` nào để phân biệt bằng shape thân hàm.

- **`components/operate.tsx` và `lib/dashboard-ui.tsx`** (hai file re-export `@deprecated` không còn ai import) mất `formatDateTime`/`formatDate` khỏi danh sách re-export — hook không re-export được dưới dạng hàm thường mà giữ đúng ngữ nghĩa cũ, và không có call site nào cần chúng.
