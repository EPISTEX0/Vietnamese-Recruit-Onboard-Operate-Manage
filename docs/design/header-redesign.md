# Header Redesign — Vroom HR

## Mục tiêu

Thiết kế lại header navigation với trải nghiệm hiện đại, chuyên nghiệp, phù hợp với brand "Đỏ Việt" (Crimson) và warm-toned UI. Cải thiện responsive, micro-interaction, và accessibility.

## Tech Stack

- Next.js 14 (App Router)
- React 18
- Tailwind CSS (với design tokens từ globals.css)
- Radix UI primitives (DropdownMenu, Popover, NavigationMenu)
- motion (framer-motion) — cho animation
- lucide-react — icons
- next-themes — dark/light mode (đã có sẵn)

## Cấu trúc file hiện tại (giữ nguyên)

```
frontend/src/components/header-navigation/
├── header-navigation.tsx       # Main component
├── header-utilities.tsx        # Search, notifications, account
├── nav-item-trigger.tsx        # Button trigger cho mỗi nav group
├── mega-menu-panel.tsx         # Dropdown panel cho nav group
├── mobile-menu-overlay.tsx     # Full-screen mobile menu
├── focus-utils.ts              # Focus management
├── navigation-utils.ts         # Navigation helpers
└── index.ts                    # Exports
```

## Thiết kế mới — Thay đổi chi tiết

### 1. Header Navigation (`header-navigation.tsx`)

#### Visual updates:
- **Chiều cao**: Giữ `h-14` (56px) nhưng thêm `backdrop-blur-md bg-background/85` cho hiệu ứng frosted glass nhẹ, border-bottom mỏng hơn.
- **Container**: `px-6` thay vì `px-4` (căn chỉnh với layout padding mới).
- **Logo**: Avatar "V" badge cải tiến — dùng `bg-gradient-to-br from-primary to-primary/80`, bỏ `border`, thêm glow nhẹ.
- **Loading state**: Thêm skeleton shimmer animation cho toàn bộ header (logo + nav items + utilities).
- **Breadcrumbs**: Nhúng breadcrumb vào header (có thể collapse trên mobile). Sử dụng BreadcrumbProvider đã có.

#### Behavior updates:
- **Mega menu**: Thêm `motion` (framer-motion) cho open/close animation (scale + fade).
- **Hover intent**: Giảm timer từ 300ms → 200ms.
- **Sticky shadow**: Thêm shadow nhẹ (`shadow-sm`) khi scroll xuống (dùng `useScroll` từ motion).
- **Route change**: Transition mượt hơn khi đổi route, phối hợp với NavigationProgress.

#### Responsive:
- **Desktop (md+)**: Full nav groups + utilities.
- **Tablet (sm-md)**: Collapse nav groups vào hamburger + giữ search + account.
- **Mobile (<sm)**: Chỉ hamburger + logo + account avatar.

### 2. Header Utilities (`header-utilities.tsx`)

#### Visual updates:
- **Search button**: Thêm `rounded-lg bg-muted/50 border border-border/30 px-3 py-1.5` để trông như một search box nhỏ, có placeholder "Tìm kiếm..." (Việt) và `⌘K` badge.
- **Notifications**: Thêm badge (số 0) disabled style (opacity-30) cho tới khi có real data. Popover content cải tiến với icon và empty state đẹp hơn.
- **Account dropdown**:
  - Avatar: Thêm role badge (admin → đỏ, user → xanh) góc dưới phải avatar.
  - Dropdown: Thêm "Kênh nhân viên" / "Kênh quản trị" switch link ở bottom.
  - Thêm ThemeToggle (dark/light) nếu chưa có.
  - User info section với role label.
- **Theme toggle**: Thêm nút sun/moon icon toggle (dùng `useTheme` từ next-themes) bên cạnh search.

### 3. Mega Menu Panel (`mega-menu-panel.tsx`)

#### Visual updates:
- **Animation**: Dùng `motion.div` với `initial={{ opacity: 0, scale: 0.95, y: -4 }}`, `animate={{ opacity: 1, scale: 1, y: 0 }}` transition 150ms.
- **Layout**: Grid 2-column nếu group có >4 links, single column nếu ≤4.
- **Typography**: Link description hỗ trợ (nếu có), icon aligned với label.
- **Active state**: Background màu `bg-accent/10 text-accent` với left-border 2px (dùng `border-l-2 border-primary/50`).
- **Shadow**: `shadow-xl border border-border/40`.

