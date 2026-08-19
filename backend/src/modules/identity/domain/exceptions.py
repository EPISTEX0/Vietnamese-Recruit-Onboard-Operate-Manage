"""Domain exceptions for the Identity & Auth module.

This module defines the exception hierarchy used throughout the identity
module to represent authentication and authorization failures.
"""


class AuthError(Exception):
    """Base exception for the identity module.

    All domain-specific exceptions inherit from this class, enabling
    a single exception handler to catch any auth-related error and
    return a consistent JSON error response.

    Attributes:
        status_code: HTTP status code to return to the client.
        error_code: Machine-readable error identifier.
        message: Human-readable error description.
    """

    status_code: int = 500
    error_code: str = "AUTH_ERROR"
    message: str = "Lỗi xác thực hệ thống"

    def __init__(self, message: str | None = None) -> None:
        """Initialize AuthError.

        Args:
            message: Optional custom message override. If not provided,
                the class-level default message is used.
        """
        if message is not None:
            self.message = message
        super().__init__(self.message)


class InvalidStateError(AuthError):
    """CSRF state token is invalid or expired.

    Raised when the OAuth2 callback receives a state parameter that
    cannot be verified (bad signature, expired, or missing).
    """

    status_code = 400
    error_code = "AUTH_INVALID_STATE"
    message = "Trạng thái xác thực không hợp lệ"


class GoogleAuthError(AuthError):
    """Google token exchange or API call failed.

    Raised when the system cannot complete the OAuth2 token exchange
    with Google, or when a Google API call returns an error.
    """

    status_code = 502
    error_code = "AUTH_GOOGLE_ERROR"
    message = "Xác thực Google thất bại"


class AccessDeniedError(AuthError):
    """The account cannot sign in even though the credentials are valid.

    Raised for reasons like a deactivated account or a staff email that
    already has an Employee account of its own.
    """

    status_code = 403
    error_code = "AUTH_ACCESS_DENIED"
    message = "Truy cập bị từ chối. Vui lòng liên hệ quản trị viên."


class SetupAlreadyCompletedError(AuthError):
    """First-Run Setup lost the singleton race or was already completed."""

    status_code = 409
    error_code = "AUTH_SETUP_ALREADY_COMPLETED"
    message = "Hệ thống đã được thiết lập trước đó"


class DomainAccessDeniedError(AuthError):
    """Email domain is not in the Organization's allowed list.

    Raised when a user authenticates successfully with Google but
    their email domain is not present in the Organization's
    allowed_domains configuration.
    """

    status_code = 403
    error_code = "DOMAIN_NOT_ALLOWED"
    message = "Tên miền email không được phép truy cập tổ chức này."


class InsufficientScopeError(AuthError):
    """User did not grant all required OAuth scopes.

    Raised when the user completes OAuth consent but declines one
    or more of the requested permissions (Gmail, Calendar).
    """

    status_code = 400
    error_code = "AUTH_INSUFFICIENT_SCOPE"
    message = "Vui lòng cấp tất cả quyền được yêu cầu"


class InvalidCredentialsError(AuthError):
    """Email or password is incorrect.

    Raised when a user attempts local login with an email that does
    not exist, has no password set, or provides the wrong password.
    """

    status_code = 401
    error_code = "AUTH_INVALID_CREDENTIALS"
    message = "Email hoặc mật khẩu không đúng"


class InvalidTokenError(AuthError):
    """JWT access or refresh token is invalid.

    Raised when a token cannot be decoded, has an invalid signature,
    is expired, or has been revoked.
    """

    status_code = 401
    error_code = "AUTH_INVALID_TOKEN"
    message = "Phiên đăng nhập không hợp lệ hoặc đã hết hạn"


class InvalidResetTokenError(AuthError):
    """Password reset token is missing, invalid, used, or expired.

    Raised when a password reset attempt uses a token that does not
    exist, has already been used, or has expired. The message is kept
    generic so the endpoint cannot be used to probe token validity.
    """

    status_code = 400
    error_code = "AUTH_INVALID_RESET_TOKEN"
    message = "Liên kết đặt lại mật khẩu không hợp lệ hoặc đã hết hạn"


class RateLimitExceededError(AuthError):
    """Too many login attempts from a single IP.

    Raised when the per-IP rate limit (5 requests per minute) is
    exceeded on login-related endpoints.
    """

    status_code = 429
    error_code = "AUTH_RATE_LIMITED"
    message = "Quá nhiều lần đăng nhập. Vui lòng thử lại sau."
