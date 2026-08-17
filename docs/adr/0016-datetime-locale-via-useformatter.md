# Định dạng ngày giờ qua `useFormatter()`, không luồn tham số `locale`

status: accepted

Ngày giờ hiển thị trên UI chuyển sang đọc locale qua **`useFormatter()` của next-intl** thay vì nhận `locale` làm tham số. `formatDateTime`/`formatDate` trong `components/shared-ui.tsx` đổi từ hàm thường thành **hook** (`useFormatDateTime`/`useFormatDate`), đọc locale từ context thay vì từ đối số gọi hàm. Ba helper còn lại giữ nguyên dạng hàm thường (`formatRuntimeDetail`, `formatLatency`, `formatAuditDetails`) nhưng **bỏ giá trị mặc định** của tham số `locale` — thiếu tham số giờ là lỗi biên dịch, không còn là một nhánh runtime âm thầm chạy sai.

## Bối cảnh

#313 phát hiện định dạng ngày giờ hỏng theo **hai cơ chế khác nhau**, và ticket gốc cùng comment đầu của tôi trên đó chỉ thấy cơ chế thứ nhất:

1. **Literal ngay trong lời gọi** — `new Date(x).toLocaleString('vi-VN', …)`, 16 chỗ / 11 file. Tìm được bằng `rg 'vi-VN'`.
2. **Tham số `locale` có giá trị mặc định `'vi-VN'`, không ai truyền** — sáu helper trong `shared-ui.tsx`. 27/43 call site ngày giờ rơi vào nhánh này, và **không dòng nào trong số đó viết ra chữ `'vi-VN'`** — vô hình với mọi phép tìm theo chuỗi, kể cả phép đếm 28 lần đầu của tôi trên chính ticket này.

Doc comment của `isVietnamese` trong `components/shared-ui.tsx` (trước sửa) còn khẳng định sai rằng "every call site feeds them `useLocale()`" — mệnh đề đó đúng cho ba helper health/audit nhưng sai cho `formatDateTime`/`formatDate` (0/42 call site truyền locale) và cho một trong ba call site của `formatAuditDetails`. Mệnh đề sai này **là một phần cơ chế**: nó đọc như bằng chứng rằng các giá trị mặc định vô hại, nên không ai đi đếm. Đã viết lại thẳng đoạn văn thay vì thêm blockquote đính chính bên trên — để nguyên câu sai bên dưới một ghi chú sửa lỗi thì người đọc lướt vẫn gặp câu sai trước.

## Considered Options

- **Luồn `locale` qua 43 call site** (hướng ticket gốc đề xuất, dùng `useLocale()` truyền tay). Bị loại: giữ nguyên đúng cơ chế đã sinh ra lỗi — một tham số có mặc định mà lời gọi có thể quên, và call site thứ 44 lại lặng lẽ sai như 27 call site trước nó. Sửa triệu chứng, không sửa cơ chế.

- **Bỏ giá trị mặc định, bắt buộc mọi call site tự gọi `useLocale()` rồi truyền tay** (đã áp dụng cho `formatRuntimeDetail`/`formatLatency`/`formatAuditDetails`). Biến một mặc định runtime im lặng thành ràng buộc compile-time — đúng khuôn [ADR 0015](./0015-quick-start-guide-task-always-has-destination.md). Vẫn phải sửa toàn bộ call site, và vẫn có một tham số để mỗi lời gọi mới phải nhớ truyền đúng — chỉ là quên nó giờ là lỗi `tsc` thay vì lỗi runtime câm lặng.

- **Dùng `useFormatter()` của next-intl cho phần ngày giờ** (đã chọn cho `formatDateTime`/`formatDate`). `next-intl@4.13.2` có sẵn (`use-intl/dist/types/react/useFormatter.d.ts`), repo chưa dùng ở đâu. Hook đọc locale đang hoạt động trực tiếp từ context, nên **không còn tham số `locale` để quên** — không phải canh cơ chế lỗi bằng ràng buộc compile-time, mà gỡ hẳn cơ chế đó. `formatDateTime`/`formatDate` đổi tên thành `useFormatDateTime`/`useFormatDate` vì React yêu cầu tên hook bắt đầu bằng `use`; mỗi call site chỉ đổi phần import và thêm một dòng gọi hook, phần gọi hàm tại JSX giữ nguyên.

Ba helper còn lại (`formatRuntimeDetail`, `formatLatency`, `formatAuditDetails`) **không** chuyển sang `useFormatter()` — chúng không gọi API `Intl` nào cả, mà rẽ nhánh `isVietnamese(locale)` để chọn giữa hai bảng chuỗi Việt/Anh viết sẵn. `useFormatter()` không có chỗ cho hình dạng đó, nên ba helper này đi theo đường thứ hai (bỏ mặc định, giữ dạng hàm thường).

