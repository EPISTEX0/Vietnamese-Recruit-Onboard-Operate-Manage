import { StepOptions } from "shepherd.js";

export const WALKTOUR_STEPS: StepOptions[] = [
  {
    id: "brand",
    attachTo: { element: ".walktour-brand", on: "right" },
    title: "Chào mừng đến với Vroom HR",
    text: "Chào mừng bạn đến với Vroom HR — hệ thống quản trị nhân sự toàn diện dành cho doanh nghiệp Việt Nam.",
    buttons: [
      { text: "Bỏ qua", action: () => (window as any)._walktour?.cancel() },
      { text: "Tiếp", action: () => (window as any)._walktour?.next() },
    ],
  },
  {
    id: "dashboard",
    attachTo: { element: ".walktour-nav-dashboard", on: "right" },
    title: "Dashboard",
    text: "Dashboard tổng quan: xem metrics tuyển dụng, tình trạng hệ thống, và nhật ký hoạt động gần đây.",
    buttons: [
      { text: "Trước", action: () => (window as any)._walktour?.back() },
      { text: "Bỏ qua", action: () => (window as any)._walktour?.cancel() },
      { text: "Tiếp", action: () => (window as any)._walktour?.next() },
    ],
  },
  {
    id: "recruitment",
    attachTo: { element: ".walktour-nav-recruitment", on: "right" },
    title: "Tuyển dụng",
    text: "Tuyển dụng: Inbox nhận email ứng viên tự động, quản lý hồ sơ, Job Openings, lịch phỏng vấn và báo cáo metrics.",
    buttons: [
      { text: "Trước", action: () => (window as any)._walktour?.back() },
      { text: "Bỏ qua", action: () => (window as any)._walktour?.cancel() },
      { text: "Tiếp", action: () => (window as any)._walktour?.next() },
    ],
  },
  {
    id: "employees",
    attachTo: { element: ".walktour-nav-employees", on: "right" },
    title: "Nhân sự",
    text: "Nhân sự: Onboarding nhân viên mới, danh sách nhân viên, và xử lý yêu cầu từ nhân viên.",
    buttons: [
      { text: "Trước", action: () => (window as any)._walktour?.back() },
      { text: "Bỏ qua", action: () => (window as any)._walktour?.cancel() },
      { text: "Tiếp", action: () => (window as any)._walktour?.next() },
    ],
  },
  {
    id: "attendance",
    attachTo: { element: ".walktour-nav-attendance", on: "right" },
    title: "Chấm công & Bảng lương",
    text: "Chấm công & Bảng lương: theo dõi giờ làm, quản lý chấm công và tạo phiếu lương cho nhân viên.",
    buttons: [
      { text: "Trước", action: () => (window as any)._walktour?.back() },
      { text: "Bỏ qua", action: () => (window as any)._walktour?.cancel() },
      { text: "Tiếp", action: () => (window as any)._walktour?.next() },
    ],
  },
  {
    id: "system",
    attachTo: { element: ".walktour-nav-system", on: "right" },
    title: "Hệ thống",
    text: "Hệ thống: Knowledge Base nội bộ cho nhân viên, kết nối Gmail tuyển dụng, và cài đặt tổ chức.",
    buttons: [
      { text: "Trước", action: () => (window as any)._walktour?.back() },
      { text: "Bỏ qua", action: () => (window as any)._walktour?.cancel() },
      { text: "Tiếp", action: () => (window as any)._walktour?.next() },
    ],
  },
  {
    id: "assistant",
    attachTo: { element: ".walktour-assistant", on: "right" },
    title: "Trợ lý AI",
    text: "Trợ lý AI: hỏi đáp thông minh — nhân viên có thể hỏi về nội quy, chính sách; HR có thể tra cứu dữ liệu nhanh.",
    buttons: [
      { text: "Trước", action: () => (window as any)._walktour?.back() },
      { text: "Kết thúc", action: () => (window as any)._walktour?.complete() },
    ],
  },
];
