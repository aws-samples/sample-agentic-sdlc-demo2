"""Scripted six-persona handoff verification (Git round-trip / QA + DevOps).

Extends the two-persona (BA -> SA) scenario to the full six-persona lifecycle:

    BA -> SA -> Developer -> QA Engineer -> DevOps Engineer

replaying the whole handoff chain from ``draft`` to the terminal ``deployed``
state through the **real** ``project_api.handler`` against the in-memory fake
S3 (:mod:`fake_s3`) used by the per-route unit tests. Every call carries
Cognito-authorizer claims (or, for the ingest callback, none — that route is a
trusted internal caller), so owner-gating, registry-driven validation,
ETag-protected writes, and history append all run for real.

It exercises the Git round-trip end to end:

    Developer links the repo (POST /link-repo) -> submits
    (assigned_to_dev -> implementation_submitted) -> the ingest pipeline
    callback (POST /ingest-callback, status=ready) advances the project to
    assigned_to_qa -> QA advances through in_qa -> qa_passed -> hands to
    DevOps -> DevOps advances through in_deployment -> deployed.

and both new request-changes bounces (QA -> Dev, DevOps -> QA) plus an RBAC
spot check (a wrong-role forward is rejected 403).

The CodeBuild ingest itself is not exercised here (no live AWS): the callback
route is invoked directly, exactly as the CodeBuild ``aws lambda invoke`` step
does in production. To run against a deployed environment, replace the fake S3
with the real ``PROJECT_BUCKET`` and ``_event(...)`` with HTTP calls carrying
the QA/DevOps test users' Cognito ID tokens.

Validates: the six-persona chain, the Git round-trip link/submit/ingest, the
QA->Dev and DevOps->QA bounces, ownership flipping at every boundary, and
append-only history.
"""

import json
import os
import sys

import pytest

_CUSTOM_RESOURCES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _CUSTOM_RESOURCES_DIR not in sys.path:
    sys.path.insert(0, _CUSTOM_RESOURCES_DIR)
_TESTS_DIR = os.path.dirname(__file__)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

import project_api  # noqa: E402
from fake_dynamodb import FakeDynamoDBClient  # noqa: E402
from fake_s3 import FakeS3Client  # noqa: E402

BUCKET = "test-project-bucket"

BA_USER, BA_GROUP = "ba-tester@example.com", "requirements-analyst"
SA_USER, SA_GROUP = "sa-tester@example.com", "system-architect"
DEV_USER, DEV_GROUP = "dev-tester@example.com", "software-developer"
QA_USER, QA_GROUP = "qa-tester@example.com", "qa-engineer"
OPS_USER, OPS_GROUP = "devops-tester@example.com", "devops-engineer"

PROJECT_UUID = "six-persona-e2e-uuid-001"
PROJECT_NAME = "Event RSVP"
REPO_URL = "https://github.com/acme/event-rsvp"


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3Client()
    ddb = FakeDynamoDBClient()
    monkeypatch.setenv("PROJECT_BUCKET", BUCKET)
    monkeypatch.setenv("PROJECT_TABLE", "test-projects-table")
    # Ingestion CodeBuild is not configured in the scripted run — link/submit
    # never call out to AWS; the ingest callback is invoked directly below.
    monkeypatch.delenv("REPO_INGEST_CODEBUILD_PROJECT", raising=False)
    monkeypatch.setattr(project_api, "_s3", lambda: fake)
    monkeypatch.setattr(project_api, "_ddb", lambda: ddb)
    fake.ddb = ddb
    return fake


def _event(method, resource, *, project_id=None, body=None, groups=None, actor="system"):
    claims = {"email": actor}
    if groups is not None:
        claims["cognito:groups"] = list(groups)
    return {
        "httpMethod": method,
        "resource": resource,
        "pathParameters": {"projectId": project_id} if project_id else None,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {"authorizer": {"claims": claims}},
    }


def _body(resp):
    return json.loads(resp.get("body") or "{}")


def _create(s3, uuid, name, actor, groups):
    resp = project_api.handler(
        _event("POST", "/projects", body={"name": name, "project_uuid": uuid},
               groups=groups, actor=actor),
        None,
    )
    assert resp["statusCode"] == 201, _body(resp)
    return _body(resp)


def _transition(s3, uuid, *, action, groups, actor, note=None, suffix="transition"):
    body = {"action": action}
    if note is not None:
        body["note"] = note
    return project_api.handler(
        _event("POST", f"/projects/{{projectId}}/{suffix}", project_id=uuid,
               body=body, groups=groups, actor=actor),
        None,
    )


