# Tiêu chuẩn Quản trị Repo GitHub và Cấu trúc Tài liệu

status: accepted

Hệ thống tài liệu và quản trị dự án trên GitHub được chuẩn hóa toàn diện theo các tiêu chuẩn dự án Open-Source hàng đầu (chuẩn 10k+ stars). Cấu trúc bao gồm: `README.md` chính bằng Tiếng Anh chuyên nghiệp (kèm link `README.vi.md` tiếng Việt), file `ARCHITECTURE.md` chứa 3 sơ đồ Mermaid (System High-Level, Dual-KB RAG, Backbone Flow), bộ file Governance (`CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`), cùng hệ thống `.github/ISSUE_TEMPLATE/*.yml` (YAML Issue Forms) và `.github/PULL_REQUEST_TEMPLATE.md`.

## Các phương án đã cân nhắc

- **Chỉ duy trì README.md đơn giản bằng Tiếng Việt**: Bị loại vì không tạo được độ uy tín kỹ thuật cao đối với cộng đồng quốc tế, thiếu chuẩn mực đóng góp Open-Source và không minh họa được kiến trúc hệ thống rõ ràng.
- **Dùng Markdown Issue Template cũ (`.md`)**: Bị loại vì thiếu validation form, dễ tạo ra các issue trống hoặc thiếu thông tin môi trường/logs cần thiết cho việc debug.
- **Trực tiếp nhét toàn bộ chi tiết kiến trúc vào README**: Bị loại vì làm README bị phình quá to, gây khó theo dõi cho người xem lần đầu. Tách sơ đồ kiến trúc chuyên sâu vào `ARCHITECTURE.md` giúp giữ README gọn gàng, sắc nét.

## Hệ quả

- **Tăng độ uy tín (Credibility)**: Dự án mang diện mạo chuyên nghiệp chuẩn production, thu hút cộng đồng developer và doanh nghiệp tự host.
- **Đồng bộ hóa tài liệu (Governance)**: Quy định rõ ràng quy trình đóng góp code, chuẩn PR/Commit, chính sách báo cáo lỗ hổng bảo mật và cách trình bày issue.
- **Minh họa kiến trúc trực quan (Mermaid Diagrams)**: 3 sơ đồ Mermaid render native trên GitHub giúp bất kỳ ai cũng có thể nắm bắt nhanh luồng dữ liệu, ranh giới cách ly RAG và luồng tuyển dụng cốt lõi.
