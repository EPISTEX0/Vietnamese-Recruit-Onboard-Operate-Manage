# Spec: Tối ưu Docker Compose & Cấu hình Self-Host Vroom HR

## 1. Tổng quan & Mục đích
Tài liệu này xác định thông số kỹ thuật chuẩn hóa để nâng cấp hệ thống Docker Compose và môi trường Self-Host của Vroom HR lên tiêu chuẩn Production-ready dành cho doanh nghiệp Việt Nam.

- **Mục tiêu**: Đóng gói 1-Command (`docker compose up -d`) chạy mượt trên máy chủ / VPS cấu hình 4GB – 8GB RAM, 2–4 vCPU.
- **Phạm vi**: 9 services (`postgres`, `redis`, `minio`, `vroom-embedding`, `backend`, `frontend`, `gmail-worker`, `onboarding-worker`, `kb-worker`).

---

## 2. Thiết kế Cấu hình (.env & Architecture)

### 2.1 Tập trung file `.env.example` tại Root
Tất cả cấu hình hệ thống được gom vào file `.env.example` tại root với giá trị mặc định an toàn:
- `VROOM_HOST`, `VROOM_PORT`, `BACKEND_PORT`
- `AUTH_JWT_SECRET_KEY`, `AUTH_PASSWORD_SALT`
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT`
- `REDIS_PASSWORD`, `REDIS_PORT`
- `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_API_PORT`, `MINIO_CONSOLE_PORT`
- `EMBEDDING_MODEL_NAME`, `EMBEDDING_PORT`, `HF_TOKEN`
- Biến Google Workspace Integration (Client ID, Client Secret, Redirect URI)

### 2.2 Mô hình Mạng (Network Isolation)
Phân chia 2 Docker Networks:
1. `vroom-public-net`: Kết nối `frontend` và `backend` (phục vụ traffic người dùng).
2. `vroom-internal-net` (`internal: true`): Kết nối `postgres`, `redis`, `minio`, `vroom-embedding`, `backend`, `workers`. Mạng này cô lập hoàn toàn, không lộ port DB/Redis ra bên ngoài.

---

## 3. Quy chuẩn Khởi động & Tài nguyên (Startup & Resource Allocation)

### 3.1 Sửa lỗi Healthcheck & DAG Khởi động
- **MinIO**: Chuyển healthcheck từ `curl` (đã bị gỡ) sang command `mc ready local`.
- **vroom-embedding**: Tách khỏi dependency bắt buộc (`service_healthy`) của `backend`. Chuyển sang gọi Lazy-load. Core Web HR sẽ boot sẵn sàng trong `< 30s`.
- **Backend & Frontend**: Bổ sung `healthcheck` native để phục vụ lệnh `docker compose up -d --wait`.

### 3.2 Phân bổ Giới hạn Tài nguyên (Resource Limits)
- `postgres`: Memory limit 2048M, Reservation 512M, CPU 2.0. Shared buffers 512MB.
- `redis`: Memory limit 384M, Maxmemory policy `allkeys-lru`.
- `minio`: Memory limit 512M.
- `vroom-embedding`: Memory limit 3072M, Reservation 1024M, CPU 2.0.
- `backend`: Memory limit 1024M, CPU 1.5.
- `frontend`: Memory limit 768M, CPU 1.0.
- `workers`: 256M – 512M/worker.
- **Log Rotation**: `max-size: 20m`, `max-file: 3` cho tất cả container.

---

## 4. Tối ưu Runtime Dockerfile

### 4.1 Backend (`backend/Dockerfile`)
- Sửa lỗi build fail do thiếu thư mục `backend/config/` (tự tạo sẵn dir trong Dockerfile hoặc repo).
- Hỗ trợ biến môi trường `WEB_CONCURRENCY` (mặc định 2 workers) cho uvicorn.

### 4.2 Frontend (`frontend/Dockerfile`)
- Bật `output: 'standalone'` trong Next.js config và cập nhật Dockerfile sản xuất để giảm dung lượng Docker Image xuống `< 180MB` và RAM tiêu thụ `< 120MB`.
