# Embedding — chuyển từ model bundle sẵn sang endpoint do operator cấu hình

status: accepted

`vroom-embedding` không còn tự host model. Thay vì tải `AITeamVN/Vietnamese_Embedding_v2` (2.27 GB) từ HuggingFace lúc runtime và chạy qua `sentence-transformers` + `torch`, service trở thành một proxy mỏng gọi tới endpoint OpenAI-compatible `POST {EMBEDDING_API_BASE_URL}/embeddings` do operator chỉ định. Contract đối ngoại (`POST /embed`, `GET /health`) giữ nguyên, backend và kb-worker không phải sửa dòng nào.

Đây là việc đưa embedding về **cùng một mô hình cấu hình mà LLM đã dùng từ đầu**: `RECRUITMENT_LLM_BASE_URL` và `ASSISTANT_LLM_BASE_URL` vốn đã là endpoint OpenAI-compatible do operator trỏ (mặc định trong `backend/.env.example` là `http://127.0.0.1:20128/v1` — tức là local). Sau thay đổi này, cả ba năng lực AI đều theo một pattern duy nhất, và operator vẫn có thể trỏ embedding vào endpoint trong mạng nội bộ (vLLM, TEI, LocalAI) nếu cần dữ liệu không rời hạ tầng.

Quyết định này thay thế lựa chọn *"Dùng chung provider cho embedding"* đã bị loại trong [ADR 0007](./0007-knowledge-base-rag-architecture.md).

## Considered Options

- **Giữ nguyên self-host `AITeamVN/Vietnamese_Embedding_v2`**: chất lượng tiếng Việt tốt, dữ liệu không rời hạ tầng mặc định. Bị loại vì chi phí vận hành không tương xứng: image 8.52 GB (trong đó ~5–6 GB là các gói CUDA hoàn toàn vô dụng vì service chạy CPU thuần), lần chạy đầu phải tải 2.27 GB từ HuggingFace, và đã từng crash-loop vì lỗi mạng lúc tải model. Việc "self-host" ở đây thực chất bị ràng buộc vào một dependency mạng bên ngoài (HuggingFace Hub) ngay tại thời điểm khởi động.

- **Dual-mode (local hoặc remote, chọn qua env)**: giữ cả hai đường. Bị loại vì phải giữ `torch` + `sentence-transformers` trong image — tức là không thu được lợi ích chính về kích thước — đồng thời nhân đôi số đường code cần test và bảo trì. Operator muốn chạy model local vẫn làm được bằng cách trỏ `EMBEDDING_API_BASE_URL` vào một inference server local, mà không cần service này biết gì về `torch`.

- **Bỏ hẳn `vroom-embedding`, cho backend gọi thẳng provider**: bớt được một container. Bị loại vì sẽ phải sửa contract mà backend và kb-worker đang phụ thuộc, và phải nhân bản logic batching/retry/validate dimension ở phía caller. Giữ service làm một seam giúp việc đổi provider về sau chỉ là đổi env, không đụng vào backend.

- **Model đa ngôn ngữ khác qua cùng cơ chế**: không phải một lựa chọn loại trừ — chính là điều thiết kế này cho phép. `EMBEDDING_MODEL_NAME` giờ là tên model theo cách provider định danh, đổi được mà không phải build lại image.

## Consequences

- **Image từ 8.52 GB xuống 244 MB** (giảm ~97%), bỏ `torch` và `sentence-transformers` khỏi `requirements.txt`. Không còn volume `embedding-model-cache`, không còn bước tải model lúc khởi động. `deploy.resources` hạ từ 3072M/2 CPU xuống 256M/0.5 CPU, `healthcheck.start_period` từ 120s xuống 15s.

- **`EMBEDDING_API_BASE_URL` và `EMBEDDING_MODEL_NAME` không có giá trị mặc định — bắt buộc cấu hình tường minh.** Repo không chọn nhà cung cấp thay operator: đây là repo MIT public, một default trỏ vào vendor thương mại sẽ khiến người clone về mặc nhiên gửi nội dung tài liệu tới một bên họ không chọn, đồng thời mục ngay khi vendor đó đổi endpoint hoặc đóng cửa. Nguyên tắc này áp cho cả hai biến: `text-embedding-v4` là tên model riêng của một nhà cung cấp, không phải một mặc định trung lập. Cách này cũng khớp với `RECRUITMENT_LLM_BASE_URL` / `ASSISTANT_LLM_BASE_URL` vốn đã bắt operator tự trỏ.

- **Thiếu cấu hình được báo gộp trong một thông báo, không nhỏ giọt.** `validate()` gom toàn bộ lỗi rồi mới raise, đánh số từng mục kèm ví dụ cho cả hai đường (endpoint nội bộ và API hosted), nên operator không sửa một biến để rồi restart mới phát hiện thiếu biến thứ hai. Riêng `EMBEDDING_MODEL_NAME` để trống đáng chặn ở startup vì nếu không, nó sẽ lộ ra dưới dạng 502 "model not found" từ upstream — một lỗi cấu hình bị nguỵ trang thành lỗi mạng, tốn thời gian debug sai hướng. `EMBEDDING_API_KEY` vẫn được phép để trống vì endpoint nội bộ thường không cần xác thực.

