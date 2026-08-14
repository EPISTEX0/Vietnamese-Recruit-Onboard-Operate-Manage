# AI Studio design system as the active frontend

status: accepted

Vroom HR chuẩn hoá design system cho frontend chính `vroom-hr/` trên hệ thống
**AI Studio**: tông slate làm nền, một accent duy nhất **indigo** cho action và
icon, font **Inter** (kèm **JetBrains Mono** cho code/mã/audit), Tailwind v4
CSS-first, card bo `rounded-2xl`, shadow mềm. Hệ thống **Heritage** cũ
(terracotta `#B8422E`, warm limestone, font Fraunces / Public Sans / Space
Grotesk) thuộc về `frontend/` (Next.js 14, Tailwind 3) và không còn là design
system nguồn sự thật; `frontend/` được giữ làm backup legacy.

> **Đã thay đổi.** Hai chi tiết trong đoạn trên không còn đúng — xem [ADR 0013](./0013-vietnamese-typography-and-single-frontend-directory.md). Font sans giờ là **Be Vietnam Pro** thay cho Inter (JetBrains Mono giữ nguyên), và `vroom-hr/` không còn tồn tại: app AI Studio nằm ở `frontend/`, không còn thư mục Heritage legacy nào. Phần còn lại của ADR này (nền slate, một accent indigo duy nhất, Tailwind v4 CSS-first, card `rounded-2xl`) vẫn nguyên giá trị.

Quyết định này chốt DESIGN.md với code thực và ngắt sự lệch giữa tài liệu và
triển khai: DESIGN.md trước đây mô tả Heritage dù frontend chính đã là AI Studio.

## Considered Options

- **Giữ Heritage cho `vroom-hr/`:** bị loại. `vroom-hr/` đã được AI Studio dựng
  xoay quanh domain model với slate/indigo/Inter; ép Heritage sẽ phá vỡ cấu trúc
  và lịch sử quyết định của phase UI integration (xem
  `docs/ai-studio-ui-integration-plan.md:42`).
- **Không chốt design system trong ADR, để DESIGN.md tự trị:** bị loại. ADR cho
  ngữ cảnh *tại sao* đổi và ranh giới Heritage vs AI Studio, tránh ai đó quay
  lại terracotta/Fraunces khi sửa UI.
- **Chốt AI Studio (được chọn):** khớp code thực, đơn giản hoá một accent, dùng
  font Inter sẵn có cho sản phẩm dụng cụ.

## Consequences

- DESIGN.md mô tả AI Studio làm nguồn sự thật; Heritage chỉ còn tham chiếu cho
  `frontend/` legacy.
- UI mới phải dùng slate/indigo/Inter + `rounded-2xl`, không trộn accent
  terracotta hay font Fraunces sang `vroom-hr/`.
- Tailwind v4 (CSS-first, `@import "tailwindcss"` trong `globals.css`) nghĩa là
  không có `tailwind.config.js` truyền thống ở `vroom-hr/`; token mở rộng nếu
  cần phải qua CSS.
- `frontend/` chỉ giữ cho tham chiếu/backup; không dựng tính năng mới trên đó.
  Khi xoá `frontend/` hẳn, xoá luôn phần Heritage trong DESIGN.md và ADR này.

Source evidence:
- `vroom-hr/app/layout.tsx:2,6-14,24` — Inter + JetBrains_Mono qua
  `next/font/google`, body `font-sans antialiased text-slate-800 bg-slate-50/50`.
- `vroom-hr/app/globals.css:1` — `@import "tailwindcss"` (Tailwind v4).
- `vroom-hr/components/operate.tsx:9-10` — "AI Studio design system:
  slate/indigo, rounded-2xl cards, soft shadows, mono accents".
- `docs/ai-studio-ui-integration-plan.md:42` — design system mới do AI Studio
  quyết, không phải Heritage/Warm-Professional của `frontend/` cũ.
- `frontend/src/app/globals.css:9`,
  `frontend/src/__tests__/heritage-compliance.test.ts:6` — Heritage là hệ của
  `frontend/`.