def _forward(s3, uuid, *, action, groups, actor, expect_state, expect_role):
    """Apply a forward transition and assert the resulting state + ownership."""
    resp = _transition(s3, uuid, action=action, groups=groups, actor=actor)
    assert resp["statusCode"] == 200, _body(resp)
    rec = _body(resp)
    assert rec["handoff_state"] == expect_state, (expect_state, rec["handoff_state"])
    assert rec["owning_role"] == expect_role, (expect_role, rec["owning_role"])
    return rec


def _link_repo(s3, uuid, *, groups, actor, repo_url=REPO_URL, branch=None):
    body = {"repo_url": repo_url}
    if branch:
        body["branch"] = branch
    return project_api.handler(
        _event("POST", "/projects/{projectId}/link-repo", project_id=uuid,
               body=body, groups=groups, actor=actor),
        None,
    )


def _ingest_callback(s3, uuid, *, status, commit=None):
    body = {"status": status}
    if commit is not None:
        body["commit"] = commit
    # No Cognito groups — the ingest callback is an IAM-authed internal caller.
    return project_api.handler(
        _event("POST", "/projects/{projectId}/ingest-callback", project_id=uuid, body=body),
        None,
    )


def test_six_persona_handoff_end_to_end(s3):
    """Replay BA -> SA -> Dev -> QA -> DevOps from draft to deployed."""

    # --- BA segment --------------------------------------------------------
    created = _create(s3, PROJECT_UUID, PROJECT_NAME, actor=BA_USER, groups=[BA_GROUP])
    assert created["handoff_state"] == "draft"
    assert created["owning_role"] == BA_GROUP
    # Repo fields default present on creation.
    assert created["repo_ingest_status"] == "not_linked"

    _forward(s3, PROJECT_UUID, action="assign", groups=[BA_GROUP], actor=BA_USER,
             expect_state="requirements_ready", expect_role=BA_GROUP)
    _forward(s3, PROJECT_UUID, action="assign", groups=[BA_GROUP], actor=BA_USER,
             expect_state="assigned_to_sa", expect_role=SA_GROUP)

    # --- SA segment --------------------------------------------------------
    _forward(s3, PROJECT_UUID, action="approve", groups=[SA_GROUP], actor=SA_USER,
             expect_state="in_design", expect_role=SA_GROUP)
    _forward(s3, PROJECT_UUID, action="design-complete", groups=[SA_GROUP], actor=SA_USER,
             expect_state="design_ready", expect_role=SA_GROUP)
    _forward(s3, PROJECT_UUID, action="design-complete", groups=[SA_GROUP], actor=SA_USER,
             expect_state="ready_for_dev", expect_role=SA_GROUP)
    _forward(s3, PROJECT_UUID, action="assign", groups=[SA_GROUP], actor=SA_USER,
             expect_state="assigned_to_dev", expect_role=DEV_GROUP)

    # --- Developer: submit is blocked until a repo is linked ---------------
    blocked = _transition(s3, PROJECT_UUID, action="submit", groups=[DEV_GROUP], actor=DEV_USER)
    assert blocked["statusCode"] == 422, _body(blocked)
    assert _body(blocked)["code"] == "REPO_NOT_LINKED"

    # Link the pushed repo, then submit.
    linked = _link_repo(s3, PROJECT_UUID, groups=[DEV_GROUP], actor=DEV_USER, branch="release")
    assert linked["statusCode"] == 200, _body(linked)
    assert _body(linked)["repo_url"] == REPO_URL
    assert _body(linked)["repo_ingest_status"] == "pending"

    _forward(s3, PROJECT_UUID, action="submit", groups=[DEV_GROUP], actor=DEV_USER,
             expect_state="implementation_submitted", expect_role=DEV_GROUP)

    # --- Ingestion pipeline callback advances to QA ------------------------
    ing = _ingest_callback(s3, PROJECT_UUID, status="ready", commit="cafe123")
    assert ing["statusCode"] == 200, _body(ing)
    assert _body(ing)["advanced"] is True
    rec = _body(ing)["record"]
    assert rec["handoff_state"] == "assigned_to_qa"
    assert rec["owning_role"] == QA_GROUP
    assert rec["repo_commit"] == "cafe123"
    assert rec["repo_ingest_status"] == "ready"

    # --- QA segment: demonstrate the QA -> Dev bounce, then proceed --------
    _forward(s3, PROJECT_UUID, action="advance", groups=[QA_GROUP], actor=QA_USER,
             expect_state="in_qa", expect_role=QA_GROUP)

    # QA requests changes -> bounces to the developer (assigned_to_dev).
    bounce = _transition(s3, PROJECT_UUID, action="request-changes", groups=[QA_GROUP],
                         actor=QA_USER, note="Fix the flaky check-in test")
    assert bounce["statusCode"] == 200, _body(bounce)
    assert _body(bounce)["handoff_state"] == "assigned_to_dev"
    assert _body(bounce)["owning_role"] == DEV_GROUP
    assert _body(bounce)["assignment_history"][-1]["note"] == "Fix the flaky check-in test"

    # Developer re-submits (repo already linked) and ingestion re-advances to QA.
    _forward(s3, PROJECT_UUID, action="submit", groups=[DEV_GROUP], actor=DEV_USER,
             expect_state="implementation_submitted", expect_role=DEV_GROUP)
    reing = _ingest_callback(s3, PROJECT_UUID, status="ready", commit="cafe456")
    assert _body(reing)["record"]["handoff_state"] == "assigned_to_qa"

    # RBAC spot check: a DevOps user cannot advance a QA-owned state.
    wrong = _transition(s3, PROJECT_UUID, action="advance", groups=[OPS_GROUP], actor=OPS_USER)
    assert wrong["statusCode"] == 403, _body(wrong)

    # QA advances through to the QA -> DevOps boundary.
    _forward(s3, PROJECT_UUID, action="advance", groups=[QA_GROUP], actor=QA_USER,
             expect_state="in_qa", expect_role=QA_GROUP)
    _forward(s3, PROJECT_UUID, action="advance", groups=[QA_GROUP], actor=QA_USER,
             expect_state="qa_passed", expect_role=QA_GROUP)
    _forward(s3, PROJECT_UUID, action="advance", groups=[QA_GROUP], actor=QA_USER,
             expect_state="assigned_to_devops", expect_role=OPS_GROUP)

    # --- DevOps segment: demonstrate the DevOps -> QA bounce, then deploy ---
    _forward(s3, PROJECT_UUID, action="advance", groups=[OPS_GROUP], actor=OPS_USER,
             expect_state="in_deployment", expect_role=OPS_GROUP)

    ops_bounce = _transition(s3, PROJECT_UUID, action="request-changes", groups=[OPS_GROUP],
                             actor=OPS_USER, note="Deployment needs a rollback plan")
    assert ops_bounce["statusCode"] == 200, _body(ops_bounce)
    assert _body(ops_bounce)["handoff_state"] == "assigned_to_qa"
    assert _body(ops_bounce)["owning_role"] == QA_GROUP

    # QA re-advances to DevOps, which completes the deployment.
    _forward(s3, PROJECT_UUID, action="advance", groups=[QA_GROUP], actor=QA_USER,
             expect_state="in_qa", expect_role=QA_GROUP)
    _forward(s3, PROJECT_UUID, action="advance", groups=[QA_GROUP], actor=QA_USER,
             expect_state="qa_passed", expect_role=QA_GROUP)
    _forward(s3, PROJECT_UUID, action="advance", groups=[QA_GROUP], actor=QA_USER,
             expect_state="assigned_to_devops", expect_role=OPS_GROUP)
    _forward(s3, PROJECT_UUID, action="advance", groups=[OPS_GROUP], actor=OPS_USER,
             expect_state="in_deployment", expect_role=OPS_GROUP)
    final = _forward(s3, PROJECT_UUID, action="advance", groups=[OPS_GROUP], actor=OPS_USER,
                     expect_state="deployed", expect_role=OPS_GROUP)

    # --- Terminal + cross-cutting invariants -------------------------------
    assert project_api.registry.forward("deployed") is None  # terminal
    # Identity + repo linkage stable across the whole run.
    assert final["project_uuid"] == PROJECT_UUID
    assert final["repo_url"] == REPO_URL
    # History is append-only and every entry's `to` matches the next `from`.
    history = final["assignment_history"]
    assert history[0]["from"] is None and history[0]["to"] == "draft"
    for prev, nxt in zip(history, history[1:]):
        assert nxt["from"] == prev["to"], (prev, nxt)
    assert history[-1]["to"] == "deployed"
