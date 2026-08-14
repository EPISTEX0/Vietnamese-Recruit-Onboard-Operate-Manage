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

Palette xoay quanh slate (neutrals) và một accent indigo.

- **Ink (`#0f172a`, slate-900):** tiêu đề, text cốt lõi.
- **Primary (`#4f46e5`, indigo-600):** accent duy nhất cho action chính.
- **Primary-soft (`#6366f1`, indigo-500):** icon, focus ring, nhấn nhẹ.
- **Muted (`#64748b`, slate-500):** subtitle, metadata, caption.
- **Surface (`#ffffff`):** card, panel.
- **Page (`#f8fafc`, slate-50):** nền trang.

Quy ước: nền trang `bg-slate-50/50`, body text `text-slate-800` (xem
`frontend/app/layout.tsx`). Icon nhấn dùng `text-indigo-600`, tiêu đề
`text-slate-900`, subtitle `text-slate-500` (xem `frontend/components/operate.tsx`).

## Typography

- **sans / body:** Be Vietnam Pro 1rem, lineHeight 1.6 — font chính cho toàn bộ giao diện.
- **h1:** Be Vietnam Pro Semibold 1.25rem (text-xl), weight 600.
- **label / caption:** Be Vietnam Pro Medium 0.875rem (text-sm), weight 500.
- **mono:** JetBrains Mono 0.875rem — cho `code`, mã NV, audit id, qua `--font-mono`.

Cả hai font tải qua `next/font/google` trong `frontend/app/layout.tsx` và gắn vào CSS variable
`--font-sans` / `--font-mono`; body dùng `font-sans`. Be Vietnam Pro khai `subsets: ['vietnamese', 'latin']`
với weight 400/500/600/700 — đủ cho dấu tiếng Việt, đừng đổi sang font thiếu subset `vietnamese`.

## Do's and Don'ts

- **Do** dùng indigo làm accent duy nhất cho action/icon — không trộn accent thứ hai.
- **Do** dùng `rounded-2xl` cho card, shadow mềm — giữ cảm giác sản phẩm dụng cụ.
- **Do** ưu tiên tiếng Việt trong nhãn giao diện (deployment cho doanh nghiệp VN).
- **Don't** mang accent hay font của design system cũ vào `frontend/` — lịch sử chuyển đổi nằm ở
  [`docs/adr/0006-ai-studio-design-system.md`](./docs/adr/0006-ai-studio-design-system.md), không phải ở đây.
- **Don't** dùng gradient trang trí hoặc làm nền — hệ thống này phẳng có chủ đích. **Ngoại lệ:** gradient accent `from-indigo-600 to-indigo-500` được phép trên CTA đặc biệt (vd: nút "Trợ lý AI") để tạo điểm nhấn.
