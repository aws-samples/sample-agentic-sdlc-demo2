"""Error types surfaced by the Platform MCP server.

All errors are safe to show to a persona: they never carry credential or token
values (Requirement 14.4). ``ProjectApiError`` mirrors the frontend's
``ProjectApiError`` (design Section 2c) so the API's structured ``{error, code}``
reaches the persona (e.g. ``FORBIDDEN``, ``ILLEGAL_TRANSITION``,
``REPO_NOT_LINKED``, ``ETAG_MISMATCH``).
"""

from __future__ import annotations

from typing import Optional


class ConfigError(RuntimeError):
    """Raised at startup when a required configuration value is missing.

    The message names the missing variable so the operator can fix it
    (Requirement 13.3). Never includes secret values.
    """


class AuthError(RuntimeError):
    """Raised when Cognito authentication or token refresh fails.

    The message is intentionally non-sensitive (Requirement 1.7 / 14.3): it
    never includes the credential, the password, or any token value.
    """


class ProjectApiError(RuntimeError):
    """Raised when the Project API returns a non-2xx response.

    Carries the HTTP status and the API's structured error ``code`` when
    present so the tool layer can surface a clear, actionable message
    (Requirement 14.1).
    """

    def __init__(self, status: int, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - trivial
        code = f" [{self.code}]" if self.code else ""
        return f"HTTP {self.status}{code}: {self.message}"
