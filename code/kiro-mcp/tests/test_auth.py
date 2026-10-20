import pytest
from aidlc_mcp.auth import (
    PasswordCredentialProvider, RefreshTokenCredentialProvider,
    decode_jwt_claims, groups_from_claims, build_credential_provider,
)
from aidlc_mcp.config import load_config
from aidlc_mcp.errors import AuthError
from conftest import FakeCognito, make_id_token


def _clock():
    t = {"v": 1000.0}
    return t, (lambda: t["v"])


def test_password_provider_authenticates_and_exposes_groups():
    tok = make_id_token(groups=["system-architect"])
    cog = FakeCognito(results=[{"AuthenticationResult":
        {"IdToken": tok, "RefreshToken": "rt", "ExpiresIn": 3600}}])
    p = PasswordCredentialProvider(cog, "cid", "u", "pw")
    assert p.get_id_token() == tok
    assert p.get_groups() == ["system-architect"]
    assert cog.calls[0]["AuthFlow"] == "USER_PASSWORD_AUTH"
    assert cog.calls[0]["AuthParameters"]["USERNAME"] == "u"


def test_token_cached_no_reauth_within_validity():
    tok = make_id_token(groups=["qa-engineer"])
    cog = FakeCognito(results=[{"AuthenticationResult": {"IdToken": tok, "RefreshToken": "rt", "ExpiresIn": 3600}}])
    state, now = _clock()
    p = PasswordCredentialProvider(cog, "cid", "u", "pw", now_fn=now)
    p.get_id_token(); p.get_id_token()
    assert len(cog.calls) == 1  # only one auth call


def test_refresh_triggers_at_margin():
    tok1 = make_id_token(groups=["a"]); tok2 = make_id_token(groups=["a"])
    cog = FakeCognito(results=[
        {"AuthenticationResult": {"IdToken": tok1, "RefreshToken": "rt", "ExpiresIn": 3600}},
        {"AuthenticationResult": {"IdToken": tok2, "ExpiresIn": 3600}},  # refresh: no new RT
    ])
    state, now = _clock()
    p = PasswordCredentialProvider(cog, "cid", "u", "pw", refresh_margin_seconds=300, now_fn=now)
    assert p.get_id_token() == tok1
    state["v"] = 1000.0 + 3600 - 200  # within margin -> refresh
    assert p.get_id_token() == tok2
    assert cog.calls[1]["AuthFlow"] == "REFRESH_TOKEN_AUTH"
    assert cog.calls[1]["AuthParameters"]["REFRESH_TOKEN"] == "rt"


def test_auth_failure_is_non_sensitive():
    class Boom(Exception):
        response = {"Error": {"Code": "NotAuthorizedException", "Message": "x"}}
    cog = FakeCognito(error=Boom())
    p = PasswordCredentialProvider(cog, "cid", "u", "supersecretpw")
    with pytest.raises(AuthError) as ei:
        p.get_id_token()
    msg = str(ei.value)
    assert "NotAuthorizedException" in msg
    assert "supersecretpw" not in msg


def test_challenge_is_rejected():
    cog = FakeCognito(results=[{"ChallengeName": "NEW_PASSWORD_REQUIRED"}])
    p = PasswordCredentialProvider(cog, "cid", "u", "pw")
    with pytest.raises(AuthError):
        p.get_id_token()


def test_refresh_token_provider_uses_refresh_flow():
    tok = make_id_token(groups=["devops-engineer"])
    cog = FakeCognito(results=[{"AuthenticationResult": {"IdToken": tok, "ExpiresIn": 3600}}])
    p = RefreshTokenCredentialProvider(cog, "cid", "rt-value")
    assert p.get_id_token() == tok
    assert cog.calls[0]["AuthFlow"] == "REFRESH_TOKEN_AUTH"


def test_build_provider_selects_by_mode():
    env = {"AIDLC_PROJECT_API_URL": "https://a", "AIDLC_COGNITO_USER_POOL_ID": "p",
           "AIDLC_COGNITO_CLIENT_ID": "c", "AIDLC_AWS_REGION": "us-east-1",
           "AIDLC_FILES_BUCKET": "b", "AIDLC_AUTH_MODE": "password",
           "AIDLC_PERSONA_USERNAME": "u", "AIDLC_PERSONA_PASSWORD": "pw"}
    cfg = load_config(env)
    prov = build_credential_provider(cfg, cognito_client=FakeCognito())
    assert isinstance(prov, PasswordCredentialProvider)


def test_decode_and_groups_helpers():
    tok = make_id_token(groups=["r1", "r2"])
    assert groups_from_claims(decode_jwt_claims(tok)) == ["r1", "r2"]
    assert decode_jwt_claims("garbage") == {}
    assert groups_from_claims({"cognito:groups": "a,b"}) == ["a", "b"]