`formatVND` **giữ nguyên dạng hàm thường và giữ nguyên mặc định `locale = 'vi-VN'`** — cố ý, không phải bỏ sót. Định dạng tiền tệ (phân tách hàng nghìn `1.000` so với `1,000`) là một quyết định khác với định dạng ngày/tháng, và 16 call site của nó không được đếm vào phạm vi #313. Gộp vào cùng một PR sẽ làm review không kiểm được hết. `docs/frontend-review-notes` (nếu có) hoặc issue mới nên mở riêng nếu ai muốn dọn nốt.

## Consequences

- **Named `dateTime` formats tập trung ở `i18n/request.ts`.** `getRequestConfig` khai `formats.dateTime` với bốn preset (`full`, `short`, `shortWithYear`, `time`) — năm call site trước đây lặp y hệt cùng một object `{ hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' }` hoặc tương tự; gom vào một chỗ theo đúng tinh thần Tailwind-style utility duplication mà `tailwind-color-shades.test.ts` đã đặt tên cho lớp lỗi này ở #317. `formats` được export riêng (`export const formats = {...}`) để test dùng lại nguyên bản, không viết lại.

- **Preset đi tới RSC qua request config, không qua prop.** `app/layout.tsx` — layout duy nhất trong cây render `NextIntlClientProvider`, và cũng là `layout.tsx` duy nhất — **không** truyền `formats`, và như vậy là đúng. File không khai `'use client'`, nên React phân giải `next-intl` qua export condition `react-server` (`next-intl/package.json`) sang `index.react-server.js`, nơi `NextIntlClientProvider` thật ra là `NextIntlClientProviderServer`. Component đó tự lấp chỗ trống: `formats: formats === undefined ? await getFormats() : formats`, với `getFormats()` là `(await getConfig()).formats`. Nên `i18n/request.ts` là **nguồn duy nhất** cho mọi mốc thời gian render phía server.

- **`timeZone` phải khai tường minh, và đây là phần suýt lọt.** Chuyển sang `useFormatter()` kéo theo một hệ quả mà bản thân việc đổi locale không có: `timeZone` giờ đi qua cùng đường config đó. Khi `getRequestConfig` **không** trả `timeZone`, next-intl không để trống cho trình duyệt tự quyết — `getConfig.js` lấp bằng `Intl.DateTimeFormat().resolvedOptions().timeZone` **của tiến trình server**, rồi `NextIntlClientProviderServer` truyền giá trị đã phân giải đó xuống client như một prop tường minh. `frontend/Dockerfile` là `node:22-slim`, không đặt `TZ`, và `docker-compose.yml` cũng không — nên production là UTC.

  Đo trên chính `use-intl` của repo: một lượt check-in lưu `2026-08-03T01:00:00Z` (đúng ví dụ "08:00 giờ VN" trong seed script) render ra `01:00:00 3/8/2026`. Lệch 7 tiếng, trên **mọi** giờ chấm công, giờ phát hành payslip, mốc audit và giờ nhận mail. Trước #313, `toLocaleString(locale)` không truyền `timeZone` nên trình duyệt dùng zone của chính nó — đúng với người dùng ở VN, đổi lại một lần lệch hydration. Nên đây là **hồi quy do chính thay đổi này sinh ra**: đổi một nhấp nháy hydration lấy một sai số 7 tiếng vĩnh viễn.

  Điều khiến nó suýt lọt: máy dev ở VN phân giải `Asia/Saigon` và render đúng `08:00:00`. Lỗi chỉ xuất hiện khi app vào container — không test nào, không gate nào thấy được.

  Chốt bằng hằng `APP_TIME_ZONE = 'Asia/Ho_Chi_Minh'` trong `i18n/request.ts`. **Zone cố định thay vì zone của người đọc là một quyết định miền, không phải cách chữa cháy**: khung giờ chấm công, hạn chốt lương và đơn nghỉ phép là dữ kiện về ngày làm việc của một doanh nghiệp Việt Nam. Quản lý đọc console từ Singapore vẫn phải thấy ca bắt đầu 08:00 hiện ra 08:00, không phải 09:00. Cho phép cấu hình theo từng tổ chức là nhu cầu thật nhưng không thuộc ticket này — xem #345.

  Chủ repo xác nhận đây là quyết định sản phẩm đã chốt, không phải một phán đoán tạm chờ xác nhận — ngày 2026-08-17. `datetime-locale.test.ts` khoá lại bằng một assertion đọc thẳng `APP_TIME_ZONE`, nên ai đọc code rồi "sửa" về múi giờ trình duyệt để trông có vẻ đúng cho máy dev sẽ gặp test đỏ, không trôi lặng lẽ như hồi #313. #345 vẫn mở, nhưng phạm vi thu hẹp còn đúng phần cấu hình **theo từng tổ chức**; nếu triển khai, mặc định phải giữ UTC+7, không phải rơi về múi giờ trình duyệt hay server.

