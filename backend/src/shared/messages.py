"""
Centralized Message Catalog — single source of truth for all user-facing messages.

Every user-facing message in the application (backend + frontend) lives here,
organized by module. Each entry has a unique error code, a Vietnamese message,
and an English fallback.

Usage:
    from src.shared.messages import get_message, MESSAGES

    # Get message in Vietnamese (default)
    msg = get_message("AUTH_INVALID_CREDENTIALS")  # "Email hoặc mật khẩu không đúng"

    # Get message in English
    msg = get_message("AUTH_INVALID_CREDENTIALS", lang="en")  # "Email or password is incorrect"

    # Raise with message from catalog
    raise HTTPException(
        status_code=401,
        detail={
            "code": "AUTH_INVALID_CREDENTIALS",
            "message": get_message("AUTH_INVALID_CREDENTIALS"),
        },
    )
"""

from fastapi import Request

MESSAGES: dict[str, dict[str, str]] = {
    # =========================================================================
    # IDENTITY & AUTH MODULE
    # =========================================================================
    "AUTH_ERROR": {
        "vi": "Lỗi xác thực hệ thống",
        "en": "System authentication error",
    },
    "AUTH_INVALID_CREDENTIALS": {
        "vi": "Email hoặc mật khẩu không đúng",
        "en": "Email or password is incorrect",
    },
    "AUTH_INVALID_STATE": {
        "vi": "Trạng thái xác thực không hợp lệ",
        "en": "Invalid authentication state",
    },
    "AUTH_GOOGLE_ERROR": {
        "vi": "Xác thực Google thất bại",
        "en": "Google authentication failed",
    },
    "AUTH_ACCESS_DENIED": {
        "vi": "Truy cập bị từ chối. Vui lòng liên hệ quản trị viên.",
        "en": "Access denied. Please contact your administrator.",
    },
    "AUTH_INSUFFICIENT_SCOPE": {
        "vi": "Vui lòng cấp tất cả quyền được yêu cầu",
        "en": "Please grant all requested permissions",
    },
    "AUTH_INVALID_TOKEN": {
        "vi": "Phiên đăng nhập không hợp lệ hoặc đã hết hạn",
        "en": "Session is invalid or has expired",
    },
    "AUTH_RATE_LIMITED": {
        "vi": "Quá nhiều lần đăng nhập. Vui lòng thử lại sau.",
        "en": "Too many login attempts. Please try again later.",
    },
    "AUTH_SETUP_ALREADY_COMPLETED": {
        "vi": "Hệ thống đã được thiết lập trước đó",
        "en": "System has already been set up",
    },
    "DOMAIN_NOT_ALLOWED": {
        "vi": "Tên miền email không được phép truy cập tổ chức này.",
        "en": "Email domain is not allowed to access this organization.",
    },
    "ADMIN_ACCESS_DENIED": {
        "vi": "Truy cập quản trị bị từ chối.",
        "en": "Admin access required",
    },
    "TOKEN_REFRESHED": {
        "vi": "Token đã được làm mới",
        "en": "Token refreshed",
    },
    "LOGGED_OUT": {
        "vi": "Đã đăng xuất",
        "en": "Logged out",
    },
    "CONNECTION_TEST_SUCCEEDED": {
        "vi": "Kết nối thành công",
        "en": "Connection test succeeded",
    },
    "CONNECTION_TEST_FAILED": {
        "vi": "Kiểm tra kết nối thất bại",
        "en": "Connection test failed",
    },
    "DEPLOYMENT_KEY_CONNECTION_TEST_SUCCEEDED": {
        "vi": "Kiểm tra kết nối Deployment Key thành công",
        "en": "Deployment key connection test succeeded",
    },
    # AI Config
    "AI_CONFIG_INVALID": {
        "vi": "Cấu hình AI không hợp lệ",
        "en": "Invalid AI configuration",
    },
    "AI_CONNECTION_FAILED": {
        "vi": "Kết nối AI thất bại",
        "en": "AI connection failed",
    },
    "AI_CONFIG_NO_PROVIDER": {
        "vi": "Chưa cấu hình AI Provider. Vui lòng thiết lập trong Cấu hình AI & Hệ thống.",
        "en": "No provider configuration exists. Please configure in AI & System Settings.",
    },
    "CLASSIFICATION_ROLLOUT_BLOCKED": {
        "vi": "Không thể cấu hình rollout phân loại",
        "en": "Classification rollout configuration blocked",
    },
    "CLASSIFICATION_ROLLOUT_INVALID": {
        "vi": "Rollback phân loại không hợp lệ",
        "en": "Invalid classification rollout rollback",
    },
    "DOMAIN_ERROR": {
        "vi": "Lỗi tên miền",
        "en": "Domain error",
    },
    "OAUTH_VALIDATION_FAILED": {
        "vi": "Xác thực OAuth thất bại",
        "en": "OAuth validation failed",
    },
    # Role management errors
    "USER_NOT_FOUND": {
        "vi": "Không tìm thấy người dùng",
        "en": "User not found",
    },
    "LAST_ADMIN_ERROR": {
        "vi": "Không thể hạ quyền admin cuối cùng",
        "en": "Cannot demote the last admin",
    },
    "SUPER_ADMIN_PROTECTED": {
        "vi": "Không thể thay đổi quyền Super Admin",
        "en": "Cannot change Super Admin role",
    },
    # Whitelist
    "WHITELIST_INVALID_FORMAT": {
        "vi": "Định dạng whitelist không hợp lệ",
        "en": "Invalid whitelist format",
    },
    "WHITELIST_DUPLICATE": {
        "vi": "Giá trị đã tồn tại trong whitelist",
        "en": "Value already exists in whitelist",
    },
    "WHITELIST_NOT_FOUND": {
        "vi": "Không tìm thấy mục whitelist",
        "en": "Whitelist entry not found",
    },
    "WHITELIST_READONLY": {
        "vi": "Không thể xóa mục whitelist chỉ đọc từ tệp cấu hình",
        "en": "Cannot delete a read-only whitelist entry from config file",
    },
    "ORG_AI_PROVIDER_NOT_CONFIGURED": {
        "vi": "Chưa cấu hình AI Provider cho Organization. Vui lòng vào Cấu hình AI & Hệ thống để thiết lập.",  # noqa: E501
        "en": "AI Provider not configured for Organization. Go to AI & System Settings to set up.",
    },
    # =========================================================================
    # EMPLOYEE MODULE
    # =========================================================================
    "EMPLOYEE_ERROR": {
        "vi": "Lỗi module nhân viên",
        "en": "Employee module error",
    },
    "EMPLOYEE_DUPLICATE_EMAIL": {
        "vi": "Email nhân viên đã tồn tại",
        "en": "Employee with this email already exists",
    },
    "EMPLOYEE_NOT_FOUND": {
        "vi": "Không tìm thấy nhân viên",
        "en": "Employee not found",
    },
    "EMPLOYEE_RECORD_NOT_FOUND": {
        "vi": "Không tìm thấy hồ sơ nhân viên",
        "en": "Employee record not found",
    },
    "EMPLOYEE_ACCOUNT_INACTIVE": {
        "vi": "Tài khoản nhân viên đã bị vô hiệu hóa",
        "en": "Employee account is inactive",
    },
    "EMPLOYEE_MUST_BE_ACTIVE": {
        "vi": "Nhân viên phải đang hoạt động để tạo tài khoản",
        "en": "Employee must be active before creating account",
    },
    "DEPARTMENT_NOT_FOUND": {
        "vi": "Không tìm thấy phòng ban",
        "en": "Department not found",
    },
    "POSITION_NOT_FOUND": {
        "vi": "Không tìm thấy chức vụ",
        "en": "Position not found",
    },
    "DEPARTMENT_HAS_EMPLOYEES": {
        "vi": "Không thể xóa phòng ban có nhân viên đang hoạt động",
        "en": "Cannot delete department with active employees",
    },
    "POSITION_HAS_EMPLOYEES": {
        "vi": "Không thể xóa chức vụ có nhân viên đang hoạt động",
        "en": "Cannot delete position with active employees",
    },
    "FILE_TOO_LARGE": {
        "vi": "Tệp vượt quá kích thước tối đa 10MB",
        "en": "File exceeds maximum size of 10MB",
    },
    "UNSUPPORTED_FILE_TYPE": {
        "vi": "Định dạng tệp không được hỗ trợ",
        "en": "File type not supported",
    },
    "ACCESS_DENIED_VIEW_PROFILE": {
        "vi": "Từ chối truy cập: không thể xem hồ sơ của nhân viên khác",
        "en": "Access denied: cannot view another employee's profile",
    },
    "ACCESS_DENIED_UPDATE_EMPLOYEE": {
        "vi": "Nhân viên chỉ có thể cập nhật số điện thoại và địa chỉ",
        "en": "Employees can only update phone and address",
    },
    "ACCESS_DENIED_UPDATE_OTHER": {
        "vi": "Từ chối truy cập: không thể cập nhật thông tin nhân viên khác",
        "en": "Access denied: cannot update another employee",
    },
    "ACCESS_DENIED_VIEW_DOCUMENTS": {
        "vi": "Từ chối truy cập: không thể xem tài liệu của nhân viên khác",
        "en": "Access denied: cannot view another employee's documents",
    },
    "ACCESS_DENIED_UPLOAD_DOCUMENTS": {
        "vi": "Từ chối truy cập: không thể tải lên tài liệu cho nhân viên khác",
        "en": "Access denied: cannot upload to another employee's documents",
    },
    "ACCESS_DENIED_DOWNLOAD_DOCUMENT": {
        "vi": "Từ chối truy cập: không thể tải xuống tài liệu của nhân viên khác",
        "en": "Access denied: cannot download another employee's document",
    },
    "IMPORT_SUCCESS": {
        "vi": "Nhập dữ liệu thành công",
        "en": "Import completed successfully",
    },
    # =========================================================================
    # RECRUITMENT MODULE
    # =========================================================================
    "RECRUITMENT_ERROR": {
        "vi": "Lỗi module tuyển dụng",
        "en": "Recruitment module error",
    },
    "CANDIDATE_NOT_FOUND": {
        "vi": "Không tìm thấy ứng viên",
        "en": "Candidate not found",
    },
    "CV_DOCUMENT_NOT_FOUND": {
        "vi": "Không tìm thấy tài liệu CV",
        "en": "CV document not found",
    },
    "INVALID_STATUS_TRANSITION": {
        "vi": "Chuyển trạng thái không hợp lệ",
        "en": "Invalid status transition",
    },
    "CV_FILE_MISSING": {
        "vi": "Không tìm thấy tệp CV trong bộ nhớ",
        "en": "CV file not found in storage",
    },
    "STORAGE_SERVICE_UNAVAILABLE": {
        "vi": "Dịch vụ lưu trữ không khả dụng",
        "en": "Storage service unavailable",
    },
    "GMAIL_NOT_CONNECTED": {
        "vi": "Gmail chưa được kết nối",
        "en": "Gmail not connected",
    },
    "PIPELINE_TIMEOUT": {
        "vi": "Xử lý CV quá thời gian chờ",
        "en": "CV processing timed out",
    },
    "OCR_EXTRACTION_FAILED": {
        "vi": "Trích xuất OCR thất bại",
        "en": "OCR extraction failed",
    },
    "LLM_PARSE_FAILED": {
        "vi": "Phân tích CV bằng AI thất bại",
        "en": "AI CV parsing failed",
    },
    "AI_AUTOMATION_DISABLED": {
        "vi": "AI Automation đang bị tắt. Vào Cấu hình AI & Hệ thống → bật 'Phân loại email & Trích xuất CV' để dùng tính năng này.",  # noqa: E501
        "en": "AI Automation is disabled. Go to AI & System Settings → enable 'Email Classification & CV Extraction' to use this feature.",  # noqa: E501
    },
    "RECRUITMENT_ACCESS_DENIED": {
        "vi": "Chỉ HR mới có quyền quản lý Tuyển dụng",
        "en": "Only HR can access Recruitment",
    },
    "RECRUITMENT_INBOX_NOT_FOUND": {
        "vi": "Không tìm thấy mục trong Recruitment Inbox",
        "en": "Recruitment Inbox item not found",
    },
    "INBOX_ITEM_DISMISSED": {
        "vi": "Không thể sửa đổi mục đã được dismiss",
        "en": "Cannot modify a dismissed inbox item",
    },
    "INBOX_ITEM_SPLIT_DISMISSED": {
        "vi": "Không thể tách mục đã được dismiss",
        "en": "Cannot split a dismissed inbox item",
    },
    "CALENDAR_CONFLICT_NOT_FOUND": {
        "vi": "Không tìm thấy xung đột lịch",
        "en": "Calendar conflict not found",
    },
    "INVALID_INBOX_STATUS_FILTER": {
        "vi": "Giá trị lọc trạng thái inbox không hợp lệ",
        "en": "Invalid inbox status filter value",
    },
    "CORRECTION_RECORD_NOT_FOUND": {
        "vi": "Không tìm thấy bản ghi hiệu chỉnh",
        "en": "Correction record not found",
    },
    "CANDIDATE_VALIDATION_FAILED": {
        "vi": "Xác thực dữ liệu ứng viên thất bại",
        "en": "Candidate validation failed",
    },
    "REVIEW_VALIDATION_FAILED": {
        "vi": "Xác thực đánh giá thất bại",
        "en": "Review validation failed",
    },
    "CORRECTED_DATA_VALIDATION_FAILED": {
        "vi": "Xác thực dữ liệu đã hiệu chỉnh thất bại",
        "en": "Corrected data validation failed",
    },
    "JOB_APPLICATION_NOT_FOUND": {
        "vi": "Không tìm thấy đơn ứng tuyển",
        "en": "Job application not found",
    },
    "ALL_EMAILS_CLASSIFIED": {
        "vi": "Tất cả email đã được phân loại",
        "en": "All emails have been classified",
    },
    "NO_ATTACHMENTS_FOUND": {
        "vi": "Không tìm thấy tệp đính kèm",
        "en": "No attachments found",
    },
    "CV_PROCESSING_FAILED": {
        "vi": "Xử lý CV thất bại",
        "en": "CV processing failed",
    },
    # =========================================================================
    # ATTENDANCE MODULE
    # =========================================================================
    "ATTENDANCE_ERROR": {
        "vi": "Lỗi module chấm công",
        "en": "Attendance module error",
    },
    "ALREADY_CHECKED_IN": {
        "vi": "Đã check-in hôm nay",
        "en": "Already checked in for today",
    },
    "NOT_CHECKED_IN": {
        "vi": "Chưa check-in hôm nay",
        "en": "Must check in before checking out",
    },
    "ALREADY_CHECKED_OUT": {
        "vi": "Đã check-out hôm nay",
        "en": "Already checked out for today",
    },
    "OFFICE_NETWORK_REQUIRED": {
        "vi": "Chỉ được phép check-in từ mạng văn phòng đã được phê duyệt.",
        "en": "Attendance check-in is only allowed from approved office network.",
    },
    "ATTENDANCE_RECORD_NOT_FOUND": {
        "vi": "Không tìm thấy bản ghi chấm công",
        "en": "Attendance record not found",
    },
    "CHECKED_IN_SUCCESS": {
        "vi": "Check-in thành công!",
        "en": "Checked in successfully",
    },
    "CHECKED_OUT_SUCCESS": {
        "vi": "Check-out thành công!",
        "en": "Checked out successfully",
    },
    "ATTENDANCE_CORRECTED_SUCCESS": {
        "vi": "Đã lưu hiệu chỉnh chấm công",
        "en": "Attendance record corrected successfully",
    },
    "DATE_RANGE_INVALID": {
        "vi": "Ngày kết thúc phải sau hoặc bằng ngày bắt đầu",
        "en": "End date must be on or after start date",
    },
    "YEAR_MONTH_REQUIRED_BOTH": {
        "vi": "Cần cung cấp cả năm và tháng, hoặc không cung cấp cả hai",
        "en": "Either provide both year and month, or neither",
    },
    # Attendance IP allowlist
    "INVALID_CIDR": {
        "vi": "Định dạng CIDR không hợp lệ",
        "en": "Invalid CIDR format",
    },
    "DUPLICATE_CIDR": {
        "vi": "CIDR đã tồn tại trong danh sách cho phép",
        "en": "CIDR already exists in allowlist",
    },
    "TOO_MANY_NETWORKS": {
        "vi": "Quá nhiều mạng trong danh sách cho phép",
        "en": "Too many network entries",
    },
    "CIDR_NOT_FOUND": {
        "vi": "Không tìm thấy CIDR trong danh sách cho phép",
        "en": "CIDR not found in allowlist",
    },
    "CIDR_ADDED_SUCCESS": {
        "vi": "Đã thêm CIDR",
        "en": "CIDR added successfully",
    },
    "CIDR_DELETED_SUCCESS": {
        "vi": "Đã xóa CIDR",
        "en": "CIDR deleted successfully",
    },
    "ALLOWLIST_UPDATED_SUCCESS": {
        "vi": "Đã cập nhật allowlist",
        "en": "Allowlist updated successfully",
    },
    "INVALID_CIDR_FORMAT_EXAMPLE": {
        "vi": "Định dạng CIDR không hợp lệ. Ví dụ: 192.168.1.0/24 hoặc 10.0.0.1",
        "en": "Invalid CIDR format. Example: 192.168.1.0/24 or 10.0.0.1",
    },
    # Leave
    "LEAVE_TYPE_NOT_FOUND": {
        "vi": "Không tìm thấy loại nghỉ phép",
        "en": "Leave type not found",
    },
    "LEAVE_REQUEST_NOT_FOUND": {
        "vi": "Không tìm thấy đơn nghỉ phép",
        "en": "Leave request not found",
    },
    "INSUFFICIENT_LEAVE_BALANCE": {
        "vi": "Số ngày phép không đủ",
        "en": "Insufficient leave balance",
    },
    "LEAVE_OVERLAP": {
        "vi": "Đơn nghỉ phép trùng với đơn đã tồn tại",
        "en": "Leave request overlaps with an existing request",
    },
    "INVALID_LEAVE_STATUS_TRANSITION": {
        "vi": "Chuyển trạng thái đơn nghỉ không hợp lệ",
        "en": "Invalid leave status transition",
    },
    "LEAVE_DATE_IN_PAST": {
        "vi": "Không thể hủy đơn nghỉ đã bắt đầu",
        "en": "Cannot cancel leave that has already started",
    },
    # Overtime
    "OVERTIME_REQUEST_NOT_FOUND": {
        "vi": "Không tìm thấy đơn tăng ca",
        "en": "Overtime request not found",
    },
    "OVERTIME_LIMIT_EXCEEDED": {
        "vi": "Vượt quá giới hạn tăng ca",
        "en": "Overtime limit exceeded",
    },
    "OVERTIME_END_BEFORE_START": {
        "vi": "Giờ kết thúc phải sau giờ bắt đầu",
        "en": "End time must be after start time",
    },
    "OVERTIME_OVERLAP": {
        "vi": "Bạn đã có đơn tăng ca trong ngày này",
        "en": "You already have an overtime request on this date",
    },
    # Schedule
    "SCHEDULE_NOT_FOUND": {
        "vi": "Không tìm thấy lịch làm việc",
        "en": "Schedule not found",
    },
    # =========================================================================
    # EMPLOYEE REQUEST MODULE
    # =========================================================================
    "EMPLOYEE_REQUEST_ERROR": {
        "vi": "Lỗi module yêu cầu nhân viên",
        "en": "Employee request module error",
    },
    "REQUEST_NOT_FOUND": {
        "vi": "Không tìm thấy yêu cầu",
        "en": "Request not found",
    },
    "REQUEST_NOT_OWNED": {
        "vi": "Bạn không sở hữu yêu cầu này",
        "en": "You do not own this request",
    },
    "REQUEST_NOT_CANCELLABLE": {
        "vi": "Chỉ có thể hủy yêu cầu đang chờ duyệt",
        "en": "Only submitted requests can be cancelled",
    },
    "REQUEST_NOT_REVIEWABLE": {
        "vi": "Chỉ có thể duyệt yêu cầu đang chờ",
        "en": "Only submitted requests can be reviewed",
    },
    "REQUEST_SUBMIT_FORBIDDEN": {
        "vi": "Chỉ nhân viên mới có thể gửi yêu cầu",
        "en": "Only employees can submit requests",
    },
    # =========================================================================
    # PAYSLIP / PAYROLL MODULE
    # =========================================================================
    "PAYSLIP_ERROR": {
        "vi": "Lỗi module lương",
        "en": "Payslip module error",
    },
    "PAYSLIP_NOT_FOUND": {
        "vi": "Không tìm thấy phiếu lương",
        "en": "Payslip not found",
    },
    "PAYSLIP_NOT_PUBLISHED": {
        "vi": "Phiếu lương chưa được phát hành",
        "en": "Payslip is not yet published",
    },
    "PAYSLIP_ALREADY_EXISTS": {
        "vi": "Phiếu lương đã tồn tại cho nhân viên và kỳ này",
        "en": "A payslip already exists for this employee and period",
    },
    "PAYSLIP_ALREADY_PUBLISHED": {
        "vi": "Không thể sửa đổi phiếu lương đã phát hành",
        "en": "Cannot modify a published payslip",
    },
    "PAYSLIP_NOT_DRAFT": {
        "vi": "Phiếu lương phải ở trạng thái nháp",
        "en": "Payslip must be in draft status",
    },
    "PAYSLIP_CREATED_SUCCESS": {
        "vi": "Đã tạo phiếu lương",
        "en": "Payslip created successfully",
    },
    "PAYSLIP_PUBLISHED_SUCCESS": {
        "vi": "Đã phát hành phiếu lương",
        "en": "Payslip published successfully",
    },
    "PAYSLIP_UNPUBLISHED_SUCCESS": {
        "vi": "Đã thu hồi phiếu lương",
        "en": "Payslip unpublished successfully",
    },
    "PAYSLIP_DELETED_SUCCESS": {
        "vi": "Đã xóa phiếu lương",
        "en": "Payslip deleted successfully",
    },
    "BULK_PUBLISH_SUCCESS": {
        "vi": "Đã phát hành hàng loạt phiếu lương",
        "en": "Payslips bulk published successfully",
    },
    "INVALID_PAYSLIP_ID": {
        "vi": "ID phiếu lương không hợp lệ",
        "en": "Invalid payslip ID",
    },
    "INVALID_EMPLOYEE_ID": {
        "vi": "ID nhân viên không hợp lệ",
        "en": "Invalid employee ID",
    },
    "INVALID_STATUS": {
        "vi": "Trạng thái không hợp lệ. Phải là 'draft' hoặc 'published'",
        "en": "Invalid status. Must be 'draft' or 'published'",
    },
    "INVALID_PERIOD_MONTH": {
        "vi": "Định dạng kỳ lương không hợp lệ. Sử dụng định dạng YYYY-MM",
        "en": "Invalid period month format. Use YYYY-MM format",
    },
    # Payroll (broader)
    "PAYROLL_ERROR": {
        "vi": "Lỗi module lương",
        "en": "Payroll module error",
    },
    "PERIOD_NOT_FOUND": {
        "vi": "Không tìm thấy kỳ lương",
        "en": "Pay period not found",
    },
    "PERIOD_ALREADY_CLOSED": {
        "vi": "Kỳ lương đã đóng",
        "en": "Pay period already closed",
    },
    "EMPLOYEE_NOT_IN_PERIOD": {
        "vi": "Nhân viên không trong kỳ lương",
        "en": "Employee not in this pay period",
    },
    "SALARY_NOT_CONFIGURED": {
        "vi": "Chưa cấu hình lương cho nhân viên",
        "en": "Salary not configured for employee",
    },
    "TAX_CALCULATION_ERROR": {
        "vi": "Tính thuế thất bại",
        "en": "Tax calculation error",
    },
    # =========================================================================
    # GMAIL MODULE
    # =========================================================================
    "GMAIL_ERROR": {
        "vi": "Lỗi module Gmail",
        "en": "Gmail module error",
    },
    "UNAUTHORIZED": {
        "vi": "Phiên đăng nhập không hợp lệ hoặc đã hết hạn",
        "en": "Session is invalid or has expired",
    },
    "GMAIL_CONNECT_FAILED": {
        "vi": "Kết nối Gmail thất bại",
        "en": "Gmail connection failed",
    },
    "LABEL_NAMESPACE_VIOLATION": {
        "vi": "Nhãn phải nằm trong không gian tên VroomHR/",
        "en": "Label must be in the VroomHR/ namespace",
    },
    "GMAIL_FETCH_ERROR": {
        "vi": "Lấy dữ liệu từ Gmail API thất bại",
        "en": "Failed to fetch data from Gmail API",
    },
    "MESSAGE_NOT_FOUND": {
        "vi": "Không tìm thấy thư",
        "en": "Email not found",
    },
    "GMAIL_LABEL_REMOVE_FAILED": {
        "vi": "Xóa nhãn thất bại",
        "en": "Failed to remove label",
    },
    "GMAIL_SEND_FAILED": {
        "vi": "Gửi email thất bại",
        "en": "Failed to send email",
    },
    "RATE_LIMITED": {
        "vi": "Vượt quá giới hạn tần suất",
        "en": "Rate limit exceeded",
    },
    "NOT_AUTHORIZED": {
        "vi": "Không có quyền truy cập",
        "en": "Not authorized",
    },
    "EMAIL_CATEGORY_MISMATCH": {
        "vi": "Danh mục email không đúng, cần là 'recruitment'",
        "en": "Email category mismatch, expected 'recruitment'",
    },
    "EMAIL_STATUS_MISMATCH": {
        "vi": "Trạng thái email không đúng, cần là 'classified'",
        "en": "Email status mismatch, expected 'classified'",
    },
    "EMAIL_NOT_AWAITING_RECOVERY": {
        "vi": "Email không ở trạng thái chờ phục hồi thủ công",
        "en": "Email is not awaiting manual recovery",
    },
    "INVALID_EMAIL_CATEGORY": {
        "vi": "Danh mục email không hợp lệ",
        "en": "Invalid email category",
    },
    "RECIPIENT_EMAIL_REQUIRED": {
        "vi": "Email người nhận là bắt buộc",
        "en": "Recipient email is required",
    },
    "BODY_HTML_REQUIRED": {
        "vi": "Nội dung email là bắt buộc",
        "en": "Email body is required",
    },
    "EMAIL_SEND_SUCCESS": {
        "vi": "Đã gửi email thành công",
        "en": "Email sent successfully",
    },
    "EMAIL_SYNC_SUCCESS": {
        "vi": "Đã đồng bộ email",
        "en": "Emails synced successfully",
    },
    "CV_PROCESSED_COUNT": {
        "vi": "Đã xử lý {count} CV",
        "en": "Processed {count} CVs",
    },
    # =========================================================================
    # ASSISTANT MODULE
    # =========================================================================
    "ASSISTANT_ERROR": {
        "vi": "Lỗi module trợ lý AI",
        "en": "AI Assistant module error",
    },
    "LAST_MESSAGE_FROM_USER": {
        "vi": "Tin nhắn cuối cùng phải từ người dùng",
        "en": "Last message must be from user",
    },
    "SESSION_NOT_FOUND": {
        "vi": "Không tìm thấy phiên trò chuyện",
        "en": "Session not found",
    },
    "ASSISTANT_SERVICE_UNAVAILABLE": {
        "vi": "Dịch vụ trợ lý AI không khả dụng",
        "en": "AI Assistant service unavailable",
    },
    # Assistant tool messages (returned as tool errors)
    "INVALID_MONTH": {
        "vi": "Tháng không hợp lệ. Vui lòng nhập từ 1 đến 12.",
        "en": "Invalid month. Please enter 1 to 12.",
    },
    "INVALID_YEAR": {
        "vi": "Năm không hợp lệ.",
        "en": "Invalid year.",
    },
    "INVALID_DATE_FORMAT": {
        "vi": "Ngày không hợp lệ. Định dạng: YYYY-MM-DD.",
        "en": "Invalid date. Format: YYYY-MM-DD.",
    },
    "INVALID_TIME_FORMAT": {
        "vi": "Giờ không hợp lệ. Định dạng: HH:MM.",
        "en": "Invalid time. Format: HH:MM.",
    },
    "CANDIDATE_NOT_FOUND_TOOL": {
        "vi": "Không tìm thấy ứng viên",
        "en": "Candidate not found",
    },
    "ONBOARDING_PROCESS_NOT_FOUND": {
        "vi": "Không tìm thấy quy trình onboarding",
        "en": "Onboarding process not found",
    },
    "ONBOARDING_IN_PROGRESS": {
        "vi": "Onboarding đang diễn ra: {count} nhân viên.",
        "en": "Onboarding in progress: {count} employees.",
    },
    "MISSING_ONBOARDING_PROCESS_ID": {
        "vi": "Thiếu tham số bắt buộc: onboarding_process_id.",
        "en": "Missing required parameter: onboarding_process_id.",
    },
    "INVALID_REQUEST_TYPE": {
        "vi": "Loại yêu cầu không hợp lệ",
        "en": "Invalid request type",
    },
    "INVALID_LEAVE_TYPE": {
        "vi": "Loại nghỉ không hợp lệ",
        "en": "Invalid leave type",
    },
    # =========================================================================
    # ONBOARDING MODULE
    # =========================================================================
    "ONBOARDING_ERROR": {
        "vi": "Lỗi module onboarding",
        "en": "Onboarding module error",
    },
    "ONBOARDING_PROCESS_NOT_FOUND_SHORT": {
        "vi": "Không tìm thấy quy trình onboarding",
        "en": "Onboarding process not found",
    },
    # =========================================================================
    # ADMIN / SHARED GENERIC
    # =========================================================================
    "ADMIN_ACTION_REQUIRED": {
        "vi": "Cần quyền quản trị để thực hiện hành động này",
        "en": "Admin access required",
    },
    "VALIDATION_ERROR": {
        "vi": "Vui lòng kiểm tra lại thông tin đã nhập.",
        "en": "Please check your input.",
    },
    "NETWORK_ERROR": {
        "vi": "Lỗi kết nối mạng. Vui lòng kiểm tra kết nối và thử lại.",
        "en": "Network error. Please check your connection and try again.",
    },
    "TIMEOUT": {
        "vi": "Yêu cầu đã hết thời gian chờ. Vui lòng thử lại.",
        "en": "Request timed out. Please try again.",
    },
    "UNKNOWN_ERROR": {
        "vi": "Đã xảy ra lỗi không xác định. Vui lòng thử lại.",
        "en": "An unknown error occurred. Please try again.",
    },
    "ESS_ERROR": {
        "vi": "Lỗi hệ thống tự phục vụ",
        "en": "Self-service system error",
    },
    "ESS_FORBIDDEN": {
        "vi": "Không thể truy cập tài nguyên này",
        "en": "Cannot access this resource",
    },
    "ESS_NOT_FOUND": {
        "vi": "Không tìm thấy tài nguyên",
        "en": "Resource not found",
    },
    # Extra codes synced from frontend
    "NOT_FOUND": {
        "vi": "Không tìm thấy tài nguyên yêu cầu.",
        "en": "Requested resource not found.",
    },
    "FORBIDDEN": {
        "vi": "Bạn không có quyền thực hiện thao tác này.",
        "en": "You do not have permission to perform this action.",
    },
    "INTERNAL_ERROR": {
        "vi": "Lỗi máy chủ. Vui lòng thử lại sau.",
        "en": "Server error. Please try again later.",
    },
    "AUTH_SETUP_REQUIRED": {
        "vi": "Hệ thống chưa được thiết lập. Vui lòng chạy trình hướng dẫn cài đặt.",
        "en": "System has not been set up. Please run the setup wizard.",
    },
    "AUTH_PASSWORD_TOO_WEAK": {
        "vi": "Mật khẩu phải có ít nhất 8 ký tự, bao gồm chữ hoa, chữ thường và số.",
        "en": "Password must be at least 8 characters with uppercase, lowercase, and numbers.",
    },
    "ONBOARDING_NOT_FOUND": {
        "vi": "Không tìm thấy quy trình onboarding.",
        "en": "Onboarding process not found.",
    },
    "ONBOARDING_ALREADY_EXISTS": {
        "vi": "Nhân viên này đã có quy trình onboarding đang chạy.",
        "en": "This employee already has an active onboarding process.",
    },
    "AI_POLICY_CONSENT_REQUIRED": {
        "vi": "Bạn cần đồng ý chính sách dữ liệu trước khi bật tính năng AI.",
        "en": "You must accept the data policy before enabling AI features.",
    },
    "AI_CAPABILITY_CONSENT_REQUIRED": {
        "vi": "Bạn cần đồng ý điều khoản sử dụng cho tính năng này.",
        "en": "You must accept the terms for this capability.",
    },
    "AI_DEPLOYMENT_KEY_MISSING": {
        "vi": "Deployment key chưa được cấu hình. Liên hệ quản trị hệ thống.",
        "en": "Deployment key not configured. Contact system administrator.",
    },
    "ADMIN_ERROR": {
        "vi": "Lỗi module quản trị.",
        "en": "Admin module error.",
    },
    # =========================================================================
    # EMAIL TEMPLATES
    # =========================================================================
    "EMAIL_INTERVIEW_SUBJECT": {
        "vi": "Thư mời phỏng vấn - {name}",
        "en": "Interview Invitation - {name}",
    },
    "EMAIL_OFFER_SUBJECT": {
        "vi": "Chúc mừng bạn đã trúng tuyển - Vroom HR",
        "en": "Congratulations - You're Hired - Vroom HR",
    },
    "GENERAL_ERROR": {
        "vi": "Lỗi hệ thống ({code})",
        "en": "System error ({code})",
    },
}


