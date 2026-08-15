# Vroom HR

Vroom HR (Vietnamese Recruit-Onboard-Operate-Manage) là nền tảng quản lý nhân sự mã nguồn mở, tự host dành cho doanh nghiệp Việt Nam. Mỗi công ty chạy một deployment riêng (database và server riêng). Một deployment chỉ phục vụ đúng một công ty. Glossary này quy định nghĩa chuẩn của các thuật ngữ domain để team dùng một từ cho một khái niệm trong spec, code và docs.

## Ngôn ngữ domain

**Organization**:
Công ty duy nhất sở hữu một deployment. Đây là singleton — mỗi instance đang chạy chỉ có đúng một Organization. Organization lưu các cài đặt cấp công ty (tên, mã số thuế, timezone, ngày nghỉ, allowed email domains). Organization KHÔNG phải boundary để cách ly dữ liệu vì một deployment không chứa nhiều công ty.
_Avoid_: Company, Tenant, Account, Client

**Tenant**:
Thuật ngữ legacy từ Policy Engine, nơi `tenant_id` được thiết kế làm khóa cách ly nhiều công ty. Trong mô hình self-hosted, mỗi deployment chỉ có một công ty nên `tenant_id` thực tế là hằng số. Xem mọi `tenant_id` hiện có là implementation detail cần đóng băng hoặc loại bỏ, không phải khái niệm multi-tenancy đang hoạt động.
_Avoid_: dùng Tenant như thể nhiều công ty cùng chia sẻ một deployment

**System Admin** (Deployer / Quản trị hệ thống):
Vai trò quản trị kỹ thuật và triển khai hệ thống (`UserRole.SYSTEM_ADMIN`). Chịu trách nhiệm thực hiện First-Run Setup, cấu hình Google Workspace OAuth Client ID/Secret, LLM Provider/API Keys, Email Whitelist, Organization Domains và xem System Audit Logs. System Admin bị cấm hoàn toàn (Strict Block) khỏi việc truy cập hoặc thao tác trên dữ liệu nghiệp vụ HR.
_Avoid_: Super Admin, System Administrator, Deployer (dùng System Admin thống nhất trong spec/code)

**System Admin Console** (Quản trị hệ thống):
Bề mặt giao diện dành riêng cho System Admin, tách hẳn với giao diện nghiệp vụ HR. Console chứa mọi đề mục cấu hình kỹ thuật; "Cấu hình AI" chỉ là một đề mục bên trong nó, không phải tên của cả console.
_Avoid_: Cấu hình AI & Hệ thống hay Cấu hình hệ thống dùng để chỉ toàn bộ console, Settings page, Admin panel

**Tổng quan hệ thống**:
Trang chủ của System Admin Console — nơi System Admin đáp xuống sau khi đăng nhập. Trình bày trạng thái vận hành của deployment và Quick-Start Guide của vai trò System Admin. Đây không phải Dashboard HR: System Admin không truy cập được bề mặt nghiệp vụ HR.
_Avoid_: Admin Dashboard, Trang chủ admin, dùng chung tên với Dashboard HR

**HR**:
Vai trò quản trị viên nghiệp vụ nhân sự (`UserRole.HR`). Quản lý Candidate, Job Opening, Onboarding, Employee Profile, Attendance, Employee Request, Payslip, Knowledge Base và tương tác HR Assistant. HR KHÔNG có quyền truy cập hoặc chỉnh sửa các secret cấu hình hạ tầng kỹ thuật của System Admin.
_Avoid_: Manager (Manager là khái niệm quan hệ báo cáo riêng), Administrator
**Employee**:
Một người có hồ sơ việc làm trong hệ thống. Employee được tạo ngay khi Candidate được accepted, bắt đầu ở trạng thái **inactive** (`is_active = false`, đang onboarding). Khi onboarding hoàn tất, Employee trở thành **active** (`is_active = true`). Ranh giới: Candidate = chưa được accepted; Employee inactive = đã accepted, đang onboarding; Employee active = đã hoàn tất onboarding. Employee active sử dụng phần Employee Self-Service.
_Avoid_: User (User là khái niệm tài khoản xác thực; Employee là khái niệm HR)

**Employee Account**:
Tài khoản xác thực gắn với một Employee. HR tạo và quản lý tài khoản này; Employee dùng nó để đăng nhập Employee Self-Service. Ở phiên bản đầu, tài khoản có thể được cấp mật khẩu tạm thời và bắt buộc đổi mật khẩu trong lần đăng nhập đầu tiên. Chỉ Employee active mới được nhận Employee Account.
_Avoid_: User, Login account, Profile

