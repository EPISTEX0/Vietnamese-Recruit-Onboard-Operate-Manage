# Design system — typography tiếng Việt và hợp nhất về một thư mục frontend

status: accepted

Design system **AI Studio** vẫn là nguồn sự thật cho UI, nhưng hai chi tiết mà [ADR 0006](./0006-ai-studio-design-system.md) mô tả đã không còn đúng với code đang chạy. ADR này ghi lại **hiện trạng đã ship**, không đề xuất thay đổi mới.

**Font sans đổi từ Inter sang Be Vietnam Pro.** Be Vietnam Pro được thiết kế cho tiếng Việt; Inter đặt dấu chật, tệ nhất ở chữ hoa có dấu (Ế, Ộ, Ữ) — nơi dấu phụ và dấu thanh phải chồng lên nhau trong chiều cao vốn đã hết chỗ. Sản phẩm này có UI tiếng Việt là mặc định, nên chất lượng đặt dấu không phải chi tiết thẩm mỹ mà là chất lượng đọc của phần lớn văn bản trên màn hình. **JetBrains Mono giữ nguyên** cho code/mã/audit: nội dung đó là ASCII, không có nhu cầu tiếng Việt, và không có lý do gì để đổi.

**Chỉ còn một thư mục frontend.** ADR 0006 mô tả hai thư mục — `vroom-hr/` là frontend chính chạy AI Studio, `frontend/` là backup legacy chạy Heritage trên Next 14 + Tailwind 3. Tiền đề đó đã chết: `vroom-hr/` **không còn tồn tại** trong repo, và app AI Studio giờ nằm ở `frontend/`. Cái tên `vroom-hr` chỉ còn sống trong trường `name` của `frontend/package.json` — đó là tên package, không phải đường dẫn, và không nên đọc như bằng chứng rằng thư mục cũ còn.

Phần còn lại của ADR 0006 **vẫn nguyên giá trị**: nền slate, một accent **indigo** duy nhất cho action và icon, Tailwind v4 CSS-first (`@import "tailwindcss"`, không có `tailwind.config.js` truyền thống), card bo `rounded-2xl` với shadow mềm.

## Considered Options

- **Giữ Inter**: font sẵn có, quen thuộc, metric tốt cho UI tiếng Anh. Bị loại vì đặt dấu tiếng Việt chật — vấn đề lộ rõ nhất ở chữ hoa có dấu, vốn xuất hiện thường xuyên trong tiêu đề và label của một UI tiếng Việt. Đây là quyết định của chủ repo dựa trên chất lượng hiển thị thực tế, không phải tối ưu theo số đo.

- **Be Vietnam Pro (được chọn)**: được thiết kế cho tiếng Việt ngay từ đầu, có đủ weight cần cho hệ thống (400/500/600/700) và phục vụ qua `next/font/google` nên self-host, không phát request ra ngoài lúc runtime.

- **Đổi luôn font mono**: không làm. Code, mã lỗi và audit log là ASCII; đổi mono chỉ tạo rủi ro về alignment mà không giải quyết vấn đề nào đang có.

- **Sửa ADR 0006 tại chỗ**: bị loại. ADR là bản ghi lịch sử của quyết định *tại thời điểm ra quyết định*; sửa đè sẽ xoá mất ngữ cảnh vì sao lúc đó lại chọn Inter và vì sao lúc đó có hai thư mục. Cách xử lý theo precedent sẵn có của repo — [ADR 0007](./0007-knowledge-base-rag-architecture.md) được [ADR 0012](./0012-operator-configured-embedding-endpoint.md) thay thế một phần qua blockquote trỏ tới — là viết ADR mới rồi cắm pointer vào ADR cũ.

## Consequences

- **Đổi font là đổi một import và một variable, không đụng component.** `frontend/app/layout.tsx:2` import `Be_Vietnam_Pro` và `JetBrains_Mono` từ `next/font/google`; `:9`–`:12` gán `variable: '--font-sans'`, `:16`–`:18` gán `variable: '--font-mono'`; `:31` gắn cả hai variable lên `<html>`. Component chỉ dùng class `font-sans` / `font-mono`, nên không component nào biết tên font cụ thể.

- **`subsets` có `'vietnamese'`** (`layout.tsx:10`) — nếu thiếu, `next/font` sẽ không subset ký tự tiếng Việt và dấu sẽ hỏng hoặc rơi về fallback. Đây là điều kiện cần để lựa chọn font ở trên thực sự có tác dụng, không chỉ đúng trên giấy.

- **Favicon SVG không theo font này.** `frontend/app/icon.svg` là file standalone, trình duyệt render biệt lập nên `next/font` không với tới; nó dùng `system-ui, sans-serif` để render nhất quán cho mọi người thay vì phụ thuộc vào font đã cài sẵn trên OS.

- **ADR 0006 là file duy nhất trong repo còn trích đường dẫn `vroom-hr/`** — và cố ý giữ như vậy. Các đường dẫn `vroom-hr/app/layout.tsx`, `vroom-hr/app/globals.css`, `vroom-hr/components/operate.tsx` trong phần Source evidence của ADR đó không còn resolve; chúng là bằng chứng *tại thời điểm ra quyết định*, không phải chỉ dẫn để đi theo. Ai cần file tương ứng hôm nay thì bỏ tiền tố `vroom-hr/` và tìm dưới `frontend/`.

- **Heritage không còn tồn tại trong code.** Không còn chỗ nào ngoài ADR 0006 nhắc tới terracotta `#B8422E`, Fraunces, Public Sans hay Space Grotesk — kể cả `frontend/src/__tests__/heritage-compliance.test.ts` mà ADR 0006 trích cũng đã không còn. Ranh giới "Heritage vs AI Studio" mà ADR 0006 dựng lên giờ không còn hai vế; chỉ còn AI Studio.

- **Không có ADR index phải cập nhật.** Citation trong repo đều point-to-point: `DESIGN.md:56` và `DESIGN.md:95` trỏ thẳng tới ADR 0006, người đọc tới đó rồi theo blockquote sang ADR này. Đổi lại, pointer trong ADR 0006 là thứ duy nhất giữ cho chuỗi đó không đứt — nếu về sau có ADR thay thế tiếp, phải cắm pointer theo đúng cách này.
