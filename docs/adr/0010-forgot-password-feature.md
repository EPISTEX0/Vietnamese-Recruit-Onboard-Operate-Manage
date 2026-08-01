# 10. Forgot Password Feature Architecture

* Status: accepted
* Deciders: Core Architecture Team
* Date: 2026-08-01

## Context and Problem Statement

To enhance user autonomy and reduce administrative overhead, the platform requires a self-service "Forgot Password" feature. Currently, users have no mechanism to reset their own passwords if forgotten, necessitating manual intervention by system administrators.

The implementation must be secure, protecting against user enumeration, brute-force attacks, and token-based vulnerabilities, while ensuring a smooth user experience.

## Decision Drivers

- **Security**: Prevent user enumeration, token theft, and brute-force attacks.
- **UX**: User-friendly, self-service recovery process with clear email instructions.
- **Maintainability**: Reliable email delivery, token management, and monitoring.
- **Resilience**: Rate limiting and graceful failure modes.

## Considered Options

1. **Integrated email-based reset link (Magic Link)**: Send a secure link to the registered email address. This is standard, user-friendly, and secure if implemented with short-lived tokens and secure transport.
2. **Security-question-based reset**: Prone to social engineering and difficult to implement securely. Rejected.
3. **Admin-manual-reset-only**: Does not solve the self-service requirement. Rejected.

## Decision Outcome

Selected **Option 1: Integrated email-based reset link (Magic Link)** with strict security measures.

### 1. Persistence Model
- **Table**: `password_reset_tokens`
- **Fields**: `user_id` (FK), `token_hash` (SHA-256), `expires_at` (timestamp), `used_at` (timestamp, nullable), `created_at`, `created_by_ip`.
- **Divergence note**: An earlier draft listed `is_revoked (boolean)`; the implemented schema records `used_at` (the timestamp of consumption) instead. `used_at IS NULL` is the exact inverse of `is_revoked = FALSE`, so the invariant is preserved while additionally keeping an audit trace of when the token was consumed.
- **Invariant**: Only one active (non-expired, unused) token exists per user at any time. A new request invalidates previous tokens for that user.

### 2. Token Security
- **Format**: Raw random high-entropy token generated in the application, stored as a SHA-256 hash in the database.
- **Lifecycle**: Single-use token. Invalidated immediately upon password reset or a new reset request.

### 3. Revocation
- **Triggers**: Password reset success (revokes all refresh tokens), new password request (revokes all existing reset tokens for that user).

### 4. Email Delivery
- **Mechanism**: `SendService` using Gmail Adapter.
- **Template**: HTML formatted email containing the magic link.
- **Failure Mode**: Graceful failure. If email fails to send, log the error internally but return a generic 200 OK response to the client to prevent user enumeration.

### 5. Rate Limiting
- **Limiter**: Redis-based dual-limiter.
    - **IP-based**: Max 3 requests per 15 minutes.
    - **Email-based**: Max 2 requests per 15 minutes.
- **Response**: HTTP 429 Too Many Requests.

### 6. Security & Anti-Enumeration
- **Response Consistency**: Always return a generic "If the account exists, you will receive an email shortly" message, regardless of whether the email is registered.
- **Timing**: The negative path (unknown / inactive / passwordless account) applies a fixed delay before answering so its response time approximates the email-send path; the dual rate limiter additionally throttles probing from one IP and toward one inbox. The email-send path's true latency (Gmail API round-trip) still exceeds the fixed delay, so this is a mitigation, not a perfect constant-time guarantee.

### 7. Frontend
- **Validation**: Client-side validation for form inputs and reset token format (format check + presence, then the token-info endpoint for validity).
- **Flow**: User requests reset -> receives email with magic link -> clicks link -> frontend validates token presence/format -> user submits new password -> backend performs reset -> redirect to login.

### 8. UX
- **Magic Link Expiry**: 15 minutes.

## Positive Consequences

- **Enhanced Security**: Prevents enumeration and protects against token misuse.
- **User Autonomy**: Self-service reset improves user satisfaction.
- **System Integrity**: Rate limiting protects against abuse of the email service.

## Negative Consequences

- **Email Reliability**: Dependent on Gmail/Email provider reliability.
- **Token Management**: Adds complexity to database management and token lifecycle handling.
