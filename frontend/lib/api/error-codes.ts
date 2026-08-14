    /**
     * Error Codes Registry — maps BE error_code → Vietnamese display message.
     *
     * AUTO-GENERATED from backend/src/shared/messages.py.
     * Run:  python3 -c "from backend.src.shared.messages import sync_frontend_error_codes; print(sync_frontend_error_codes())"
     * Or just copy from the Python catalog when adding new codes.
     *
     * Every error_code returned by the BE MUST have a mapping here.
     * Unknown error_codes fall back to the raw code string.
     */

    export const ERROR_CODE_MESSAGES: Record<string, string> = {
      // ── Shared / Generic ──
      NETWORK_ERROR: "Lỗi kết nối mạng. Vui lòng kiểm tra kết nối và thử lại.",
      TIMEOUT: "Yêu cầu đã hết thời gian chờ. Vui lòng thử lại.",
      UNKNOWN_ERROR: "Đã xảy ra lỗi không xác định. Vui lòng thử lại.",
      VALIDATION_ERROR: "Vui lòng kiểm tra lại thông tin đã nhập.",
      NOT_FOUND: "Không tìm thấy tài nguyên yêu cầu.",
      UNAUTHORIZED: "Bạn cần đăng nhập để thực hiện thao tác này.",
      FORBIDDEN: "Bạn không có quyền thực hiện thao tác này.",
      INTERNAL_ERROR: "Lỗi máy chủ. Vui lòng thử lại sau.",
      RATE_LIMITED: "Quá nhiều yêu cầu. Vui lòng thử lại sau.",
      FILE_TOO_LARGE: "Tệp vượt quá kích thước tối đa 10MB.",
      UNSUPPORTED_FILE_TYPE: "Định dạng tệp không được hỗ trợ.",
      CONNECTION_TEST_SUCCEEDED: "Kết nối thành công",
      CONNECTION_TEST_FAILED: "Kiểm tra kết nối thất bại",

      // ── Identity / Auth ──
      AUTH_ERROR: "Lỗi xác thực hệ thống.",
      AUTH_INVALID_CREDENTIALS: "Email hoặc mật khẩu không đúng.",
      AUTH_INVALID_STATE: "Trạng thái xác thực không hợp lệ.",
      AUTH_GOOGLE_ERROR: "Xác thực Google thất bại.",
      AUTH_ACCESS_DENIED: "Truy cập bị từ chối. Vui lòng liên hệ quản trị viên.",
      AUTH_INSUFFICIENT_SCOPE: "Vui lòng cấp tất cả quyền được yêu cầu.",
      AUTH_INVALID_TOKEN: "Phiên đăng nhập không hợp lệ hoặc đã hết hạn.",
      AUTH_INVALID_RESET_TOKEN: "Liên kết đặt lại mật khẩu không hợp lệ hoặc đã hết hạn",
      AUTH_RATE_LIMITED: "Quá nhiều lần đăng nhập, vui lòng thử lại sau.",
      AUTH_SETUP_ALREADY_COMPLETED: "Hệ thống đã được thiết lập trước đó.",
      AUTH_SETUP_REQUIRED: "Hệ thống chưa được thiết lập. Vui lòng chạy trình hướng dẫn cài đặt.",
      AUTH_PASSWORD_TOO_WEAK: "Mật khẩu phải có ít nhất 8 ký tự, bao gồm chữ hoa, chữ thường và số.",
      DOMAIN_NOT_ALLOWED: "Tên miền email không được phép truy cập tổ chức này.",
      ADMIN_ACCESS_DENIED: "Truy cập quản trị bị từ chối.",
      DOMAIN_ERROR: "Lỗi tên miền",
      OAUTH_VALIDATION_FAILED: "Xác thực OAuth thất bại",

      // ── Employee ──
      EMPLOYEE_ERROR: "Lỗi module nhân viên.",
      EMPLOYEE_DUPLICATE_EMAIL: "Email nhân viên đã tồn tại.",
      EMPLOYEE_NOT_FOUND: "Không tìm thấy nhân viên.",
      EMPLOYEE_RECORD_NOT_FOUND: "Không tìm thấy hồ sơ nhân viên",
      EMPLOYEE_ACCOUNT_INACTIVE: "Tài khoản nhân viên đã bị vô hiệu hóa",
      DEPARTMENT_NOT_FOUND: "Không tìm thấy phòng ban.",
      POSITION_NOT_FOUND: "Không tìm thấy chức vụ.",
      DEPARTMENT_HAS_EMPLOYEES: "Không thể xóa phòng ban có nhân viên đang hoạt động.",
      POSITION_HAS_EMPLOYEES: "Không thể xóa chức vụ có nhân viên đang hoạt động.",

      // ── Recruitment ──
      RECRUITMENT_ERROR: "Lỗi module tuyển dụng.",
      CANDIDATE_NOT_FOUND: "Không tìm thấy ứng viên.",
      CV_DOCUMENT_NOT_FOUND: "Không tìm thấy tài liệu CV.",
      INVALID_STATUS_TRANSITION: "Chuyển trạng thái không hợp lệ.",
      CV_FILE_MISSING: "Không tìm thấy tệp CV trong bộ nhớ.",
      STORAGE_SERVICE_UNAVAILABLE: "Dịch vụ lưu trữ không khả dụng.",
      GMAIL_NOT_CONNECTED: "Gmail chưa được kết nối.",
      PIPELINE_TIMEOUT: "Xử lý CV quá thời gian chờ.",
      OCR_EXTRACTION_FAILED: "Trích xuất OCR thất bại.",
      LLM_PARSE_FAILED: "Phân tích CV bằng AI thất bại.",
      AI_AUTOMATION_DISABLED: "AI Automation đang bị tắt. Vào Cấu hình AI & Hệ thống → bật 'Phân loại email & Trích xuất CV' để dùng tính năng này.",
      RECRUITMENT_ACCESS_DENIED: "Chỉ HR mới có quyền quản lý Tuyển dụng",
      RECRUITMENT_INBOX_NOT_FOUND: "Không tìm thấy mục trong Recruitment Inbox",

      // ── Onboarding ──
      ONBOARDING_ERROR: "Lỗi module onboarding.",
      ONBOARDING_NOT_FOUND: "Không tìm thấy quy trình onboarding.",
      ONBOARDING_ALREADY_EXISTS: "Nhân viên này đã có quy trình onboarding đang chạy.",

      // ── Attendance ──
      ATTENDANCE_ERROR: "Lỗi module chấm công.",
      ATTENDANCE_RECORD_NOT_FOUND: "Không tìm thấy bản ghi chấm công",
      LEAVE_TYPE_NOT_FOUND: "Không tìm thấy loại nghỉ phép.",
      LEAVE_REQUEST_NOT_FOUND: "Không tìm thấy đơn nghỉ phép.",
      INSUFFICIENT_LEAVE_BALANCE: "Số ngày phép không đủ.",
      LEAVE_OVERLAP: "Đơn nghỉ phép trùng với đơn đã tồn tại.",
      INVALID_LEAVE_STATUS_TRANSITION: "Chuyển trạng thái đơn nghỉ không hợp lệ.",
      LEAVE_DATE_IN_PAST: "Không thể hủy đơn nghỉ đã bắt đầu.",
      ALREADY_CHECKED_IN: "Đã check-in hôm nay.",
      NOT_CHECKED_IN: "Chưa check-in hôm nay.",
      ALREADY_CHECKED_OUT: "Đã check-out hôm nay.",
      OFFICE_NETWORK_REQUIRED: "Chỉ được phép check-in từ mạng văn phòng đã được phê duyệt.",
      OVERTIME_REQUEST_NOT_FOUND: "Không tìm thấy đơn tăng ca",
      OVERTIME_LIMIT_EXCEEDED: "Vượt quá giới hạn tăng ca",
      OVERTIME_END_BEFORE_START: "Giờ kết thúc phải sau giờ bắt đầu",
      OVERTIME_OVERLAP: "Bạn đã có đơn tăng ca trong ngày này",
      SCHEDULE_NOT_FOUND: "Không tìm thấy lịch làm việc",
      INVALID_CIDR: "Định dạng CIDR không hợp lệ",
      INVALID_CIDR_FORMAT_EXAMPLE: "Định dạng CIDR không hợp lệ. Ví dụ: 192.168.1.0/24 hoặc 10.0.0.1",

      // ── Employee Request ──
      REQUEST_NOT_FOUND: "Không tìm thấy yêu cầu",
      REQUEST_NOT_OWNED: "Bạn không sở hữu yêu cầu này",
      REQUEST_NOT_CANCELLABLE: "Chỉ có thể hủy yêu cầu đang chờ duyệt",
      REQUEST_NOT_REVIEWABLE: "Chỉ có thể duyệt yêu cầu đang chờ",

      // ── Payroll / Payslip ──
      PAYSLIP_ERROR: "Lỗi module phiếu lương.",
      PAYSLIP_NOT_FOUND: "Không tìm thấy phiếu lương.",
      PAYSLIP_NOT_PUBLISHED: "Phiếu lương chưa được phát hành",
      PAYSLIP_ALREADY_EXISTS: "Phiếu lương đã tồn tại cho nhân viên và kỳ này",
      PAYSLIP_ALREADY_PUBLISHED: "Phiếu lương đã được phát hành, không thể sửa.",
      PAYSLIP_NOT_DRAFT: "Chỉ phiếu lương bản nháp mới có thể thực hiện thao tác này.",
      PERIOD_NOT_FOUND: "Không tìm thấy kỳ lương",
      SALARY_NOT_CONFIGURED: "Chưa cấu hình lương cho nhân viên",

      // ── Gmail ──
      GMAIL_ERROR: "Lỗi module Gmail.",
      GMAIL_NOT_FOUND: "Không tìm thấy email.",
      GMAIL_CONNECT_FAILED: "Kết nối Gmail thất bại. Vui lòng thử lại.",
      GMAIL_SEND_FAILED: "Gửi email thất bại. Vui lòng thử lại.",
      GMAIL_FETCH_ERROR: "Lấy dữ liệu từ Gmail API thất bại",
      EMAIL_NOT_AWAITING_RECOVERY: "Email không ở trạng thái chờ phục hồi thủ công",
      INVALID_EMAIL_CATEGORY: "Danh mục email không hợp lệ",

      // ── AI / Assistant ──
      AI_CONFIG_INVALID: "Cấu hình AI không hợp lệ. Vui lòng kiểm tra lại.",
      AI_CONFIG_NO_PROVIDER: "Chưa cấu hình AI Provider. Vui lòng thiết lập trong Cấu hình AI & Hệ thống.",
      AI_CONNECTION_FAILED: "Không thể kết nối tới AI Provider. Vui lòng kiểm tra lại thông tin.",
      AI_POLICY_CONSENT_REQUIRED: "Bạn cần đồng ý chính sách dữ liệu trước khi bật tính năng AI.",
      AI_CAPABILITY_CONSENT_REQUIRED: "Bạn cần đồng ý điều khoản sử dụng cho tính năng này.",
      AI_DEPLOYMENT_KEY_MISSING: "Deployment key chưa được cấu hình. Liên hệ quản trị hệ thống.",
      ASSISTANT_ERROR: "Lỗi module trợ lý AI",
      LAST_MESSAGE_FROM_USER: "Tin nhắn cuối cùng phải từ người dùng",
      SESSION_NOT_FOUND: "Không tìm thấy phiên trò chuyện",

      // ── Admin / System ──
      ADMIN_ERROR: "Lỗi module quản trị.",
      USER_NOT_FOUND: "Không tìm thấy người dùng.",
      LAST_ADMIN_ERROR: "Không thể hạ quyền admin cuối cùng",
      SUPER_ADMIN_PROTECTED: "Không thể thay đổi vai trò của Super Admin.",
    };

    export function getErrorMessage(code: string): string {
      return ERROR_CODE_MESSAGES[code] || code;
    }

    /**
     * Resolve a user-facing message from any thrown error, falling back to
     * the raw error message then the given fallback string.
     *
     * Accepts AuthApiError (which carries `code`) and the generic ApiError
     * (which carries `errorCode`) via duck typing, so pages can render a
     * single catch block instead of duplicating the code lookup chain.
     */
    export function errorMessage(err: unknown, fallback: string): string {
      if (err instanceof Error) {
        const maybe = err as Error & { code?: string; errorCode?: string };
        const code = maybe.code ?? maybe.errorCode;
        return getErrorMessage(code ?? "") || err.message || fallback;
      }
      return fallback;
    }


    