- **Preset thiếu không ném lỗi — nó âm thầm trả `String(date)`.** Đây là hệ quả đáng nhớ nhất của việc đổi sang preset đặt tên. Khi tên preset không phân giải được, `use-intl` gọi `onError(MISSING_FORMAT)` (mặc định chỉ là `console.error`) rồi trả fallback, mà fallback của `dateTime` là `String(value)` — tức `'Mon Aug 17 2026 09:49:27 GMT+0700 (Indochina Time)'`: thô, tiếng Anh, mù locale. Nói cách khác là một phiên bản **tệ hơn** của chính lỗi #313, giao thẳng ra production kèm một dòng console không ai đọc.

  Đo thật: xoá dòng `formats,` khỏi `i18n/request.ts` để lại **toàn bộ 222 test xanh** và `tsc --noEmit` sạch. Sáu test component có render ngày giờ đều tự truyền `formats={formats}` cho `NextIntlClientProvider`, nên chúng vẫn chạy đúng trong khi đường production hỏng — chúng kiểm provider, chưa bao giờ kiểm cái config nuôi provider đó. Ba assertion mới trong `datetime-locale.test.ts` đóng đúng khe này, và đã chứng minh bằng mutation: xoá `formats,` → 1 đỏ; đổi tên preset `full` → `fullish` trong khi call site vẫn xin `'full'` → 2 đỏ.

- **Hai chỗ không phải component/hook phải nhận `format` làm tham số**, vì chúng không thể tự gọi `useFormatter()`: `gmail/helpers.ts`'s `fmtDate` (giữ nguyên logic phân biệt Unix timestamp giây / ISO 8601, chỉ đổi phần locale) và `AiChat.tsx`'s `nowTime`. Cả hai đổi chữ ký để nhận `format: ReturnType<typeof useFormatter>` từ caller.

- **Lưới chặn mới, `frontend/datetime-locale.test.ts`**, cùng hình dạng `tailwind-color-shades.test.ts` (#317): suy tập cần kiểm từ cây mã, không hard-code danh sách file. Bắt hai hình dạng — literal locale trong lời gọi `toLocale*`, và tham số `locale` có mặc định string literal trong khai báo hàm — với một ngoại lệ đặt tên tường minh (`formatVND`) thay vì cố suy ra "helper nào là ngày giờ" một cách cấu trúc, vì `formatAuditDetails` (đúng phạm vi) không gọi API `Intl` nào để phân biệt bằng shape thân hàm.

  Hai phép quét đó là **quét mã nguồn thuần** — chúng chứng minh không file nào *viết ra* một locale, và không nói gì về việc preset đặt tên có phân giải được lúc chạy hay không. Nên khối `describe` thứ hai trong cùng file canh nửa còn lại: mọi tên preset xuất hiện trong cây phải có trong `formats.dateTime`, `getRequestConfig` phải thật sự trả `formats` ra ngoài, và hành vi xuống cấp `String(date)` được **chạy** chứ không chỉ mô tả. Tên preset trích bằng cách duyệt cân bằng ngoặc chứ không bằng regex: đối số đầu gần như luôn là `new Date(...)`, mà `)` của nó cắt đứt mọi mẫu `\([^)]*\)`, và vài dòng có hai lời gọi `format.dateTime` cạnh nhau — regex non-greedy sẽ ghép mở của lời gọi này với đối số của lời gọi kia và bịa ra tên preset không ai xin.

  `next-intl/server` bị stub trong file test này. Package ship hai build sau một export condition, Vitest không có condition `react-server`, nên nó phân giải sang build client — nơi `getRequestConfig` là stub ném `"not supported in Client Components"`. Đó là phân giải sai, không phải module sai. Bản thay thế không phải xấp xỉ: implementation server thật là hàm identity nguyên vẹn (`function getRequestConfig(createRequestConfig) { return createRequestConfig; }`), nó tồn tại để mang chữ ký kiểu chứ không mang hành vi.

- **`components/operate.tsx` và `lib/dashboard-ui.tsx`** (hai file re-export `@deprecated` không còn ai import) mất `formatDateTime`/`formatDate` khỏi danh sách re-export — hook không re-export được dưới dạng hàm thường mà giữ đúng ngữ nghĩa cũ, và không có call site nào cần chúng.
