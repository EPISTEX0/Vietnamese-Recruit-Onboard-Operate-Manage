---
version: stable
name: AI Studio
description: Sản phẩm HR dụng cụ, rõ ràng, tin cậy — slate/indigo trên nền sáng.
colors:
  ink: "#0f172a"
  primary: "#4f46e5"
  primary-soft: "#6366f1"
  muted: "#64748b"
  surface: "#ffffff"
  page: "#f8fafc"
  on-primary: "#ffffff"
typography:
  sans:
    fontFamily: Be Vietnam Pro
    fontSize: 1rem
    lineHeight: 1.6
  h1:
    fontFamily: Be Vietnam Pro
    fontSize: 1.25rem
    fontWeight: 600
  label:
    fontFamily: Be Vietnam Pro
    fontSize: 0.875rem
    fontWeight: 500
  mono:
    fontFamily: JetBrains Mono
    fontSize: 0.875rem
rounded:
  card: 16px
  pill: 9999px
spacing:
  sm: 8px
  md: 16px
  lg: 32px
components:
  page-header:
    iconColor: "{colors.primary-soft}"
    titleColor: "{colors.ink}"
    subtitleColor: "{colors.muted}"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.card}"
    shadow: soft
  primary-button:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.pill}"
---

## Overview

Hệ thống thiết kế **AI Studio** là design system đang dùng cho `frontend/` —
frontend chính của Vroom HR (package `vroom-hr`). Đây là hệ thống được quyết bởi AI Studio khi tái xây
dây frontend xoay quanh domain model (xem
[`docs/adr/0006-ai-studio-design-system.md`](./docs/adr/0006-ai-studio-design-system.md)).

Đặc trưng: tông slate làm nền, một accent duy nhất **indigo** cho action và icon,
font **Be Vietnam Pro** cho mọi text hiển thị và **JetBrains Mono** cho code /
mã nội bộ / audit. Card bo góc lớn (`rounded-2xl`), shadow mềm, negative space
vừa phải — đọc được, không trang trí.

## Colors

