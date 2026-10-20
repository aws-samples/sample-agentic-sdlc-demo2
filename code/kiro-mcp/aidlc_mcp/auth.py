"""Pluggable persona authentication (design Section 2b, Requirement 1).

Authentication is isolated behind the :class:`CredentialProvider` protocol so
Authentication Option 1b ships now and Option 1a (interactive OAuth-PKCE) can be
added later without changing any tool code.

Providers return the Cognito **ID token** (not the access token) because the
Project API's API Gateway Cognito authorizer reads ``cognito:groups`` from it.
Tokens live only in memory and are never logged, echoed, or returned in tool
output (Requirement 1.6).

The Cognito client is injected so the providers are unit-testable with a fake;
:func:`build_credential_provider` lazily creates a real ``boto3`` client.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from typing import Callable, List, Optional, Protocol, runtime_checkable

from .config import Config
from .errors import AuthError


def decode_jwt_claims(token: str) -> dict:
    """Return the (unverified) claims of a JWT payload segment.

    Signature is NOT verified here — the token is only *used* against the
    Project API, which verifies it server-side. We read claims locally solely to
    know the token's expiry and the caller's ``cognito:groups``. Returns ``{}``
    on any decode failure.
    """
    try:
        segments = token.split(".")
        if len(segments) < 2:
            return {}
        payload = segments[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def groups_from_claims(claims: dict) -> List[str]:
    """Extract ``cognito:groups`` as a list (handles list or CSV string)."""
    raw = claims.get("cognito:groups", [])
    if isinstance(raw, list):
        return [g for g in raw if isinstance(g, str) and g]
    if isinstance(raw, str):
        return [g for g in raw.split(",") if g]
    return []


@runtime_checkable
class CredentialProvider(Protocol):
    """The single auth interface the tool layer depends on."""

    def get_id_token(self) -> str:
        """Return a currently-valid Cognito ID token, refreshing as needed."""
        ...

    def get_claims(self) -> dict:
        """Return the (unverified) claims of the current ID token."""
        ...

    def get_groups(self) -> List[str]:
        """Return the caller's Cognito groups (persona roles)."""
        ...


def _auth_error_from(exc: Exception) -> AuthError:
    """Map a boto3/Cognito exception to a non-sensitive AuthError.

    Includes the Cognito error *code* (e.g. ``NotAuthorizedException``) which is
    safe and helpful, but never the credential, password, or token.
    """
    code = None
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code")
    label = code or type(exc).__name__
    return AuthError(f"Cognito authentication failed ({label}).")


