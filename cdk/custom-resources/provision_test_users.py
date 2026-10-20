"""CloudFormation custom resource: provision Cognito test users (consolidated).

Replaces the previous pattern of THREE ``AwsCustomResource`` constructs per
test user (adminCreateUser + adminSetUserPassword + adminAddUserToGroup) with a
SINGLE Lambda-backed custom resource that provisions a whole list of users in
one shot. The old pattern emitted ~3 ``Custom::AWS`` resources (plus IAM) per
user and pushed the synthesized template past CloudFormation's hard 1 MB limit;
this collapses all of that into one resource + one function.

Behavior:
  * CloudFormation lifecycle (event carries ``ResponseURL``):
      - ``Create`` / ``Update`` -> provision every user (idempotent).
      - ``Delete``              -> remove every user.
      Always signals SUCCESS/FAILED back to the pre-signed ``ResponseURL``.
  * Direct invocation (no ``ResponseURL``): provision from env and return a
    summary. Lets the function be invoked directly to reconcile users without
    a stack update.

The user list and pool id are read from the event's ``ResourceProperties``
when present, otherwise from the ``TEST_USERS`` / ``USER_POOL_ID`` env vars
(both set by CDK to the same values), so direct invocation needs no payload.

Idempotent: ``adminCreateUser`` swallows ``UsernameExistsException`` and the
password/group calls are safe to repeat.
"""

import json
import os
import urllib.request

import boto3

_cognito = boto3.client("cognito-idp")


def _resolve(event):
    """Resolve (user_pool_id, users) from ResourceProperties, falling back to env."""
    props = event.get("ResourceProperties", {}) if isinstance(event, dict) else {}
    pool_id = props.get("UserPoolId") or os.environ.get("USER_POOL_ID", "")
    users = props.get("Users")
    if users is None:
        users = json.loads(os.environ.get("TEST_USERS", "[]"))
    return pool_id, users


def _provision(pool_id, users):
    for u in users:
        email = u["email"]
        password = u["password"]
        group = u["group"]
        role = u.get("role", "")
        attrs = [
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
        ]
        if role:
            attrs.append({"Name": "custom:role", "Value": role})
        try:
            _cognito.admin_create_user(
                UserPoolId=pool_id,
                Username=email,
                MessageAction="SUPPRESS",
                UserAttributes=attrs,
            )
        except _cognito.exceptions.UsernameExistsException:
            pass  # idempotent — already created
        _cognito.admin_set_user_password(
            UserPoolId=pool_id, Username=email, Password=password, Permanent=True
        )
        _cognito.admin_add_user_to_group(
            UserPoolId=pool_id, Username=email, GroupName=group
        )


def _delete(pool_id, users):
    for u in users:
        try:
            _cognito.admin_delete_user(UserPoolId=pool_id, Username=u["email"])
        except _cognito.exceptions.UserNotFoundException:
            pass
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            print(f"[PROVISION_TEST_USERS] delete error for {u.get('email')}: {exc}")


def _send(event, status, reason):
    """Signal the CloudFormation pre-signed ResponseURL."""
    body = json.dumps(
        {
            "Status": status,
            "Reason": reason,
            "PhysicalResourceId": event.get("PhysicalResourceId") or "test-users",
            "StackId": event["StackId"],
            "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"],
            "Data": {},
        }
    ).encode("utf-8")
    response_url = event["ResponseURL"]
    if not response_url.lower().startswith("https://"):
        raise ValueError("refusing non-https CloudFormation ResponseURL")
    req = urllib.request.Request(
        response_url,
        data=body,
        method="PUT",
        headers={"content-type": "", "content-length": str(len(body))},
    )
    urllib.request.urlopen(req)  # noqa: S310  # nosec B310 https-validated pre-signed URL


def handler(event, context):
    # Direct invocation (no CloudFormation envelope): reconcile from env/props.
    if not isinstance(event, dict) or "ResponseURL" not in event:
        pool_id, users = _resolve(event if isinstance(event, dict) else {})
        _provision(pool_id, users)
        return {"provisioned": [u["email"] for u in users]}

    # CloudFormation custom-resource lifecycle.
    try:
        pool_id, users = _resolve(event)
        request_type = event.get("RequestType")
        if request_type in ("Create", "Update"):
            _provision(pool_id, users)
        elif request_type == "Delete":
            _delete(pool_id, users)
        _send(event, "SUCCESS", "ok")
    except Exception as exc:  # noqa: BLE001 — must always signal CFN
        print(f"[PROVISION_TEST_USERS] error: {exc}")
        _send(event, "FAILED", str(exc))
