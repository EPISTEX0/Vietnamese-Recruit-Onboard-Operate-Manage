# Tích hợp Google Workspace cấp Organization

Vroom HR dùng một Google Workspace account dùng chung để nhận CV qua Gmail, gửi email đã được HR xác nhận và quản lý lịch phỏng vấn. Kết nối thuộc về Organization, không thuộc về HR đã thực hiện consent; lựa chọn này tránh gián đoạn khi HR thay đổi và phù hợp mô hình một deployment chỉ phục vụ một Organization.

## Trạng thái

accepted

## Quyết định

### Quyền sở hữu và cấu hình

- Mỗi Organization có tối đa một **Organization Google Connection** hoạt động, đại diện cho một **Organization Shared Google Account** như `recruitment@company.vn`.
- Mọi HR có thể xem trạng thái, Connect/Reconnect và Disconnect; mọi thay đổi đều được audit. HR thực hiện consent nhưng không sở hữu token.
- Mỗi Organization tự sở hữu Google Cloud project và OAuth Web Client. Consent screen là **Internal**, project thuộc Google Workspace organization và Gmail API/Calendar API được bật.
- HR nhập OAuth client ID/client secret trong cấu hình deployment. Client secret và token chỉ được xử lý ở backend.
- Email trả về từ Google phải được backend xác minh và thuộc một `allowed email domain` của Organization. `hd` hoặc email do frontend gửi lên không phải bằng chứng xác thực.
- Chỉ hỗ trợ Google Workspace trong phiên bản đầu; Gmail cá nhân, nhiều Google account, domain-wide delegation và OAuth Client dùng chung do Vroom vận hành nằm ngoài scope.

### OAuth và secrets

Sử dụng Authorization Code flow cho web server, `access_type=offline`, state ngẫu nhiên dùng một lần gắn với HR session, redirect URI khớp tuyệt đối và chỉ chấp nhận callback tương ứng. Reconnect phải xử lý trường hợp Google không trả refresh token mới mà không ghi đè refresh token còn hợp lệ bằng giá trị rỗng.

Scopes bắt buộc:

```text
openid
email
https://www.googleapis.com/auth/gmail.readonly
https://www.googleapis.com/auth/gmail.send
https://www.googleapis.com/auth/calendar.events
https://www.googleapis.com/auth/calendar.calendarlist.readonly
```

Không yêu cầu `gmail.modify`, `mail.google.com`, Calendar ACL, Drive hoặc Contacts. Vì vậy trạng thái phân loại được lưu trong Vroom; Vroom không tạo hoặc sửa Gmail label.

Access token, refresh token và OAuth client secret được mã hóa bằng authenticated encryption (AES-256-GCM) trước khi lưu. Deployment operator cung cấp khóa riêng qua secret manager/environment, tách khỏi database password và JWT signing key. Encrypted payload lưu `format_version`, nonce và `key_version`; decryptor phải đọc được phiên bản trước trong suốt key rotation. Secrets không xuất hiện trong API response, log, telemetry hoặc debug backup; mất toàn bộ khóa hợp lệ buộc HR Reconnect.

Trạng thái quyền của connection là:

```text
disconnected → connected → reauthorization_required
```

Không có capability health riêng cho `gmail_ingestion`, `gmail_sending` hay `calendar_sync`; chỉ connection có trạng thái. Lỗi quota/network tạm thời khi ingest được log và tự retry ở lần poll kế tiếp mà không đổi trạng thái connection; grant không còn dùng được — bị Google từ chối (revoke, `invalid_grant`, hoặc lỗi xác thực 4xx khi refresh/fetch/gửi) hoặc bị hệ thống chủ động thu hồi — mới chuyển connection sang `reauthorization_required`.

### Gmail ingestion

- ARQ worker poll Gmail mỗi 1–5 phút. Lần kết nối đầu chỉ thiết lập `history_id` baseline hiện tại và không tự nhập email cũ.
- Các lần sau dùng Gmail History API để xử lý email mới trong `INBOX`. `gmail_message_id` là khóa idempotency trong phạm vi connection/account; attachment có checksum để chống xử lý trùng ở Backbone Flow. Gmail cursor thuộc **Organization Google Connection**, không thuộc HR/User; reconnect hoặc đổi shared account phải thiết lập baseline mới.
- Nếu `startHistoryId` không còn hợp lệ và Gmail trả 404, worker xóa cursor cùng local ingestion state chưa hoàn tất rồi thực hiện full sync mới theo phạm vi dữ liệu Vroom thực sự cần (INBOX và cửa sổ thời gian có giới hạn), sau đó lưu baseline mới. Đây là full sync phục hồi theo contract của Gmail, không phải quét mailbox không giới hạn.
- HR có action **Import previous emails** riêng, chọn cửa sổ 7 hoặc 30 ngày, xem preview số lượng, theo dõi tiến độ và có thể dừng. Import không làm hỏng cursor đồng bộ email mới.
- Chỉ lưu message/thread ID, sender, subject, received time và metadata tối thiểu. Nội dung dùng để phân loại là dữ liệu tạm; chỉ attachment CV được chọn cho Backbone Flow mới được lưu bền vững.
- AI Automation phân loại `cv/partner/event/internal/other`; chỉ `cv` đi tiếp vào Backbone Flow. Disconnect xóa token và cursor nhưng không xóa Candidate/CV đã nhập hợp lệ.