**Manager**:
Quan hệ báo cáo trực tiếp của một Employee với một Employee khác. Manager không đồng nghĩa với HR, không phải system role và tự nó không ngụ ý một permission model riêng.
_Avoid_: dùng Manager để chỉ HR, Administrator hoặc người mặc định có quyền phê duyệt

**Employee Self-Service**:
Phần giao diện dành cho Employee active. Đây là phần riêng với giao diện quản trị HR, phục vụ các view và action do Employee sở hữu.
_Avoid_: Portal, Employee dashboard, User area

**Employee Assistant**:
Trợ lý hội thoại dành cho Employee active. Chỉ được đọc dữ liệu của chính Employee đó và có thể draft các action do Employee sở hữu như request nghỉ phép hoặc làm thêm giờ, nhưng không bao giờ tự ghi dữ liệu.
_Avoid_: Employee AI Agent, Employee Chatbot, Self-service bot

**First-Run Setup**:
Flow thiết lập ban đầu, chỉ hiển thị trên deployment mới trước khi có tài khoản HR. Flow xác lập danh tính tối thiểu của Organization và tạo tài khoản HR đầu tiên; các cài đặt Organization khác được cấu hình sau.
_Avoid_: Signup, onboarding wizard, install flow, login screen

**Knowledge Base** (Cơ sở tri thức / Tài liệu nội bộ):
Kho tài liệu phi cấu trúc (PDF, DOCX) do HR quản lý, phục vụ RAG retrieval cho AI Assistant. Hệ thống có hai Knowledge Base riêng biệt về mặt vật lý: HR Knowledge Base (chỉ HR truy cập) và Employee Knowledge Base (HR quản lý, Employee truy vấn). HR upload tài liệu, hệ thống tự động parse → chunk → embed → index vào pgvector. Quá trình ingestion là bất đồng bộ qua ARQ worker. File gốc lưu trên MinIO.
_Avoid_: FAQ, Document Library, Policy Engine — Knowledge Base là kho dữ liệu phi cấu trúc cho RAG, không phải FAQ tĩnh hay document management tổng quát.

**HR Knowledge Base**:
Một trong hai Knowledge Base, chứa tài liệu chỉ dành cho HR (ví dụ: quy trình nội bộ HR, tài liệu pháp lý tham khảo, hướng dẫn nghiệp vụ tuyển dụng). HR Assistant truy vấn cả HR Knowledge Base và Employee Knowledge Base.
_Avoid_: Admin Docs, Internal HR Docs

**Employee Knowledge Base**:
Một trong hai Knowledge Base, chứa tài liệu HR publish cho Employee (ví dụ: nội quy lao động, sổ tay nhân viên, quy chế phúc lợi, lịch sự kiện công ty). HR Assistant và Employee Assistant đều truy vấn được KB này. Employee Assistant KHÔNG truy cập được HR Knowledge Base.
_Avoid_: Public Docs, Employee Handbook — Handbook là một tập con của Employee Knowledge Base.

**Attendance Record**:
Bản ghi chấm công hằng ngày cho một Employee trong một ngày làm việc. Ghi nhận việc Employee có mặt trong ngày đó, tách biệt với nghỉ phép, làm thêm giờ và payroll.
_Avoid_: Presence status, Shift, Timesheet

**Employee Request**:
Request do Employee gửi và cần HR review trước khi có hiệu lực, chẳng hạn request nghỉ phép hoặc làm thêm giờ. Request thuộc về Employee gửi request và do HR quyết định.
_Avoid_: Ticket, Approval item, Form submission

**Payslip**:
Bảng kê payroll cho một Employee trong một kỳ trả lương. Payslip trình bày các khoản payroll để Employee xem, tách biệt với payroll calculation engine.
_Avoid_: Salary record, Payroll run, Payment

## Khả năng AI

Hệ thống có ba khái niệm AI riêng biệt. KHÔNG được gộp chúng dưới tên gọi chung “AI Agent”.

**AI Automation**:
Các tác vụ AI chạy nền theo event, không có hội thoại: phân loại intent của email (job_application/partner/event/internal/other) và parse CV thành dữ liệu có cấu trúc. `job_application` biểu thị ý định ứng tuyển, không phụ thuộc việc email đã có CV hay chưa. Đã được triển khai trong module recruitment. Đây là pipeline, không phải agent.
_Avoid_: gọi khái niệm này là “the AI Agent”

