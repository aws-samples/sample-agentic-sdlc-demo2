import pytest
from aidlc_mcp.config import load_config
from aidlc_mcp.errors import ConfigError

BASE = {
    "AIDLC_PROJECT_API_URL": "https://api.example.com/prod/",
    "AIDLC_COGNITO_USER_POOL_ID": "us-east-1_abc",
    "AIDLC_COGNITO_CLIENT_ID": "clientid",
    "AIDLC_AWS_REGION": "us-east-1",
    "AIDLC_FILES_BUCKET": "aidlc-files-bucket",
}


def test_password_mode_ok_and_strips_trailing_slash():
    env = {**BASE, "AIDLC_AUTH_MODE": "password",
           "AIDLC_PERSONA_USERNAME": "sa@example.com", "AIDLC_PERSONA_PASSWORD": "pw"}  # nosec B105 test fixture
    cfg = load_config(env)
    assert cfg.project_api_url == "https://api.example.com/prod"  # trailing slash stripped
    assert cfg.auth_mode == "password"
    assert cfg.persona_username == "sa@example.com"


def test_defaults_to_password_mode():
    env = {**BASE, "AIDLC_PERSONA_USERNAME": "u", "AIDLC_PERSONA_PASSWORD": "p"}  # nosec B105 test fixture
    assert load_config(env).auth_mode == "password"


@pytest.mark.parametrize("missing", list(BASE.keys()))
def test_missing_required_var_fails_fast_naming_it(missing):
    env = {k: v for k, v in BASE.items() if k != missing}
    env.update({"AIDLC_PERSONA_USERNAME": "u", "AIDLC_PERSONA_PASSWORD": "p"})  # nosec B105 test fixture
    with pytest.raises(ConfigError) as ei:
        load_config(env)
    assert missing in str(ei.value)


def test_password_mode_requires_credentials():
    with pytest.raises(ConfigError):
        load_config({**BASE, "AIDLC_AUTH_MODE": "password"})


def test_refresh_token_mode_requires_token():
    with pytest.raises(ConfigError):
        load_config({**BASE, "AIDLC_AUTH_MODE": "refresh_token"})
    cfg = load_config({**BASE, "AIDLC_AUTH_MODE": "refresh_token",
                       "AIDLC_PERSONA_REFRESH_TOKEN": "rt"})  # nosec B105 test fixture
    assert cfg.persona_refresh_token == "rt"  # nosec B105 test fixture


def test_invalid_auth_mode_rejected():
    with pytest.raises(ConfigError):
        load_config({**BASE, "AIDLC_AUTH_MODE": "magic",
                     "AIDLC_PERSONA_USERNAME": "u", "AIDLC_PERSONA_PASSWORD": "p"})  # nosec B105 test fixture


def test_redacted_hides_secrets():
    env = {**BASE, "AIDLC_PERSONA_USERNAME": "u", "AIDLC_PERSONA_PASSWORD": "supersecret"}  # nosec B105 test fixture
    red = load_config(env).redacted()
    assert red["persona_password"] == "***"
    assert "supersecret" not in str(red)