- **Nội dung chunk tài liệu KB và CV đi tới endpoint mà operator cấu hình.** Nếu đó là API bên thứ ba thì dữ liệu rời hạ tầng — điều operator cần biết và tự quyết; nếu chính sách dữ liệu không cho phép, trỏ `EMBEDDING_API_BASE_URL` vào endpoint nội bộ, service không phân biệt hai trường hợp. Phần self-host thực sự (PostgreSQL, MinIO, toàn bộ deployment, single-tenant) không thay đổi.

- **Thêm một phụ thuộc runtime vào tính sẵn sàng của provider.** Trước đây, sau khi model đã tải xong thì ingestion chạy được kể cả khi mất mạng. Giờ mỗi lần embed đều cần mạng. Service bù lại bằng retry có backoff cho lỗi tạm thời (timeout, 5xx, 429) và không retry cho lỗi tất định (401/403), đồng thời map lỗi upstream thành status có nghĩa (504 khi timeout, 429 khi bị rate-limit, 502 cho phần còn lại) thay vì nuốt.

- **Vector cũ và vector mới không tương thích ngữ nghĩa.** Tại thời điểm thay đổi, `hr_knowledge_base_chunks` và `employee_knowledge_base_chunks` đều rỗng (0 row) nên không cần migration. Nếu về sau đổi provider hoặc đổi model, **phải re-index toàn bộ tài liệu** — vector sinh bởi hai model khác nhau không so sánh được với nhau, dù cùng số chiều.

- **Số chiều được kiểm tra lúc khởi động, fail fast — ở hai tầng.** Cột pgvector là `Vector(1024)` cố định. (1) Nếu `EMBEDDING_DIMENSIONS` khác 1024, service chết ngay trước cả khi gọi provider: đây là cạm bẫy dễ bỏ sót vì provider mặc định **có tôn trọng** tham số `dimensions`, nên nó sẽ ngoan ngoãn trả về đúng số chiều được yêu cầu — vector khớp provider nhưng không khớp cột DB, và lỗi chỉ lộ ra ở tầng database khi kb-worker insert. (2) Nếu provider trả về số chiều khác `EMBEDDING_DIMENSIONS` (trường hợp provider **bỏ qua** tham số này), probe lúc startup phát hiện và service cũng chết ngay.

- **Timeout và retry được chọn theo ngân sách của caller.** `retrieval_service.py` gọi service với timeout 30s. Mặc định `EMBEDDING_TIMEOUT=12` và `EMBEDDING_MAX_RETRIES=1` giữ tổng thời gian xấu nhất (12 + 0.5 + 12 ≈ 24.5s) nằm dưới ngưỡng đó — nếu vượt, caller đã bỏ cuộc trước khi service kịp trả lời, và toàn bộ thời gian retry là lãng phí.

- **`EMBEDDING_API_KEY` là tuỳ chọn, `dimensions` có thể tắt.** Endpoint chạy nội bộ thường không cần key (khi để trống, service không gửi header `Authorization`), và một số server OpenAI-compatible — điển hình là vLLM với model không hỗ trợ Matryoshka — trả 400 nếu nhận tham số `dimensions`; đặt `EMBEDDING_SEND_DIMENSIONS=false` cho trường hợp đó. Hai điều này là điều kiện cần để đường "trỏ vào endpoint nội bộ" thực sự dùng được, chứ không chỉ tồn tại trên giấy.

- **Provider mặc định giới hạn 10 input mỗi request**, trong khi backend gửi toàn bộ chunk của một tài liệu trong một lần gọi. Service tự chia thành các batch `EMBEDDING_BATCH_SIZE` (mặc định 10), gọi tuần tự và ghép lại đúng thứ tự đầu vào. Đổi provider có giới hạn khác thì chỉnh env, không sửa code.

## Điều kiện nên đảo ngược

Quay lại mô hình bundle model vào image khi có ít nhất một trong các điều sau:

- Chính sách dữ liệu bắt buộc nội dung tài liệu không được rời hạ tầng **và** không thể dựng nổi một inference server nội bộ (khi đó cách nhẹ hơn vẫn là trỏ `EMBEDDING_API_BASE_URL` vào nội bộ, không phải nhét model trở lại image).
- Chi phí gọi API vượt chi phí vận hành GPU/CPU cho khối lượng ingestion thực tế.
- Chất lượng retrieval tiếng Việt của model provider tụt rõ rệt so với `AITeamVN/Vietnamese_Embedding_v2` khi đo trên tập tài liệu thật.
- Yêu cầu ingestion phải chạy được trong môi trường air-gapped.

Nếu đảo ngược, cần nhớ: image sẽ lại nặng ~8.5 GB nếu cài `torch` mặc định (nên dùng bản CPU-only để tránh ~5–6 GB gói CUDA thừa), và **toàn bộ tài liệu phải được re-index**.