**AI Assistant**:
Chatbot hội thoại dành cho HR (role admin). Có thể READ dữ liệu recruitment và onboarding (số lượng Candidate theo status, tóm tắt CV đã parse, lịch phỏng vấn, tiến độ onboarding) và DRAFT action cho HR (ví dụ soạn email mời phỏng vấn hoặc chúc mừng), nhưng không bao giờ tự ghi database — HR phải xác nhận mọi write (human-in-the-loop).
_Avoid_: Chatbot (quá chung), Agent (hàm ý tự động ghi dữ liệu)

**AI Agent (autonomous)**:
Khả năng tương lai giả định, trong đó AI tự quyết định và tự thực thi write action. Rõ ràng nằm ngoài scope hiện tại — chỉ được ghi nhận như một định hướng tương lai.
_Avoid_: dùng “Agent” để gọi Assistant hiện tại vì Assistant không autonomous

**Organization AI Configuration**:
Cấu hình AI cấp Organization do HR quản lý, xác định provider/model mặc định, nguồn credential và trạng thái bật/tắt độc lập của AI Automation và AI Assistant. Cấu hình này không thuộc Employee hoặc Employee Account.
_Avoid_: User AI Settings, Employee AI Configuration, AI Agent Configuration

## Recruitment & Onboarding

**Job Application**:
Đầu vào tuyển dụng ghi nhận ý định ứng tuyển vào Organization, dù do ứng viên gửi trực tiếp, nhân viên giới thiệu hay agency chuyển tiếp, và dù có file CV, link hồ sơ hay chưa cung cấp CV. Job Application phân biệt nguồn gửi với danh tính người ứng tuyển; có thể chưa xác định Job Opening nhưng sau khi HR làm rõ chỉ nhắm tối đa một Job Opening. Nó tồn tại trước Candidate và chỉ được chuyển thành Candidate khi đủ thông tin hoặc được HR chấp nhận đưa vào quy trình tuyển dụng.
_Avoid_: CV email, Applicant email, dùng `cv` làm tên intent

**Recruitment Inbox**:
Không gian làm việc thống nhất để HR xử lý email tuyển dụng và Job Application theo trạng thái, bao gồm trường hợp cần xác nhận phân loại, cần bổ sung thông tin, sẵn sàng review và đã xử lý. Đây không phải hộp thư Gmail tổng quát hoặc trang lỗi AI.
_Avoid_: AI Error Queue, Classification Queue, Gmail Inbox

**Candidate**:
Một người đã được đưa vào quy trình tuyển dụng, được tạo từ Job Application đủ điều kiện hoặc do HR tạo thủ công. Candidate đi qua pipeline: new → reviewing → `interview_scheduled` → accepted/rejected/archived. Candidate CHƯA phải Employee. Candidate có thể chưa được gán hoặc được gán vào đúng một Job Opening; việc gán có thể thay đổi cho đến trước khi Candidate đạt accepted/rejected/archived.
_Avoid_: Applicant, Employee (Employee chỉ tồn tại sau onboarding)

**Interview**: Một buổi phỏng vấn cụ thể của một Candidate. Một Candidate có thể có nhiều Interview để biểu diễn nhiều vòng hoặc lần lên lịch lại mà không ghi đè lịch sử phỏng vấn. Vòng đời: scheduled → completed/cancelled. Việc đổi lịch giữ nguyên Interview; nếu hủy buổi cũ và tạo buổi thay thế thì tạo Interview mới. Interview không tự động thay đổi Candidate pipeline.
_Avoid_: lưu lịch phỏng vấn trực tiếp như một thuộc tính duy nhất của Candidate, Interview Stage

**Job Opening**:
Một nhu cầu tuyển dụng cụ thể cho một Position trong Organization. Department được suy ra từ Position trong model ban đầu. Job Opening có thể nhóm các Candidate đang được cân nhắc cho nhu cầu đó và theo dõi target headcount riêng với Candidate pipeline; Candidate có thể tồn tại mà không cần Job Opening. Vòng đời: draft → open → closed/cancelled; chỉ Job Opening open mới nhận Candidate assignment mới. Headcount được tính theo Candidate accepted, không theo onboarding completion hoặc Employee active.
_Avoid_: Recruitment Plan, Hiring Plan, Vacancy, Requisition