def get_request_language(request: Request) -> str:
    """Extract language preference from the Accept-Language header.

    Args:
        request: The FastAPI Request object.

    Returns:
        Language code "vi" or "en". Defaults to "vi" if the header is absent,
        empty, or does not start with a supported language prefix.
    """
    accept_lang = request.headers.get("Accept-Language", "vi")
    if accept_lang and accept_lang.strip().lower().startswith("en"):
        return "en"
    return "vi"


def get_message(code: str, lang: str = "vi") -> str:
    """Get a user-facing message by its error code.

    Args:
        code: The error code (e.g. "AUTH_INVALID_CREDENTIALS").
        lang: Language code ("vi" or "en"). Defaults to "vi".

    Returns:
        The message string in the requested language.
        Falls back to the error code if not found.
    """
    entry = MESSAGES.get(code)
    if entry is None:
        return code
    return entry.get(lang, code)


def resolve_error_message(exc: object, lang: str = "vi") -> str:
    """Pick the message an API error response should carry for ``exc``.

    Every module's domain exceptions follow the same shape: a class-level
    ``message`` default plus an ``__init__`` that assigns ``self.message``
    only when the caller supplies context (the offending CIDR, the payslip
    id, the reason a promotion was blocked). Translating by error code alone
    throws that context away, because the catalog is keyed by code and has
    nowhere to put per-instance detail.

    So an instance-level ``message`` -- the marker that a caller built one --
    wins, and everything else reads from the localized catalog. When the code
    is missing from the catalog, ``get_message`` would hand back the bare
    code; the exception's own default message is more useful than that.

    Args:
        exc: The domain exception being rendered.
        lang: Language code ("vi" or "en"). Defaults to "vi".

    Returns:
        The message to place in the error response body.
    """
    custom = vars(exc).get("message")
    if isinstance(custom, str) and custom:
        return custom

    code = getattr(exc, "error_code", None)
    default = getattr(exc, "message", None)
    if not isinstance(code, str):
        return default if isinstance(default, str) else ""

    message = get_message(code, lang)
    if message == code and isinstance(default, str) and default:
        return default
    return message


