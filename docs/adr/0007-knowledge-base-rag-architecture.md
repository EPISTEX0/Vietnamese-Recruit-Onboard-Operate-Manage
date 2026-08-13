# Knowledge Base — kiến trúc RAG cho tài liệu nội bộ

status: accepted

Hệ thống bổ sung RAG retrieval cho AI Assistant và Employee Assistant thông qua hai Knowledge Base riêng biệt về mặt vật lý (`hr_knowledge_base` và `employee_knowledge_base`), thay vì một KB duy nhất với visibility filter. Tài liệu (PDF, DOCX) do HR upload, parse bất đồng bộ qua ARQ worker, chunk + embed bằng `AITeamVN/Vietnamese_Embedding_v2` chạy trong Docker service riêng, lưu vector vào pgvector. Kết quả retrieval được inject ẩn vào system prompt qua ContextBuilder (không phải Read-Tool ở MVP). HR Assistant thấy citation nguồn, Employee Assistant chỉ thấy câu trả lời.

## Considered Options

- **Một KB + visibility filter (WHERE visibility = 'all')**: đơn giản hơn về mặt DB, chỉ một bảng documents và một bảng chunks. Bị loại vì rủi ro leak dữ liệu HR-only cho Employee nếu một điểm code quên filter. Hai KB riêng biệt tạo defense-in-depth: Employee không thể chạm vào HR KB dù code có bug. HR truy vấn cả hai KB (union), Employee chỉ truy vấn Employee KB.

- **Qdrant / ChromaDB**: vector DB chuyên dụng, performance cao, built-in RAG features. Bị loại cho MVP vì thêm service vào docker-compose và chi phí vận hành. pgvector đủ sức cho vài trăm tài liệu HR, dùng chung PostgreSQL hiện có, backup/restore đơn giản. Có thể migrate lên Qdrant sau nếu cần.

- **Retrieval là Read-Tool**: LLM chủ động gọi `search_knowledge_base` tool khi cần. Bị loại cho MVP vì tăng latency tool-calling loop và phình system prompt. Context injection ẩn cover 80-90% nhu cầu. Read-Tool fallback sẽ được cân nhắc ở giai đoạn sau.

- **Ingestion đồng bộ**: parse + embed ngay trong request HTTP. Bị loại vì embedding model local có thể chậm (10-50 trang), gây timeout và UX kém. Ingestion bất đồng bộ qua ARQ worker với trạng thái document (pending → processing → ready/error) cho phép upload ngay và xử lý sau.

- **Dùng chung provider cho embedding**: tận dụng endpoint OpenAI-compatible đã cấu hình. Bị loại vì `AITeamVN/Vietnamese_Embedding_v2` cho chất lượng tiếng Việt tốt hơn đáng kể và có thể chạy local, không gửi dữ liệu tài liệu nội bộ ra ngoài. Embedding model chạy trong Docker service riêng, không phụ thuộc vào provider chat.

  > **Đã thay đổi.** Lựa chọn này về sau được chấp nhận — xem [ADR 0012](./0012-operator-configured-embedding-endpoint.md). `vroom-embedding` giờ proxy tới endpoint OpenAI-compatible do operator cấu hình; phần còn lại của ADR này (hai KB tách biệt, pgvector, ingestion bất đồng bộ, context injection) vẫn nguyên giá trị.

## Consequences

- **Thêm 1 Docker service** (`vroom-embedding`) vào docker-compose, tăng RAM usage ~1-2GB cho model embedding tiếng Việt.
- **Thêm extension pgvector** cho PostgreSQL — migration không reversible dễ dàng nếu muốn chuyển sang vector DB khác, nhưng dữ liệu vector không phải dữ liệu nghiệp vụ chính nên rủi ro thấp.
- **Tài liệu HR-only được bảo vệ ở tầng vật lý** — hai bảng documents và chunks riêng cho mỗi KB. Khi Employee Assistant query, chỉ search trong `employee_knowledge_base_chunks`. Không có code path nào có thể vô tình leak.
- **Không có versioning và approval workflow ở MVP** — HR upload là publish ngay, document mới ghi đè document cũ. Đây là deliberate simplification; nếu cần versioning sẽ thêm ở giai đoạn sau.
- **Citation chỉ cho HR** — Employee không thấy nguồn tài liệu, giữ UX đơn giản. Có thể thêm citation cho Employee sau nếu có nhu cầu.
