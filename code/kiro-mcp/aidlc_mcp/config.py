"""Configuration loading for the Platform MCP server (design Section 8).

All values come from environment variables (or an explicit mapping, for tests).
Required deployment values are validated at startup and the server *fails fast*
with a clear message naming any missing one (Requirement 13.1 / 13.3). Persona
credentials are validated according to the selected auth mode.

No secret value is ever logged or echoed; this module only reads and stores
values in memory on the returned dataclass.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional

from .errors import ConfigError

# Deployment values required for every auth mode. Sourced from the deployment's
# ``/config.json`` (see the setup docs / design Section 8 mapping table).
_REQUIRED_DEPLOYMENT_VARS = (
    "AIDLC_PROJECT_API_URL",
    "AIDLC_COGNITO_USER_POOL_ID",
    "AIDLC_COGNITO_CLIENT_ID",
    "AIDLC_AWS_REGION",
    "AIDLC_FILES_BUCKET",
)

_VALID_AUTH_MODES = ("password", "refresh_token", "oauth")


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration for the MCP server."""

    project_api_url: str
    cognito_user_pool_id: str
    cognito_client_id: str
    aws_region: str
    files_bucket: str
    auth_mode: str
    # Persona credential (exactly the fields the chosen ``auth_mode`` needs).
    persona_username: Optional[str] = None
    persona_password: Optional[str] = field(default=None, repr=False)
    persona_refresh_token: Optional[str] = field(default=None, repr=False)
    # Operational knobs.
    state_dir: Optional[str] = None
    test_command: Optional[str] = None
    token_refresh_margin_seconds: int = 300

    def redacted(self) -> dict:
        """Return a dict safe for logging (no secret values)."""
        return {
            "project_api_url": self.project_api_url,
            "cognito_user_pool_id": self.cognito_user_pool_id,
            "cognito_client_id": self.cognito_client_id,
            "aws_region": self.aws_region,
            "files_bucket": self.files_bucket,
            "auth_mode": self.auth_mode,
            "persona_username": self.persona_username,
            "persona_password": "***" if self.persona_password else None,
            "persona_refresh_token": "***" if self.persona_refresh_token else None,
            "state_dir": self.state_dir,
            "test_command": self.test_command,
        }


def _require(env: Mapping[str, str], name: str) -> str:
    value = (env.get(name) or "").strip()
    if not value:
        raise ConfigError(
            f"Missing required configuration '{name}'. Set it in the persona "
            "agent's MCP server env (see the .kiro/specs/kiro-native-platform spec)."
        )
    return value


def load_config(env: Optional[Mapping[str, str]] = None) -> Config:
    """Load and validate configuration from ``env`` (defaults to ``os.environ``).

    Raises :class:`ConfigError` naming the first missing/invalid value so the
    server fails fast at startup (Requirement 13.3).
    """
    env = os.environ if env is None else env

    for name in _REQUIRED_DEPLOYMENT_VARS:
        _require(env, name)

    auth_mode = (env.get("AIDLC_AUTH_MODE") or "password").strip().lower()
    if auth_mode not in _VALID_AUTH_MODES:
        raise ConfigError(
            f"AIDLC_AUTH_MODE must be one of {_VALID_AUTH_MODES}; got '{auth_mode}'."
        )

    persona_username = (env.get("AIDLC_PERSONA_USERNAME") or "").strip() or None
    persona_password = env.get("AIDLC_PERSONA_PASSWORD") or None
    persona_refresh_token = env.get("AIDLC_PERSONA_REFRESH_TOKEN") or None

    if auth_mode == "password":
        if not persona_username or not persona_password:
            raise ConfigError(
                "auth_mode 'password' requires AIDLC_PERSONA_USERNAME and "
                "AIDLC_PERSONA_PASSWORD."
            )
    elif auth_mode == "refresh_token":
        if not persona_refresh_token:
            raise ConfigError(
                "auth_mode 'refresh_token' requires AIDLC_PERSONA_REFRESH_TOKEN."
            )
    # auth_mode 'oauth' (Option 1a) is reserved; the provider is not implemented
    # in this spec, so no persona credential is validated here.

    margin_raw = (env.get("AIDLC_TOKEN_REFRESH_MARGIN_SECONDS") or "300").strip()
    try:
        margin = int(margin_raw)
    except ValueError:
        raise ConfigError(
            f"AIDLC_TOKEN_REFRESH_MARGIN_SECONDS must be an integer; got '{margin_raw}'."
        )

    return Config(
        project_api_url=_require(env, "AIDLC_PROJECT_API_URL").rstrip("/"),
        cognito_user_pool_id=_require(env, "AIDLC_COGNITO_USER_POOL_ID"),
        cognito_client_id=_require(env, "AIDLC_COGNITO_CLIENT_ID"),
        aws_region=_require(env, "AIDLC_AWS_REGION"),
        files_bucket=_require(env, "AIDLC_FILES_BUCKET"),
        auth_mode=auth_mode,
        persona_username=persona_username,
        persona_password=persona_password,
        persona_refresh_token=persona_refresh_token,
        state_dir=(env.get("AIDLC_STATE_DIR") or "").strip() or None,
        test_command=(env.get("AIDLC_TEST_COMMAND") or "").strip() or None,
        token_refresh_margin_seconds=margin,
    )
