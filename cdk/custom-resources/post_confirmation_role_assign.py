"""Post-confirmation trigger: assign a confirmed user to their selected role group.

Attached to the Cognito User Pool ``postConfirmation`` trigger. The user's
``custom:role`` attribute names the Cognito group to add them to. Because the
companion ``preSignUp`` trigger sets ``autoConfirmUser = true``, this
post-confirmation trigger fires immediately after sign-up (design Section 1b).

Logic (design Section 1b, Req 1.2/1.3):
  * Read ``custom:role`` from the event's user attributes.
  * If it is one of the six valid Cognito groups, add the user to that group
    via ``cognito-idp:AdminAddUserToGroup`` (Req 1.2).
  * Otherwise assign the user to NO group and log an error entry identifying the
    rejected role value (Req 1.3).
  * Always return the event unchanged -- Cognito requires the trigger to return
    the event so sign-up is not blocked.
"""

import boto3

# The six defined Cognito groups (design Section 1b, requirements Glossary).
# A role recorded on the user that is not in this set is rejected (Req 1.3).
VALID_GROUPS = {
    "requirements-analyst",
    "system-architect",
    "software-developer",
    "qa-engineer",
    "devops-engineer",
    "maintenance-specialist",
}

# ---------------------------------------------------------------------------
# AWS clients (module-level lazy init for Lambda container reuse; mirrors the
# ``_s3()`` pattern in project_api.py).
# ---------------------------------------------------------------------------

_cognito_idp_client = None


def _cognito_idp():
    global _cognito_idp_client
    if _cognito_idp_client is None:
        _cognito_idp_client = boto3.client("cognito-idp")
    return _cognito_idp_client


def handler(event, context):
    """Cognito post-confirmation Lambda entry point.

    Defensive against a missing ``request``/``userAttributes`` key: a malformed
    event simply yields an empty role, which is rejected (no group assigned)
    rather than raising and blocking the sign-up flow.
    """
    request = event.get("request") or {}
    user_attributes = request.get("userAttributes") or {}
    role = user_attributes.get("custom:role", "")

    username = event.get("userName", "")

    if role in VALID_GROUPS:
        # Req 1.2: add the confirmed user to the group matching their role.
        _cognito_idp().admin_add_user_to_group(
            UserPoolId=event["userPoolId"],
            Username=username,
            GroupName=role,
        )
    else:
        # Req 1.3: assign no group and record an error log entry identifying the
        # rejected role value.
        print(
            f"[POST_CONFIRM] Rejected role value: {role!r}; "
            f"user {username} assigned to no group"
        )

    # Cognito requires the trigger to return the event unchanged.
    return event