#### Accessiblity:
- Focus trap trong panel khi mở.
- Escape đóng panel và focus về trigger.
- Arrow keys navigation (giữ nguyên và cải tiến).

### 4. Nav Item Trigger (`nav-item-trigger.tsx`)

#### Visual updates:
- **Active indicator**: Sửa thành underline gradient thay vì solid primary.
- **Hover state**: Thêm `bg-accent/5` thay vì full accent.
- **Open state**: Khi menu mở, icon rotate nhẹ (dùng motion).
- **Font**: Dùng `font-label` family cho consistency.

### 5. Mobile Menu Overlay (`mobile-menu-overlay.tsx`)

#### Visual updates:
- **Animation**: Slide-in từ bên trái (drawer style) thay vì fade toàn màn hình. Dùng `motion.div` với `x: [-320, 0]` và backdrop overlay.
- **Overlay**: Semi-transparent backdrop (bg-black/30) + click-outside-close.
- **Header**: Giữ nguyên close button, thêm role label (Admin / Employee).
- **Groups**: Collapsible accordion với chevron animation.
- **Footer**: Theme toggle + logout button ở bottom của drawer.
- **Width**: Drawer 320px trên mobile, max-w-sm.

### 6. Animations & Transitions

Sử dụng `motion` (framer-motion) package đã có:

```tsx
// Mega menu open/close
<motion.div
  initial={{ opacity: 0, scale: 0.95, y: -8 }}
  animate={{ opacity: 1, scale: 1, y: 0 }}
  exit={{ opacity: 0, scale: 0.95, y: -8 }}
  transition={{ duration: 0.15, ease: "easeOut" }}
/>

// Mobile drawer
<motion.div
  initial={{ x: "-100%" }}
  animate={{ x: 0 }}
  exit={{ x: "-100%" }}
  transition={{ type: "spring", damping: 25, stiffness: 300 }}
/>

// Nav trigger chevron
<motion.span
  animate={{ rotate: isOpen ? 180 : 0 }}
  transition={{ duration: 0.2 }}
/>
```

### 7. Dark Mode Support

Dùng `next-themes` (đã có trong dependencies). Các component cần gọi `useTheme()` cho theme toggle button. Dark mode variants đã được định nghĩa trong `globals.css`, Tailwind dark class tự động hoạt động.

## Các component mới cần tạo

1. **`theme-toggle.tsx`** — Sun/Moon icon toggle button (nếu chưa có)
2. **`header-breadcrumbs.tsx`** — Breadcrumb strip embedded trong header (mobile collapse)

## Files cần sửa

| File | Loại thay đổi |
|------|---------------|
| `header-navigation.tsx` | Sửa cấu trúc, thêm motion, breadcrumb, scroll shadow |
| `header-utilities.tsx` | Thêm theme toggle, search box style, role badge, notification badge |
| `nav-item-trigger.tsx` | Cải tiến active/hover/open indicator, thêm motion |
| `mega-menu-panel.tsx` | Thêm motion animation, grid layout |
| `mobile-menu-overlay.tsx` | Đổi thành slide-in drawer, thêm collapsible groups |
| `app/(dashboard)/layout.tsx` | Có thể cần tweak padding/margin |

## Non-goals

- Không thay đổi nav config (`admin-nav-config.ts`, `ess-nav-config.ts`, `header-nav-config.ts`)
- Không thay đổi business logic (auth, role checking, route guards)
- Không thay đổi CommandBar / CommandPalette
- Không thêm real notification data — chỉ UI

## Acceptance Criteria

1. Header hiển thị đúng trên desktop (≥1024px), tablet (≥768px) và mobile (<768px)
2. Frosted glass effect hoạt động (backdrop-blur)
3. Mega menu animation mượt, không giật
4. Theme toggle chuyển dark/light mode thành công
5. Mobile drawer trượt từ trái ra, có backdrop click-outside
6. Scroll shadow xuất hiện khi scroll xuống
7. Breadcrumb xuất hiện trong header (có thể ẩn trên mobile)
8. Role badge trên avatar hoạt động (admin = đỏ, user = blue)
9. Tất cả test hiện tại vẫn pass
10. Keyboard navigation hoạt động đầy đủ (Tab, Escape, Arrow keys)