def get_error_detail(code: str, lang: str = "vi") -> dict[str, str]:
    """Get a structured error detail dict for use in HTTPException.

    Args:
        code: The error code (e.g. "AUTH_INVALID_CREDENTIALS").
        lang: Language code ("vi" or "en"). Defaults to "vi".

    Returns:
        A dict with "code" and "message" keys for use in HTTPException detail.
    """
    return {"code": code, "message": get_message(code, lang)}


class MessageCodes:
    """Constants for all message codes to enable IDE autocompletion.

    Usage:
        from src.shared.messages import get_message, MessageCodes
        msg = get_message(MessageCodes.AUTH_INVALID_CREDENTIALS)
    """

    # Identity & Auth
    AUTH_ERROR = "AUTH_ERROR"
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    AUTH_INVALID_STATE = "AUTH_INVALID_STATE"
    AUTH_GOOGLE_ERROR = "AUTH_GOOGLE_ERROR"
    AUTH_ACCESS_DENIED = "AUTH_ACCESS_DENIED"
    AUTH_INSUFFICIENT_SCOPE = "AUTH_INSUFFICIENT_SCOPE"
    AUTH_INVALID_TOKEN = "AUTH_INVALID_TOKEN"
    AUTH_RATE_LIMITED = "AUTH_RATE_LIMITED"
    AUTH_SETUP_ALREADY_COMPLETED = "AUTH_SETUP_ALREADY_COMPLETED"
    DOMAIN_NOT_ALLOWED = "DOMAIN_NOT_ALLOWED"
    ADMIN_ACCESS_DENIED = "ADMIN_ACCESS_DENIED"
    TOKEN_REFRESHED = "TOKEN_REFRESHED"
    LOGGED_OUT = "LOGGED_OUT"
    AI_CONFIG_INVALID = "AI_CONFIG_INVALID"
    AI_CONNECTION_FAILED = "AI_CONNECTION_FAILED"
    AI_CONFIG_NO_PROVIDER = "AI_CONFIG_NO_PROVIDER"
    CLASSIFICATION_ROLLOUT_BLOCKED = "CLASSIFICATION_ROLLOUT_BLOCKED"
    CLASSIFICATION_ROLLOUT_INVALID = "CLASSIFICATION_ROLLOUT_INVALID"
    DOMAIN_ERROR = "DOMAIN_ERROR"
    OAUTH_VALIDATION_FAILED = "OAUTH_VALIDATION_FAILED"
    USER_NOT_FOUND = "USER_NOT_FOUND"
    LAST_ADMIN_ERROR = "LAST_ADMIN_ERROR"
    SUPER_ADMIN_PROTECTED = "SUPER_ADMIN_PROTECTED"
    WHITELIST_INVALID_FORMAT = "WHITELIST_INVALID_FORMAT"
    WHITELIST_DUPLICATE = "WHITELIST_DUPLICATE"
    WHITELIST_NOT_FOUND = "WHITELIST_NOT_FOUND"
    WHITELIST_READONLY = "WHITELIST_READONLY"
    ORG_AI_PROVIDER_NOT_CONFIGURED = "ORG_AI_PROVIDER_NOT_CONFIGURED"
    # Employee
    EMPLOYEE_ERROR = "EMPLOYEE_ERROR"
    EMPLOYEE_DUPLICATE_EMAIL = "EMPLOYEE_DUPLICATE_EMAIL"
    EMPLOYEE_NOT_FOUND = "EMPLOYEE_NOT_FOUND"
    DEPARTMENT_NOT_FOUND = "DEPARTMENT_NOT_FOUND"
    POSITION_NOT_FOUND = "POSITION_NOT_FOUND"
    DEPARTMENT_HAS_EMPLOYEES = "DEPARTMENT_HAS_EMPLOYEES"
    POSITION_HAS_EMPLOYEES = "POSITION_HAS_EMPLOYEES"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
    # Recruitment
    RECRUITMENT_ERROR = "RECRUITMENT_ERROR"
    AI_AUTOMATION_DISABLED = "AI_AUTOMATION_DISABLED"
    CANDIDATE_NOT_FOUND = "CANDIDATE_NOT_FOUND"
    CV_DOCUMENT_NOT_FOUND = "CV_DOCUMENT_NOT_FOUND"
    GMAIL_NOT_CONNECTED = "GMAIL_NOT_CONNECTED"
    CV_FILE_MISSING = "CV_FILE_MISSING"
    STORAGE_SERVICE_UNAVAILABLE = "STORAGE_SERVICE_UNAVAILABLE"
    PIPELINE_TIMEOUT = "PIPELINE_TIMEOUT"
    OCR_EXTRACTION_FAILED = "OCR_EXTRACTION_FAILED"
    LLM_PARSE_FAILED = "LLM_PARSE_FAILED"
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"
    RECRUITMENT_ACCESS_DENIED = "RECRUITMENT_ACCESS_DENIED"
    RECRUITMENT_INBOX_NOT_FOUND = "RECRUITMENT_INBOX_NOT_FOUND"
    CALENDAR_CONFLICT_NOT_FOUND = "CALENDAR_CONFLICT_NOT_FOUND"
    # Attendance
    ATTENDANCE_ERROR = "ATTENDANCE_ERROR"
    ALREADY_CHECKED_IN = "ALREADY_CHECKED_IN"
    NOT_CHECKED_IN = "NOT_CHECKED_IN"
    OFFICE_NETWORK_REQUIRED = "OFFICE_NETWORK_REQUIRED"
    CHECKED_IN_SUCCESS = "CHECKED_IN_SUCCESS"
    CHECKED_OUT_SUCCESS = "CHECKED_OUT_SUCCESS"
    ATTENDANCE_CORRECTED_SUCCESS = "ATTENDANCE_CORRECTED_SUCCESS"
    # Leave
    LEAVE_TYPE_NOT_FOUND = "LEAVE_TYPE_NOT_FOUND"
    LEAVE_REQUEST_NOT_FOUND = "LEAVE_REQUEST_NOT_FOUND"
    INSUFFICIENT_LEAVE_BALANCE = "INSUFFICIENT_LEAVE_BALANCE"
    LEAVE_OVERLAP = "LEAVE_OVERLAP"
    INVALID_LEAVE_STATUS_TRANSITION = "INVALID_LEAVE_STATUS_TRANSITION"
    LEAVE_DATE_IN_PAST = "LEAVE_DATE_IN_PAST"
    # Overtime
    OVERTIME_REQUEST_NOT_FOUND = "OVERTIME_REQUEST_NOT_FOUND"
    OVERTIME_LIMIT_EXCEEDED = "OVERTIME_LIMIT_EXCEEDED"
    OVERTIME_END_BEFORE_START = "OVERTIME_END_BEFORE_START"
    OVERTIME_OVERLAP = "OVERTIME_OVERLAP"
    # Schedule
    SCHEDULE_NOT_FOUND = "SCHEDULE_NOT_FOUND"
    # Employee Request
    REQUEST_NOT_FOUND = "REQUEST_NOT_FOUND"
    REQUEST_NOT_OWNED = "REQUEST_NOT_OWNED"
    REQUEST_NOT_CANCELLABLE = "REQUEST_NOT_CANCELLABLE"
    REQUEST_NOT_REVIEWABLE = "REQUEST_NOT_REVIEWABLE"
    # Payslip
    PAYSLIP_ERROR = "PAYSLIP_ERROR"
    PAYSLIP_NOT_FOUND = "PAYSLIP_NOT_FOUND"
    PAYSLIP_ALREADY_EXISTS = "PAYSLIP_ALREADY_EXISTS"
    PAYSLIP_ALREADY_PUBLISHED = "PAYSLIP_ALREADY_PUBLISHED"
    PAYSLIP_NOT_DRAFT = "PAYSLIP_NOT_DRAFT"
    PAYSLIP_NOT_PUBLISHED = "PAYSLIP_NOT_PUBLISHED"
    INVALID_PAYSLIP_ID = "INVALID_PAYSLIP_ID"
    # Gmail
    GMAIL_ERROR = "GMAIL_ERROR"
    MESSAGE_NOT_FOUND = "MESSAGE_NOT_FOUND"
    GMAIL_SEND_FAILED = "GMAIL_SEND_FAILED"
    GMAIL_FETCH_ERROR = "GMAIL_FETCH_ERROR"
    EMAIL_NOT_AWAITING_RECOVERY = "EMAIL_NOT_AWAITING_RECOVERY"
    INVALID_EMAIL_CATEGORY = "INVALID_EMAIL_CATEGORY"
    GMAIL_SYNC_SUCCESS = "GMAIL_SYNC_SUCCESS"
    ALL_EMAILS_CLASSIFIED = "ALL_EMAILS_CLASSIFIED"
    # Assistant
    ASSISTANT_ERROR = "ASSISTANT_ERROR"
    LAST_MESSAGE_FROM_USER = "LAST_MESSAGE_FROM_USER"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    # Generic
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    CONNECTION_TEST_SUCCEEDED = "CONNECTION_TEST_SUCCEEDED"
    CONNECTION_TEST_FAILED = "CONNECTION_TEST_FAILED"
    # Email templates
    EMAIL_INTERVIEW_SUBJECT = "EMAIL_INTERVIEW_SUBJECT"
    EMAIL_OFFER_SUBJECT = "EMAIL_OFFER_SUBJECT"
    EMAIL_INTERVIEW_BODY = "EMAIL_INTERVIEW_BODY"
    EMAIL_OFFER_BODY = "EMAIL_OFFER_BODY"


# Backwards-compatible alias for existing code that imports MESSAGE_CODES
MESSAGE_CODES = MessageCodes