**Backbone Flow**:
Workflow cốt lõi duy nhất của project: email đến → AI nhận diện Job Application → thu thập/parse CV khi có → Candidate → HR review → lên lịch phỏng vấn → accept → email chúc mừng → onboarding → Employee. Đây là flow mà project được xây dựng xoay quanh; mọi thứ khác là thứ yếu hoặc đã tạm gác.
_Avoid_: Pipeline (Pipeline chỉ machine status của Candidate)

**Onboarding**:
Quy trình dựa trên checklist, biến Candidate accepted thành Employee active. Quy trình được kích hoạt bởi event Candidate "accepted". Một OnboardingProcess chứa danh sách task (ví dụ ký hợp đồng, nộp hồ sơ, gán department/position, đặt start date); HR hoàn tất từng task, và chỉ khi mọi task bắt buộc hoàn tất cùng với thông tin department, position, manager và start date đầy đủ thì Employee mới trở thành active.
_Tình trạng triển khai (đã có):_ publisher `candidate_accepted` (ARQ) bên recruitment → worker consumer `onboarding/worker.py` (retry tối đa 3) → `OnboardingProcess` với checklist 4 task cố định → activation đặt `Employee.is_active = true` trong cùng transaction với task cuối. API `/api/onboarding/*` và UI `/onboarding` cho HR đã có. Onboarding không còn là "mắt xích còn thiếu" — nó đã được triển khai đầu cuối.
_Avoid_: Promotion, Hiring

**Onboarding Task**:
Một item trong checklist của OnboardingProcess, có status (pending/done).
_Avoid_: Step, Stage

## Nội bộ AI Assistant

**Tool**:
Một hàm có kiểu dữ liệu mà AI Assistant có thể gọi. Chỉ có đúng hai loại, không có loại nào khác: Read-Tool và Draft-Tool. LLM không bao giờ được cung cấp tool có khả năng ghi database — đây là ranh giới an toàn mang tính cấu trúc, không phụ thuộc vào convention.
_Avoid_: Function (quá chung), Plugin, Skill

**Read-Tool**:
Tool thực hiện read thật thông qua các service hiện có và trả về dữ liệu live (ví dụ đếm Candidate theo status, lấy một Candidate, liệt kê review queue). Có thể gọi tự do vì an toàn.
_Avoid_: Query (dành riêng cho command/query layer)

**Draft-Tool**:
Tool KHÔNG thực hiện write. Tool trả về một proposal có cấu trúc — gồm action type, parameters và preview dễ đọc (ví dụ email mời phỏng vấn đã soạn). LLM chỉ có thể đề xuất, không thể thực thi.
_Avoid_: Write-tool, Action-tool

**Draft Action**:
Proposal có cấu trúc do Draft-Tool trả về. HR review proposal và khi confirm, frontend gọi trực tiếp write endpoint thật hiện có (không gọi thông qua LLM). Đây là cơ chế giữ AI Assistant trong mô hình human-in-the-loop.
_Avoid_: Auto-action, Command

**AI Policy Preset**:
Mức chính sách tự động hóa được Organization chọn cho AI Automation, chẳng hạn conservative, balanced hoặc high-recall. Preset không phải raw confidence threshold và không cho phép Organization tự định nghĩa ngưỡng chưa được đánh giá.
_Avoid_: AI threshold, Confidence setting, AI mode

**AI Evaluation Set**:
Tập mẫu email hoặc CV đã được chọn, gán nhãn và redaction để đo chất lượng AI theo model, prompt và policy version. Đây là nguồn đánh giá có kiểm soát, không phải dữ liệu để hệ thống tự động học trực tuyến.
_Avoid_: Training set, Online learning data, AI history

**Field Provenance**:
Dấu vết chỉ ra field được AI trích xuất từ phần nào của email hoặc CV và mức độ cần HR xác nhận. Field Provenance phân biệt dữ liệu nguồn với structured draft do AI đề xuất.
_Avoid_: AI explanation, Chain-of-thought, Confidence note

## Google Integration

**Organization Google Connection**: Kết nối Google Workspace dùng chung của Organization, được HR thiết lập để phục vụ các tác vụ Gmail và Calendar của Organization. Kết nối này không thuộc về một Employee cụ thể, dù HR là người thực hiện cấp quyền. Mỗi Organization chỉ có một kết nối hoạt động.
_Avoid_: HR Google Account, Employee Google Connection, Personal Google Connection