### Gmail sending

- Nội dung do AI Assistant tạo luôn là Draft Action. HR phải xem preview và xác nhận trước khi backend gọi Gmail send endpoint; LLM không được nhận write-tool.
- Mỗi lần gửi có idempotency key và vòng đời `pending → sending → sent/failed` để retry không gửi trùng.
- Calendar invitation/update/cancellation do Google Calendar gửi; Gmail không gửi email lịch trùng lặp. Email tuyển dụng khác vẫn dùng Gmail và cần HR xác nhận.

### Interview và Google Calendar

**Interview** là entity riêng: một Candidate có nhiều Interview. Không tiếp tục dùng một bộ calendar field duy nhất trên Candidate.

Interview có vòng đời:

```text
scheduled → completed
          → cancelled
```

Reschedule cập nhật cùng Interview và cùng Google event. Nếu buổi cũ bị hủy rồi thay bằng buổi khác, giữ Interview cũ là `cancelled` và tạo Interview mới. `completed` do HR xác nhận; không suy ra từ thời gian đã trôi qua. Interview/RSVP/Calendar change không tự động thay đổi Candidate pipeline; nếu Candidate cần chuyển sang `interview_scheduled`, đó phải là quyết định nghiệp vụ tường minh trong HR command, không phải side effect của đồng bộ Google.

Mỗi Interview chứa tối thiểu Candidate, `round_name`, start/end UTC, timezone IANA, mode (`google_meet`, `in_person`, `custom_link`) và một hay nhiều interviewer. Candidate là attendee bắt buộc; interviewer có thể là Employee dùng email công việc hoặc người ngoài dùng tên + email. Organization Shared Google Account là organizer, không phải interviewer. Không tạo event nếu attendee bắt buộc thiếu email hợp lệ.

HR chọn một calendar tuyển dụng chuyên biệt khi cấu hình connection; backend lưu `calendar_id` và không mặc định dùng `primary`. Khi HR xác nhận tạo/đổi/hủy Interview, Calendar API dùng `sendUpdates=all`. Google Meet là tùy chọn theo Interview và được tạo bằng `conferenceDataVersion=1` với `requestId` idempotent. Event không recurring trong phiên bản đầu; timezone mặc định lấy từ Organization nhưng HR có thể đổi theo Interview.

Mỗi Interview liên kết tối đa một Google event và lưu `calendar_id`, `event_id`, `etag`, Google `updated` và sync metadata. Calendar sync dùng `syncToken`, xử lý phân trang và deleted events; token 410 buộc bounded full sync trên calendar đã chọn. Chỉ event deletion/cancelled rõ ràng mới được chuyển Interview thành `cancelled`; không suy ra event bị xóa chỉ vì không xuất hiện trong full sync hữu hạn. RSVP (`needsAction/accepted/declined/tentative`) chỉ cung cấp thông tin và cảnh báo cho HR, không tự hủy Interview.

Mọi update/delete gửi `If-Match` với `etag`. Phản hồi 412 tạo conflict thay vì last-write-wins và đóng băng write tự động. **Giữ Google** đọc phiên bản mới rồi cập nhật local; **ghi đè từ Vroom** phải là xác nhận rõ của HR, đọc ETag hiện tại và gửi update mới. Nếu lại gặp 412 thì tạo conflict mới. Mọi resolve conflict được audit. Event bị xóa trực tiếp trên Google làm Interview `cancelled` nhưng không đổi Candidate pipeline.

Không cho xóa/anonymize Candidate khi còn Interview `scheduled`; HR phải hủy Interview trước để Calendar gửi cancellation. Interview lịch sử sau đó được xóa hoặc anonymize cùng Candidate theo chính sách retention.

## Các phương án đã cân nhắc