class _CognitoTokenProvider:
    """Base provider: caches the ID token and refreshes it before expiry.

    Args:
        cognito_client: an object exposing ``initiate_auth(**kwargs)`` (a boto3
            ``cognito-idp`` client, or a fake in tests).
        client_id: the Cognito app client id.
        refresh_margin_seconds: refresh when within this margin of expiry.
        now_fn: injectable clock (defaults to ``time.time``).
    """

    def __init__(
        self,
        cognito_client,
        client_id: str,
        refresh_margin_seconds: int = 300,
        now_fn: Callable[[], float] = time.time,
    ):
        self._client = cognito_client
        self._client_id = client_id
        self._margin = refresh_margin_seconds
        self._now = now_fn
        self._id_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._expires_at: float = 0.0

    # -- overridable auth steps -------------------------------------------
    def _initial_auth(self) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError

    def _refresh_auth(self) -> dict:
        return self._initiate_auth(
            "REFRESH_TOKEN_AUTH", {"REFRESH_TOKEN": self._refresh_token}
        )

    # -- shared machinery -------------------------------------------------
    def _initiate_auth(self, flow: str, params: dict) -> dict:
        try:
            resp = self._client.initiate_auth(
                ClientId=self._client_id, AuthFlow=flow, AuthParameters=params
            )
        except Exception as exc:  # noqa: BLE001 - normalized to AuthError
            raise _auth_error_from(exc) from None
        challenge = resp.get("ChallengeName") if isinstance(resp, dict) else None
        if challenge:
            raise AuthError(
                f"Cognito requires a challenge ({challenge}) that this "
                "non-interactive provider cannot satisfy; complete the user's "
                "one-time password change out of band."
            )
        result = (resp or {}).get("AuthenticationResult") or {}
        if not result.get("IdToken"):
            raise AuthError("Cognito did not return an ID token.")
        return result

    def _apply_result(self, result: dict) -> None:
        self._id_token = result["IdToken"]
        # REFRESH_TOKEN_AUTH does not return a new refresh token; keep the old.
        if result.get("RefreshToken"):
            self._refresh_token = result["RefreshToken"]
        expires_in = result.get("ExpiresIn")
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            self._expires_at = self._now() + float(expires_in)
        else:
            exp = decode_jwt_claims(self._id_token).get("exp")
            self._expires_at = float(exp) if isinstance(exp, (int, float)) else 0.0

    def _needs_token(self) -> bool:
        return self._id_token is None or self._now() >= (self._expires_at - self._margin)

    def _ensure_token(self) -> None:
        if self._id_token is None:
            self._apply_result(self._initial_auth())
            return
        if self._now() >= (self._expires_at - self._margin):
            if self._refresh_token:
                self._apply_result(self._refresh_auth())
            else:
                self._apply_result(self._initial_auth())

    # -- public interface -------------------------------------------------
    def get_id_token(self) -> str:
        self._ensure_token()
        assert self._id_token is not None
        return self._id_token

    def get_claims(self) -> dict:
        return decode_jwt_claims(self.get_id_token())

    def get_groups(self) -> List[str]:
        return groups_from_claims(self.get_claims())


class PasswordCredentialProvider(_CognitoTokenProvider):
    """Option 1b (default): USER_PASSWORD_AUTH with username/password."""

    def __init__(self, cognito_client, client_id, username, password, **kwargs):
        super().__init__(cognito_client, client_id, **kwargs)
        self._username = username
        self._password = password

    def _initial_auth(self) -> dict:
        return self._initiate_auth(
            "USER_PASSWORD_AUTH",
            {"USERNAME": self._username, "PASSWORD": self._password},
        )


class RefreshTokenCredentialProvider(_CognitoTokenProvider):
    """Option 1b variant: start from a pre-obtained refresh token (no password)."""

    def __init__(self, cognito_client, client_id, refresh_token, **kwargs):
        super().__init__(cognito_client, client_id, **kwargs)
        self._refresh_token = refresh_token

    def _initial_auth(self) -> dict:
        return self._refresh_auth()


class OAuthCredentialProvider(_CognitoTokenProvider):
    """Option 1a (future): interactive OAuth-PKCE. Reserved; not implemented."""

    def _initial_auth(self) -> dict:  # pragma: no cover - reserved
        raise NotImplementedError(
            "OAuth (Option 1a) is reserved for a future phase; use auth_mode "
            "'password' or 'refresh_token'."
        )


def build_credential_provider(config: Config, cognito_client=None) -> CredentialProvider:
    """Construct the provider for ``config.auth_mode`` (design Section 2b).

    ``cognito_client`` may be injected (tests); otherwise a ``boto3``
    ``cognito-idp`` client is created lazily.
    """
    if cognito_client is None:
        import boto3  # lazy: keep import-time deps minimal / tests dependency-free

        cognito_client = boto3.client("cognito-idp", region_name=config.aws_region)

    kwargs = dict(
        cognito_client=cognito_client,
        client_id=config.cognito_client_id,
        refresh_margin_seconds=config.token_refresh_margin_seconds,
    )
    if config.auth_mode == "password":
        return PasswordCredentialProvider(
            username=config.persona_username, password=config.persona_password, **kwargs
        )
    if config.auth_mode == "refresh_token":
        return RefreshTokenCredentialProvider(
            refresh_token=config.persona_refresh_token, **kwargs
        )
    if config.auth_mode == "oauth":
        return OAuthCredentialProvider(**kwargs)
    raise AuthError(f"Unsupported auth_mode '{config.auth_mode}'.")