**Organization Shared Google Account**: Tài khoản Google Workspace dùng chung đại diện cho hoạt động tuyển dụng của Organization, ví dụ mailbox tuyển dụng và calendar phỏng vấn. Đây là tài khoản được Organization Google Connection kết nối tới.
_Avoid_: Gmail cá nhân của HR, Employee Account

## Ngôn ngữ giao diện UX

**Tên hiển thị**:
Tên có nghĩa dùng để định danh người dùng trong điều hướng và giao diện; ưu tiên hơn mã nội bộ hoặc UUID.
_Avoid_: UUID, internal ID trong breadcrumb.

**Thông báo chưa đọc**:
Số lượng thông báo có dữ liệu thật mà người dùng chưa xem; badge chỉ được hiển thị khi số này được xác nhận.
_Avoid_: badge cố định, notification count giả.

**Trạng thái rỗng do bộ lọc**:
Trạng thái trong đó dữ liệu tồn tại nhưng không có bản ghi khớp từ khóa hoặc bộ lọc hiện tại.
_Avoid_: Chưa có dữ liệu.

**Trạng thái rỗng dữ liệu**:
Trạng thái trong đó chưa có bản ghi nào thuộc phạm vi người dùng đang xem.
_Avoid_: Không tìm thấy khi nguyên nhân là hệ thống chưa có dữ liệu.

**Checklist onboarding**:
Danh sách các bước hoặc tài liệu mà Employee cần hoàn thành trong quá trình onboarding.
_Avoid_: AI Assistant placeholder.

**Quick-Start Guide**:
Danh sách tác vụ có trạng thái hiển thị sau First-Run Setup cho đến khi vai trò đó hoàn tất các tác vụ thiết yếu của mình. Một khái niệm, hai tập task theo vai trò (xem Essential Setup Task) — không phải hai cơ chế khác nhau. Đây không phải tour tính năng.
_Avoid_: Feature tour, Walkthrough, Onboarding wizard, coi Quick-Start Guide là khái niệm chỉ của HR

**Essential Setup Task**:
Một tác vụ cần hoàn thành sau First-Run Setup trước khi Backbone Flow hoạt động. Tập task tách theo vai trò: **System Admin** có ba (cấu hình Google OAuth, cấu hình AI, tạo tài khoản HR đầu tiên); **HR** có ba (kết nối Organization Shared Google Account, tạo Job Opening đầu tiên, upload tài liệu đầu tiên vào Employee Knowledge Base). Cấu hình AI thuộc System Admin, không thuộc HR.
_Avoid_: Step, Checklist item (generic), gộp hai tập task thành một danh sách bốn task

**Guide Widget**:
Widget hiển thị progress bar và danh sách Essential Setup Task của vai trò đang đăng nhập, trên Dashboard HR hoặc Tổng quan hệ thống tương ứng. Click vào task → navigate đến trang cấu hình tương ứng.
_Avoid_: Banner, Tour popup, Slideshow


**Accent thương hiệu**:
Màu trả lời câu "chỗ nào bấm được" — indigo, dùng cho action, link, focus ring và icon nhấn. Đúng một accent, không có accent thứ hai.
_Avoid_: gọi mọi màu không phải slate là "accent"; dùng màu ngữ nghĩa làm affordance.

**Tone ngữ nghĩa**:
Màu trả lời câu "cái này đang ở trạng thái nào" — sáu tone có tên (`BadgeTone`), class chính tắc ở `frontend/components/shared-ui.tsx`. Trục riêng, độc lập với Accent thương hiệu; một màn hình mang cả hai không phải vi phạm.
_Avoid_: coi tone ngữ nghĩa là "accent thứ hai"; viết chuỗi class tone bằng tay thay vì đi qua bảng.

**Tiếng Việt mặc định**:
Ngôn ngữ mặc định của giao diện trong deployment dành cho doanh nghiệp Việt Nam; nhãn nghiệp vụ dùng tiếng Việt nhất quán.
_Avoid_: trộn Việt/Anh không có chủ đích.

## Tài liệu & Quản trị Repo

**Repository Governance**:
Hệ thống quy chuẩn tài liệu và quy trình quản trị dự án công khai trên GitHub (`README.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md`, `SECURITY.md`, YAML Issue Forms, PR Template) đảm bảo tiêu chuẩn Open-Source chuyên nghiệp (chuẩn 10k+ stars).
_Avoid_: Unstructured docs, Blank issue templates