- **OAuth connection theo từng HR:** loại bỏ vì token và calendar sẽ phụ thuộc vòng đời việc làm của HR, trong khi Gmail/Calendar phục vụ Organization.
- **OAuth Client trung tâm do Vroom vận hành:** loại bỏ vì deployment self-hosted sẽ phụ thuộc control plane trung tâm và việc quản lý redirect URI/verification chung.
- **Google service account với domain-wide delegation:** loại bỏ vì cấp quyền quá rộng và cần Workspace administrator impersonation; user consent cho shared account có boundary nhỏ hơn.
- **Gmail push notification qua Pub/Sub ngay từ đầu:** hoãn vì buộc deployment có public HTTPS, Pub/Sub và cơ chế gia hạn watch; incremental polling phù hợp self-hosted hơn và adapter vẫn có thể thay sau.
- **`gmail.modify` để gắn label:** loại bỏ để giảm quyền; phân loại và processing state thuộc Vroom.
- **Một lịch phỏng vấn lưu trực tiếp trên Candidate:** loại bỏ vì không biểu diễn được nhiều vòng, lịch sử hủy/thay thế và conflict độc lập.
- **Calendar `primary` của HR:** loại bỏ vì sai ownership và làm integration phụ thuộc HR.
- **Last-write-wins khi đồng bộ Calendar:** loại bỏ vì có thể âm thầm ghi đè thay đổi của interviewer/HR.

## Hệ quả và kế hoạch chuyển đổi

Thiết kế hiện tại đang gắn OAuth grant, Gmail cursor và email với `user_id`; worker poll mọi HR có grant hợp lệ. Calendar adapter dùng token của HR, ghi vào `calendars/primary`, luôn gửi update và Candidate chỉ có `calendar_event_id`, `interview_start_at`, `interview_timezone`. Gmail hiện còn yêu cầu `gmail.modify`, tự khởi tạo label và lần poll đầu tự nhập một cửa sổ email cũ. Các hành vi này phải được thay thế, không được giữ song song như một mô hình ownership thứ hai.

Thứ tự triển khai đề xuất:

1. Thêm persistence cho Organization Google Connection, Gmail/Calendar cursors và audit; chuyển OAuth callback/status/worker từ `user_id` sang Organization singleton. Giữ `connected_by_hr_id` chỉ để audit. Dùng encrypted-payload format có version; các grant HR cũ sẽ được revoke thay vì giả định có thể decrypt/migrate sang ownership mới.
2. Thay scope hiện tại bằng bộ scope đã chốt; bỏ LabelService/write-label; thêm kiểm tra Internal Workspace account và allowed domain.
3. Chuyển Gmail worker sang baseline-no-backfill, incremental History API và import job riêng; giữ pipeline phân loại/CV hiện có sau boundary ingestion.
4. Thêm Interview, Interview participant và calendar sync metadata; chuyển Calendar adapter từ `primary`/HR token sang selected `calendar_id`/Organization token, thêm syncToken, RSVP, ETag conflict và optional Meet.
5. Backfill một Interview `scheduled` cho mỗi Candidate đang có calendar fields. Nếu event cũ không truy cập được bằng Organization Shared Google Account, giữ lifecycle status của Interview (`scheduled`/`completed`/`cancelled`) và đặt cờ `needs_relink=true`; yêu cầu HR hủy/tạo lịch thay thế trước khi ghi Calendar, không âm thầm tạo event trùng.
6. Chuyển toàn bộ read/write sang Interview trong cùng release, kiểm chứng migration rồi xóa calendar fields trên Candidate. Không duy trì dual-write dài hạn.
7. Sau migration, vô hiệu/revoke các OAuth grant Gmail theo HR và loại các endpoint cho phép gửi email hoặc sửa lịch mà không qua bước HR confirmation.

Các test bắt buộc bao gồm OAuth state/replay/domain/scope failures; token refresh không làm mất refresh token; secret redaction; singleton connection; Gmail cursor 404 recovery và idempotency; không tự backfill; send retry không trùng; nhiều Interview/Candidate; selected calendar thay vì primary; optional Meet; attendee/RSVP; Calendar 410/412; event deletion; Candidate deletion guard; migration và rollback.

## Tham chiếu

- [Google OAuth 2.0 for Web Server Applications](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Trường hợp OAuth verification không bắt buộc](https://support.google.com/cloud/answer/13464323)
- [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [Gmail synchronization](https://developers.google.com/workspace/gmail/api/guides/sync)
- [Google Calendar API scopes](https://developers.google.com/workspace/calendar/api/auth)
- [Google Calendar incremental synchronization](https://developers.google.com/workspace/calendar/api/guides/sync)
- [Google Calendar resource versions và ETag](https://developers.google.com/workspace/calendar/api/guides/version-resources)