Palette xoay quanh slate (neutrals) và một accent indigo. Bảy tone ngữ nghĩa cho **trạng thái** là một
trục riêng — xem [Semantic palette](#semantic-palette) bên dưới.

- **Ink (`#0f172a`, slate-900):** tiêu đề, text cốt lõi.
- **Primary (`#4f46e5`, indigo-600):** accent duy nhất cho action chính.
- **Primary-soft (`#6366f1`, indigo-500):** icon, focus ring, nhấn nhẹ.
- **Muted (`#64748b`, slate-500):** subtitle, metadata, caption.
- **Surface (`#ffffff`):** card, panel.
- **Page (`#f8fafc`, slate-50):** nền trang.

Quy ước: nền trang `bg-slate-50/50`, body text `text-slate-800` (xem
`frontend/app/layout.tsx`). Icon nhấn dùng `text-indigo-600`, tiêu đề
`text-slate-900`, subtitle `text-slate-500` (xem `frontend/components/operate.tsx`).

## Semantic palette

Có **hai trục màu**, và luật "một accent duy nhất" ở trên chỉ nói về trục thứ nhất:

1. **Accent thương hiệu — indigo.** Trả lời "chỗ nào bấm được": nút hành động chính, link, focus ring,
   icon trang trí của header và card. Đúng một accent, không trộn accent thứ hai. Đây là luật cũ, không
   đổi.
2. **Palette ngữ nghĩa — `BadgeTone`.** Trả lời "cái này đang ở trạng thái nào": xong / đang chờ / hỏng.
   Bảy tone có tên, class chính tắc ở `frontend/components/shared-ui.tsx:172-200`
   (`BadgeTone` + `BADGE_TONE_PARTS`). **Đó là nguồn chân lý** — bảng class không chép lại vào đây; sửa
   ở đó, không sửa ở tài liệu. Một bảng duy nhất cho cả `Badge` lẫn `StatusPill` kể từ #316.

Hai trục độc lập và một màn hình mang cả hai: nút "Lưu" indigo đứng cạnh badge "Thất bại" rose không vi
phạm gì cả. Cái luật cấm là dùng **màu thứ hai làm affordance** — ví dụ nút hành động chính màu
emerald, hay link màu sky.

### Nghĩa từng tone

Rút từ call site đang có, không tự đặt. Cột cuối là chứng cứ, đọc ngược lại được.

| Tone | Nghĩa | Call site |
|---|---|---|
| `emerald` | Xong, đạt, đang hoạt động — kết quả tốt đã chốt | `JOB_STATUS_META.open`, `CONFLICT_STATUS_META.resolved`, hộp thành công ở `forgot-password/page.tsx:76`, `change-password/page.tsx:104` |
| `amber` | Chưa xong, đang chờ ai đó làm gì đó — cảnh báo chứ chưa hỏng | `INBOX_STATUS_META.needs_classification`, `PROC_STATUS_META.needs_review`, `CONFLICT_STATUS_META.pending`, khối bắt buộc xác nhận chính sách dữ liệu ở `settings/ai/page.tsx:280` |
| `rose` | Hỏng, thất bại, bị từ chối — và hành động phá huỷ | `PROC_STATUS_META.failed`, mọi hộp lỗi form (`login/page.tsx:118`), `ButtonDanger` (`shared-ui.tsx:571`) |
| `indigo` | Đang chạy theo luồng chính, chưa mang tin tốt hay xấu | `CANDIDATE_STATUS_META.reviewing`, `INBOX_STATUS_META.ready_for_review`, `PROC_STATUS_META.ocr_processing`/`llm_parsing`, `onboarding/page.tsx:61` (`in_progress`) |
| `slate` | Không có trạng thái đáng tô màu — chưa bắt đầu, hoặc đã khép lại và không cần chú ý | `CANDIDATE_STATUS_META.new`/`archived`, `INBOX_STATUS_META.resolved`, `PROC_STATUS_META.skipped`/`dismissed` |
| `violet` | Đúng **một** việc: bước "đã lên lịch phỏng vấn" của pipeline tuyển dụng. Không phải một nghĩa tổng quát — xem dưới. | `CANDIDATE_STATUS_META.interview_scheduled` (`shared-ui.tsx:262`) |
| `sky` | **Chưa có nghĩa.** Xem dưới. | — |

**`sky` không rút được nghĩa nhất quán.** Ba cách dùng khác hẳn nhau, nên ở đây ghi đúng như vậy thay vì
bịa một dòng cho đủ bảng:

- `knowledge-base/page.tsx:499` — `processing`, tức "đang xử lý". Nhưng cùng ý đó,
  `gmail/historical-import.tsx:295` dùng `amber` và `recruitment/review/page.tsx:15` dùng `indigo`.
  (Trước #318 chỗ này là hai dòng, vì file có hai `StatusBadge` trùng nhau.)
- `requests/page.tsx:240` và `employee/requests/page.tsx:268` — phân biệt `leave` với `overtime`. Đây là
  **phân loại**, không phải trạng thái; cùng cấu trúc với việc dùng indigo cho nhánh còn lại.
- `settings/page.tsx` thẻ Tài khoản — thuần trang trí, để bốn thẻ trong hàng khác màu nhau.

Chọn `sky` cho việc mới là đang chọn một màu chưa ai định nghĩa. Nếu cần nó, chốt nghĩa ở đây trước.

**`violet` thì ngược lại: có nghĩa, nhưng hẹp.** Nó không trả lời một câu hỏi trạng thái tổng quát nào
cả — nó tồn tại vì `interview_scheduled` phải phân biệt được với `reviewing` (`indigo`) ở
`recruitment/interviews/page.tsx:196`, chỗ hai trạng thái liền kề của cùng một pipeline đứng cạnh nhau
trong một danh sách. Dùng `violet` cho việc khác là đang mượn màu của một bước tuyển dụng cụ thể.

#316 hợp nhất hai bảng và **giữ cả hai tone**: khuyết tật là hai bảng lệch nhau, không phải thừa tone,
và bỏ tone nào cũng buộc phải viết lại call site rồi chọn màu thay. `sky` vì thế vẫn nằm trong bảng
chính tắc mà vẫn chưa có nghĩa — ghi đúng như vậy có ích hơn là bịa một dòng cho đủ.

### Chỗ các status map mâu thuẫn nhau

Không liên quan tới hai bảng tone mà #316 vừa hợp nhất — đây là chuyện các status map gán tone khác
nhau cho cùng một status. Ghi lại vì đây là dữ kiện, không phải luật — ai chạm vào status map thì cần biết:

- `draft` là `slate` trong `JOB_STATUS_META` (`shared-ui.tsx:282`) nhưng `amber` ở
  `payroll/payslips/page.tsx:369`. Hai cách đọc đều có lý — "chưa phát hành nên chưa cần chú ý" so với
  "đang chờ bạn phát hành".
- `cancelled` là `rose` trong `JOB_STATUS_META` nhưng `slate` ở
  `requests/page.tsx:361` và `employee/requests/page.tsx:328`.

Chưa chốt hướng nào đúng; đừng đọc bảng trên như thể đã chốt.

### Icon mang màu gì

Icon dùng indigo **trừ khi bản thân nó là readout của một trạng thái**, lúc đó nó lấy tone ngữ nghĩa của
trạng thái ấy: `settings/page.tsx:258,261` (tick emerald khi xong, dấu hỏi amber khi không đọc được),
`gmail/connection-panel.tsx:27-28` (`Plug` emerald khi đã nối, `Unplug` slate khi chưa),
`gmail/historical-import.tsx:183-184`.

Trường hợp thứ ba, yếu nhất: một hàng thẻ cho mỗi icon một tone chỉ để **phân biệt hạng mục**, không thẻ
nào báo trạng thái gì — `settings/page.tsx:151-176` (AI / Runtime / Tài khoản / Nhật ký). Đang tồn tại,
không phải luật, và #308 giữ nguyên tone bốn thẻ đó thay vì nhân dịp đặt lại.

Đừng gộp mọi hàng thẻ vào ca này. `recruitment/metrics/page.tsx:31-34` nhìn y hệt về cấu trúc nhưng
tone của nó **có nghĩa thật**: emerald cho `successRate`, rose cho `failureRate` — đúng nghĩa bảng trên
gán. Khác nhau ở chỗ tone trả lời câu gì, không ở chỗ nó nằm trong component nào.

**Sắc độ indigo của hai trục không bằng nhau, và đó không phải lỗi.** Icon nhấn thương hiệu là
`text-indigo-600` (mục [Colors](#colors) ở trên); tone ngữ nghĩa `indigo` là `text-indigo-700`, vì nó
sinh ra để đứng trên nền `bg-indigo-50` của badge. Hệ quả cụ thể: thẻ AI ở `settings/page.tsx:151` đi
qua bảng ngữ nghĩa nên icon của nó là 700, không phải 600 như icon header cạnh đó. Đây là cái giá của
việc #308 cho cả hàng thẻ đi qua một API duy nhất, và nó được ghi ra chứ không giấu.

### Quy ước mới (chưa mô tả hiện trạng)

Hai dòng dưới là **luật đặt ra từ #308**, không phải mô tả code đang có — 35 file hiện viết chuỗi class
tone thẳng tại call site thay vì đi qua bảng, và ticket này cố ý không chuyển chúng:

- **Do** đi qua `Badge` / `BadgeTone` / `BADGE_TONE_PARTS` khi cần màu trạng thái. Viết
  `bg-emerald-50 text-emerald-700` bằng tay là cách `text-emerald-600` lọt vào cạnh
  `text-emerald-700` của bảng.
- **Do** ghi nghĩa vào bảng trên khi thêm tone mới. Một tone không có nghĩa ở đây là một màu không ai
  tra được — `sky` là ví dụ sống.

## Typography

- **sans / body:** Be Vietnam Pro 1rem, lineHeight 1.6 — font chính cho toàn bộ giao diện.
- **h1:** Be Vietnam Pro Semibold 1.25rem (text-xl), weight 600.
- **label / caption:** Be Vietnam Pro Medium 0.875rem (text-sm), weight 500.
- **mono:** JetBrains Mono 0.875rem — cho `code`, mã NV, audit id, qua `--font-mono`.

Cả hai font tải qua `next/font/google` trong `frontend/app/layout.tsx` và gắn vào CSS variable
`--font-sans` / `--font-mono`; body dùng `font-sans`. Be Vietnam Pro khai `subsets: ['vietnamese', 'latin']`
với weight 400/500/600/700 — đủ cho dấu tiếng Việt, đừng đổi sang font thiếu subset `vietnamese`.

## Do's and Don'ts

- **Do** dùng indigo làm accent duy nhất cho action/icon — không trộn accent thứ hai. Luật này nói về
  affordance; màu trạng thái đi theo `BadgeTone`, xem [Semantic palette](#semantic-palette).
- **Do** dùng `rounded-2xl` cho card, shadow mềm — giữ cảm giác sản phẩm dụng cụ.
- **Do** ưu tiên tiếng Việt trong nhãn giao diện (deployment cho doanh nghiệp VN).
- **Don't** mang accent hay font của design system cũ vào `frontend/` — lịch sử chuyển đổi nằm ở
  [`docs/adr/0006-ai-studio-design-system.md`](./docs/adr/0006-ai-studio-design-system.md), không phải ở đây.
- **Don't** dùng gradient trang trí hoặc làm nền — hệ thống này phẳng có chủ đích. **Ngoại lệ:** gradient accent `from-indigo-600 to-indigo-500` được phép trên CTA đặc biệt (vd: nút "Trợ lý AI") để tạo điểm nhấn.
