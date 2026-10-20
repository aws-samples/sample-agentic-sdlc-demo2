"""Git push webhook Lambda (C7 auto-ingest).

Public API Gateway endpoint (authenticated by **signature**, not Cognito) that
GitHub/GitLab call on push. On a verified push it maps the payload's repository
URL to a project record (by the stored ``repo_url``) and re-triggers the ingest
CodeBuild for that project — so the platform's copy of the source stays in sync
without the developer re-linking.

Security: the request is verified with a shared secret before anything else —
GitHub's ``X-Hub-Signature-256`` HMAC over the raw body, or GitLab's
``X-Gitlab-Token``. An unverified request is rejected 401 before any S3/CodeBuild
access. The secret is read at runtime from Secrets Manager (``GIT_WEBHOOK_SECRET_ARN``).

Reuses the Project API helpers (``_list_records``, ``_maybe_trigger_ingest``,
``_secrets``) since this Lambda is bundled from the same ``custom-resources``
asset — the ingest trigger stays in exactly one place.
"""

import hashlib
import hmac
import json
import os

import project_api

_WEBHOOK_SECRET = None


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _webhook_secret():
    """Fetch (and cache) the webhook shared secret from Secrets Manager."""
    global _WEBHOOK_SECRET
    if _WEBHOOK_SECRET is None:
        arn = os.environ.get("GIT_WEBHOOK_SECRET_ARN")
        if not arn:
            return ""
        _WEBHOOK_SECRET = project_api._secrets().get_secret_value(SecretId=arn)[
            "SecretString"
        ]
    return _WEBHOOK_SECRET


def _headers_lower(event):
    return {k.lower(): v for k, v in (event.get("headers") or {}).items()}


def _verify(event, raw_body, secret):
    """Verify the GitHub HMAC signature or the GitLab token against ``secret``."""
    if not secret:
        return False
    headers = _headers_lower(event)
    gh_sig = headers.get("x-hub-signature-256")
    if gh_sig:
        expected = "sha256=" + hmac.new(
            secret.encode("utf-8"), raw_body.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(gh_sig, expected)
    gl_token = headers.get("x-gitlab-token")
    if gl_token is not None:
        return hmac.compare_digest(gl_token, secret)
    return False


def _normalize(url):
    """Normalize a repo URL for matching (strip trailing slash/.git, lowercase)."""
    if not isinstance(url, str):
        return ""
    u = url.strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    return u.lower()


def _extract_repo_url(payload):
    """Pull an https repo URL out of a GitHub or GitLab push payload."""
    repo = payload.get("repository") or {}
    proj = payload.get("project") or {}
    for candidate in (
        repo.get("clone_url"),
        repo.get("html_url"),
        repo.get("git_http_url"),
        proj.get("git_http_url"),
        proj.get("http_url"),
        proj.get("web_url"),
    ):
        if candidate:
            return candidate
    return None


def handler(event, context):
    """API Gateway proxy entry point for the Git push webhook."""
    raw_body = event.get("body") or ""

    if not _verify(event, raw_body, _webhook_secret()):
        return _response(401, {"error": "invalid signature", "code": "UNAUTHORIZED"})

    try:
        payload = json.loads(raw_body or "{}")
    except (json.JSONDecodeError, TypeError):
        return _response(400, {"error": "invalid JSON body", "code": "INVALID_REQUEST"})

    repo_url = _extract_repo_url(payload)
    target = _normalize(repo_url)
    if not target:
        return _response(
            400, {"error": "no repository URL in payload", "code": "INVALID_REQUEST"}
        )

    try:
        # ``_list_records`` returns ``(records, next_page_key)``; the webhook
        # matches across all projects, so it aggregates every page (next=None).
        records, _ = project_api._list_records()
    except Exception as exc:  # noqa: BLE001
        print(f"[GIT_WEBHOOK] list error: {type(exc).__name__}")
        return _response(500, {"error": "internal error", "code": "RECORD_READ_FAILED"})

    match = next(
        (r for r in records if _normalize(r.get("repo_url")) == target),
        None,
    )
    if match is None:
        # Not an error — a push for a repo no project has linked is simply ignored.
        return _response(200, {"matched": False})

    project_api._maybe_trigger_ingest(match["project_uuid"], match)
    return _response(202, {"matched": True, "project_uuid": match["project_uuid"]})
