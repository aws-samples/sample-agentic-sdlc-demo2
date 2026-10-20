"""Unit tests for the Post_Confirmation_Trigger handler (task 4.5, Part A).

Exercises ``post_confirmation_role_assign.handler`` through its real public
surface (a Cognito post-confirmation event in, the event back out) against a
tiny fake ``cognito-idp`` client that *records* the ``admin_add_user_to_group``
calls the handler makes. The handler's lazy ``_cognito_idp()`` factory is
monkeypatched to return the fake (mirrors how ``test_project_api.py``
monkeypatches ``project_api._s3``), so no AWS calls are made and ``boto3``'s real
client is never constructed.

These tests assert *observable* outcomes — the exact ``UserPoolId`` /
``Username`` / ``GroupName`` arguments actually passed to
``admin_add_user_to_group``, and the rejection text actually printed to stdout
(captured via ``capsys``) — rather than re-deriving the handler's branch
decisions, so they cannot mirror-test the implementation.

Covers (design Section 1b):
  * Valid role -> exactly one ``admin_add_user_to_group`` with the right args;
    event returned unchanged (Req 1.2).
  * Invalid role -> NO call + a rejection log entry naming the rejected value;
    event returned (Req 1.3).
  * Missing ``custom:role`` attribute -> NO call + a rejection log entry; event
    returned (Req 1.3).
  * Each of the six valid groups -> ``admin_add_user_to_group`` with that group
    (Req 1.2).

Validates: Requirements 1.2, 1.3
"""

import os
import sys

import pytest

# Make the Lambda module importable regardless of pytest's rootdir/cwd:
# post_confirmation_role_assign.py lives one directory up from this tests/
# package (mirrors test_project_api.py's sys.path insertion).
_CUSTOM_RESOURCES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _CUSTOM_RESOURCES_DIR not in sys.path:
    sys.path.insert(0, _CUSTOM_RESOURCES_DIR)

import post_confirmation_role_assign as trigger  # noqa: E402

USER_POOL_ID = "us-east-1_TestPool"
USERNAME = "new-user@example.com"

# The six valid Cognito groups (mirrors the requirements Glossary / design
# Section 1b). Declared here independently of the handler so the parametrized
# test asserts against an explicit expected set rather than reusing
# ``trigger.VALID_GROUPS`` (which would mirror the implementation).
SIX_VALID_GROUPS = [
    "requirements-analyst",
    "system-architect",
    "software-developer",
    "qa-engineer",
    "devops-engineer",
    "maintenance-specialist",
]


# ---------------------------------------------------------------------------
# Fake cognito-idp client + fixtures
# ---------------------------------------------------------------------------


class FakeCognitoIdp:
    """Records every ``admin_add_user_to_group`` call as a kwargs dict."""

    def __init__(self):
        self.add_to_group_calls = []

    def admin_add_user_to_group(self, **kwargs):
        self.add_to_group_calls.append(kwargs)
        return {}


@pytest.fixture
def cognito(monkeypatch):
    """Install a fresh fake cognito-idp client behind the lazy factory."""
    fake = FakeCognitoIdp()
    monkeypatch.setattr(trigger, "_cognito_idp", lambda: fake)
    return fake


def _event(role=None, *, include_role_attr=True, username=USERNAME,
           user_pool_id=USER_POOL_ID):
    """Build a Cognito post-confirmation event.

    When ``include_role_attr`` is False, the ``custom:role`` attribute is
    omitted entirely (the missing-attribute case). Otherwise ``role`` is placed
    under ``custom:role``.
    """
    user_attributes = {"email": username}
    if include_role_attr:
        user_attributes["custom:role"] = role
    return {
        "userPoolId": user_pool_id,
        "userName": username,
        "request": {"userAttributes": user_attributes},
        "response": {},
    }


# ===========================================================================
# 1. Valid role -> single admin_add_user_to_group with correct args (Req 1.2)
# ===========================================================================


def test_valid_role_adds_user_to_matching_group(cognito):
    """A valid role triggers exactly one group add with the right arguments."""
    event = _event("system-architect")

    result = trigger.handler(event, None)

    # Exactly one call, with the precise UserPoolId/Username/GroupName.
    assert len(cognito.add_to_group_calls) == 1
    call = cognito.add_to_group_calls[0]
    assert call == {
        "UserPoolId": USER_POOL_ID,
        "Username": USERNAME,
        "GroupName": "system-architect",
    }

    # Cognito contract: the trigger returns the event unchanged.
    assert result is event


def test_valid_role_does_not_log_a_rejection(cognito, capsys):
    """The valid-role path must not emit a rejection log line (Req 1.2 vs 1.3)."""
    trigger.handler(_event("requirements-analyst"), None)

    out = capsys.readouterr().out
    assert "Rejected role value" not in out


# ===========================================================================
# 2. Invalid role -> no call + logged rejection naming the value (Req 1.3)
# ===========================================================================


def test_invalid_role_makes_no_call_and_logs_rejection(cognito, capsys):
    """An unknown role assigns no group and logs the rejected value verbatim."""
    event = _event("wizard")

    result = trigger.handler(event, None)

    # No group assignment happened.
    assert cognito.add_to_group_calls == []

    # The rejected value appears in the captured stdout (real log output).
    out = capsys.readouterr().out
    assert "Rejected role value" in out
    assert "wizard" in out
    # The affected username is identified in the log entry too.
    assert USERNAME in out

    # Event still returned unchanged so sign-up is not blocked.
    assert result is event


# ===========================================================================
# 3. Missing custom:role attribute -> no call + logged rejection (Req 1.3)
# ===========================================================================


def test_missing_role_attribute_makes_no_call_and_logs_rejection(cognito, capsys):
    """When custom:role is absent entirely, no group is assigned and it is logged."""
    event = _event(include_role_attr=False)

    result = trigger.handler(event, None)

    assert cognito.add_to_group_calls == []
    out = capsys.readouterr().out
    assert "Rejected role value" in out
    # The empty role default ('') is what gets rejected; the username is named.
    assert USERNAME in out
    assert result is event


def test_empty_role_value_makes_no_call_and_logs_rejection(cognito, capsys):
    """An explicit empty-string role is rejected the same as a missing one."""
    trigger.handler(_event(""), None)

    assert cognito.add_to_group_calls == []
    assert "Rejected role value" in capsys.readouterr().out


# ===========================================================================
# 4. Each of the six valid groups is honored (Req 1.2)
# ===========================================================================


@pytest.mark.parametrize("group", SIX_VALID_GROUPS)
def test_each_valid_group_is_assigned(cognito, group):
    """Every one of the six defined groups results in a matching group add."""
    trigger.handler(_event(group), None)

    assert len(cognito.add_to_group_calls) == 1
    assert cognito.add_to_group_calls[0]["GroupName"] == group
    assert cognito.add_to_group_calls[0]["UserPoolId"] == USER_POOL_ID
    assert cognito.add_to_group_calls[0]["Username"] == USERNAME
