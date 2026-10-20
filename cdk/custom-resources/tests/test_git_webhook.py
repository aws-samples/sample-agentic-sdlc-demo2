"""Unit tests for the Git push webhook Lambda (git_webhook.handler).

Verifies signature enforcement (GitHub HMAC + GitLab token), repo→project
matching, and that a verified push re-triggers ingestion for the matching
project only.
"""

import hashlib
import hmac
import json
import os
import sys

import pytest

_CUSTOM_RESOURCES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _CUSTOM_RESOURCES_DIR not in sys.path:
    sys.path.insert(0, _CUSTOM_RESOURCES_DIR)

import git_webhook  # noqa: E402
import project_api  # noqa: E402

SECRET = "webhook-shared-secret"  # nosec B105 test fixture


@pytest.fixture
def wired(monkeypatch):
    """Stub the secret + Project API helpers; record ingest triggers."""
    monkeypatch.setattr(git_webhook, "_webhook_secret", lambda: SECRET)
    records = [
        {"project_uuid": "p-1", "repo_url": "https://github.com/acme/widget"},
        {"project_uuid": "p-2", "repo_url": "https://gitlab.com/acme/other"},
    ]
    # ``_list_records`` returns ``(records, next_page_key)`` — the webhook
    # aggregates all projects, so the cursor is None.
    monkeypatch.setattr(project_api, "_list_records", lambda *a, **k: (records, None))
    triggered = []
    monkeypatch.setattr(
        project_api, "_maybe_trigger_ingest", lambda uuid, rec: triggered.append(uuid)
    )
    return triggered


def _github_event(body_obj, secret=SECRET):
    raw = json.dumps(body_obj)
    sig = "sha256=" + hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return {"headers": {"X-Hub-Signature-256": sig}, "body": raw}


def test_valid_github_push_triggers_ingest_for_matching_repo(wired):
    event = _github_event({"repository": {"clone_url": "https://github.com/acme/widget.git"}})
    resp = git_webhook.handler(event, None)
    assert resp["statusCode"] == 202
    assert json.loads(resp["body"]) == {"matched": True, "project_uuid": "p-1"}
    assert wired == ["p-1"]


def test_invalid_github_signature_is_401_and_no_trigger(wired):
    event = _github_event(
        {"repository": {"clone_url": "https://github.com/acme/widget"}}, secret="wrong"  # nosec B106 test fixture
    )
    resp = git_webhook.handler(event, None)
    assert resp["statusCode"] == 401
    assert wired == []


def test_gitlab_token_push_triggers_ingest(wired):
    body = json.dumps({"project": {"git_http_url": "https://gitlab.com/acme/other.git"}})
    event = {"headers": {"X-Gitlab-Token": SECRET}, "body": body}
    resp = git_webhook.handler(event, None)
    assert resp["statusCode"] == 202
    assert json.loads(resp["body"])["project_uuid"] == "p-2"
    assert wired == ["p-2"]


def test_unsigned_request_is_401(wired):
    resp = git_webhook.handler({"headers": {}, "body": "{}"}, None)
    assert resp["statusCode"] == 401
    assert wired == []


def test_push_for_unlinked_repo_is_ignored(wired):
    event = _github_event({"repository": {"clone_url": "https://github.com/nobody/nothing"}})
    resp = git_webhook.handler(event, None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {"matched": False}
    assert wired == []


def test_missing_repo_url_is_400(wired):
    event = _github_event({"ref": "refs/heads/main"})
    resp = git_webhook.handler(event, None)
    assert resp["statusCode"] == 400
