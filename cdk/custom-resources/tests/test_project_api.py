"""Unit tests for the Project API Lambda handler.

Exercises ``project_api.handler`` through its real public surface (API Gateway
proxy events in, ``{statusCode, body}`` out) against in-memory fakes: DynamoDB
(:mod:`fake_dynamodb`) for the project record store and S3 (:mod:`fake_s3`) for
artifacts / presigned uploads / artifact-tree cleanup. Both fakes raise the
genuine ``botocore.exceptions.ClientError`` the handler catches, so the 404 /
409 (conditional-write) paths are real, not stubbed. The DynamoDB fake is
attached as ``s3.ddb``.

These tests assert *observable* outcomes — HTTP status codes and the record
actually persisted to the record store — rather than re-deriving the handler's
own decisions (no calls to ``registry.validate_transition`` from the tests), so
they cannot mirror-test the implementation.

Covers:
  * Create / list / read happy paths; list is cross-creator (Req 3.2); the
    stage cursor + version are seeded on create (Req 6.2 / 2.4).
  * Illegal transition -> 422 with an unchanged stored record (Req 4.4).
  * Version conflict (concurrent writer) -> 409 (Req 2.4 / 4.5).
  * Indexed listing: broad selection (GSI2) + role queue (GSI1) + pagination
    (Req 3.1 / 3.4 / 3.5 / 12.2).
  * Exactly one assignment_history entry appended per transition (Req 4.6).
  * RBAC: non-owning role on transition/reassign/delete -> 403, record
    unchanged; read routes succeed for any group (Req 2.2, 2.3).
  * request-changes records the supplied note in history (Req 4.6 / 7).

Validates: Requirements 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.5, 4.4, 4.5, 4.6, 5.1,
5.2, 6.2, 12.3, 12.4
"""

import json
import os
import sys

import pytest

# Make the Lambda module importable regardless of pytest's rootdir/cwd:
# project_api.py / workflow_registry.py live one directory up from this
# tests/ package (mirrors icode-agent/tests/test_workflow_registry.py).
_CUSTOM_RESOURCES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _CUSTOM_RESOURCES_DIR not in sys.path:
    sys.path.insert(0, _CUSTOM_RESOURCES_DIR)
# fake_s3 sits beside this test file.
_TESTS_DIR = os.path.dirname(__file__)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

import project_api  # noqa: E402
from fake_dynamodb import FakeDynamoDBClient  # noqa: E402
from fake_s3 import FakeS3Client  # noqa: E402

BUCKET = "test-project-bucket"
TABLE = "test-projects-table"
BA_ROLE = "requirements-analyst"
SA_ROLE = "system-architect"


# ---------------------------------------------------------------------------
# Fixtures + event/request helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def s3(monkeypatch):
    """Install fresh in-memory fakes (S3 for artifacts, DynamoDB for records).

    Records now live in DynamoDB (system of record); artifacts,
    presigned uploads, and artifact-tree cleanup still use S3. The DynamoDB fake
    is attached as ``s3.ddb`` so a single fixture (and a single ``s3`` param)
    gives a test both stores — ``s3.ddb.get_record(uuid)`` inspects a persisted
    record the way ``s3.raw_body`` inspects a persisted artifact.
    """
    fake = FakeS3Client()
    ddb = FakeDynamoDBClient()
    monkeypatch.setenv("PROJECT_BUCKET", BUCKET)
    monkeypatch.setenv("PROJECT_TABLE", TABLE)
    monkeypatch.setattr(project_api, "_s3", lambda: fake)
    monkeypatch.setattr(project_api, "_ddb", lambda: ddb)
    fake.ddb = ddb
    return fake


def _event(method, resource, project_id=None, body=None, groups=None,
           actor="user@example.com"):
    """Build an API Gateway proxy event with Cognito authorizer claims."""
    claims = {"email": actor}
    if groups is not None:
        # cognito:groups may arrive as a list or a comma-separated string;
        # tests pass a list (the handler normalizes both).
        claims["cognito:groups"] = groups
    return {
        "httpMethod": method,
        "resource": resource,
        "pathParameters": {"projectId": project_id} if project_id else None,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {"authorizer": {"claims": claims}},
    }


def _json_body(response):
    """Parse the JSON body out of a handler response."""
    return json.loads(response["body"])


def _record_key(project_uuid):
    return f"_platform/projects/{project_uuid}.json"


def _create(s3, uuid, name, groups=None, actor="ba@example.com", **extra):
    """Drive a create through the handler and return the parsed record."""
    body = {"name": name, "project_uuid": uuid, **extra}
    resp = project_api.handler(
        _event("POST", "/projects", body=body, groups=groups, actor=actor), None
    )
    assert resp["statusCode"] == 201, resp
    return _json_body(resp)


def _transition(s3, uuid, *, action=None, target_state=None, note=None,
                groups, actor="user@example.com", resource_suffix="transition"):
    """Drive a transition/reassign through the handler; return the raw response."""
    body = {}
    if action is not None:
        body["action"] = action
    if target_state is not None:
        body["target_state"] = target_state
    if note is not None:
        body["note"] = note
    return project_api.handler(
        _event(
            "POST",
            f"/projects/{{projectId}}/{resource_suffix}",
            project_id=uuid,
            body=body,
            groups=groups,
            actor=actor,
        ),
        None,
    )


# ===========================================================================
# 1. Create happy path (Req 4.1 baseline, supports 3.x)
# ===========================================================================


def test_create_persists_draft_record_with_initial_state(s3):
    """POST /projects -> 201 and a fully initialized, persisted draft record."""
    resp = project_api.handler(
        _event(
            "POST",
            "/projects",
            body={"name": "My Cool Project", "project_uuid": "proj-1"},
            groups=[BA_ROLE],
            actor="ba@example.com",
        ),
        None,
    )

    assert resp["statusCode"] == 201
    record = _json_body(resp)

    # Initial handoff state machine seeding (registry initial_state == draft).
    assert record["handoff_state"] == "draft"
    assert record["owning_role"] == BA_ROLE
    assert record["created_by"] == "ba@example.com"
    # artifact_prefix is the slug of the name, and equals project_id.
    assert record["artifact_prefix"] == "my_cool_project"
    assert record["project_id"] == "my_cool_project"
    assert record["project_uuid"] == "proj-1"

    # Exactly one seed history entry: None -> draft, note "created".
    history = record["assignment_history"]
    assert len(history) == 1
    assert history[0]["from"] is None
    assert history[0]["to"] == "draft"
    assert history[0]["actor"] == "ba@example.com"
    assert history[0]["note"] == "created"
    assert history[0]["timestamp"]  # non-empty timestamp present

    # The stage cursor is seeded to the first stage of the initial segment
    # (Req 6.2) and an optimistic-concurrency version is stamped.
    assert record["current_stage"] == "workspace_detection"
    assert record["version"] == 1

    # The record is actually persisted in the store (not just returned).
    stored = s3.ddb.get_record("proj-1")
    assert stored is not None
    assert stored["handoff_state"] == "draft"
    assert stored["current_stage"] == "workspace_detection"


def test_create_requires_name(s3):
    """A create without a name is a 400, and nothing is persisted."""
    resp = project_api.handler(
        _event("POST", "/projects", body={"project_uuid": "no-name"}, groups=[BA_ROLE]),
        None,
    )
    assert resp["statusCode"] == 400
    assert s3.ddb.get_record("no-name") is None


# ===========================================================================
# 2. List returns cross-creator projects (Req 3.2)
# ===========================================================================


def test_list_returns_projects_from_different_creators(s3):
    """GET /projects returns every record regardless of created_by (3.2)."""
    _create(s3, "p-alice", "Alice Project", actor="alice@example.com")
    _create(s3, "p-bob", "Bob Project", actor="bob@example.com")

    # A THIRD identity (carol, SA role) lists — she created neither project.
    resp = project_api.handler(
        _event("GET", "/projects", groups=[SA_ROLE], actor="carol@example.com"), None
    )
    assert resp["statusCode"] == 200
    projects = _json_body(resp)["projects"]

    by_uuid = {p["project_uuid"]: p for p in projects}
    assert {"p-alice", "p-bob"} <= set(by_uuid)
    # Cross-creator visibility: the two records were made by different users.
    assert by_uuid["p-alice"]["created_by"] == "alice@example.com"
    assert by_uuid["p-bob"]["created_by"] == "bob@example.com"


def test_read_by_id_returns_same_record_and_prefix(s3):
    """GET /projects/{id} returns the same record/artifact_prefix as created."""
    created = _create(s3, "p-read", "Read Me", actor="alice@example.com")

    resp = project_api.handler(
        _event("GET", "/projects/{projectId}", project_id="p-read",
               groups=[SA_ROLE], actor="someone-else@example.com"),
        None,
    )
    assert resp["statusCode"] == 200
    record = _json_body(resp)
    assert record["project_uuid"] == "p-read"
    assert record["artifact_prefix"] == created["artifact_prefix"]
    assert record["handoff_state"] == "draft"


# ===========================================================================
# 3. Forward transition by owner: state advances, exactly one history entry
#    (Req 4.5, 4.6)
# ===========================================================================


def test_forward_transition_by_owner_advances_state_and_appends_one_history(s3):
    """A forward transition by the owning role advances state + appends 1 entry."""
    _create(s3, "p-fwd", "Forward Project", actor="ba@example.com")

    resp = _transition(s3, "p-fwd", action="assign", groups=[BA_ROLE],
                       actor="ba@example.com")

    assert resp["statusCode"] == 200
    record = _json_body(resp)
    # draft's forward successor is requirements_ready; still BA-owned.
    assert record["handoff_state"] == "requirements_ready"
    assert record["owning_role"] == BA_ROLE

    # Exactly one NEW history entry beyond the seed (seed=1 -> now 2).
    history = record["assignment_history"]
    assert len(history) == 2
    latest = history[-1]
    assert latest["from"] == "draft"
    assert latest["to"] == "requirements_ready"
    assert latest["actor"] == "ba@example.com"
    assert latest["timestamp"]

    # Persisted state matches the response.
    stored = s3.ddb.get_record("p-fwd")
    assert stored["handoff_state"] == "requirements_ready"
    assert len(stored["assignment_history"]) == 2
    # Each state-changing write bumps the optimistic-concurrency version.
    assert stored["version"] == 2


def test_transition_across_ownership_updates_owning_role(s3):
    """Advancing into the SA segment flips owning_role to system-architect."""
    _create(s3, "p-cross", "Cross", actor="ba@example.com")
    # draft -> requirements_ready (BA owns draft).
    _transition(s3, "p-cross", action="assign", groups=[BA_ROLE], actor="ba@example.com")
    # requirements_ready -> assigned_to_sa (BA still owns requirements_ready).
    resp = _transition(s3, "p-cross", action="assign", groups=[BA_ROLE],
                       actor="ba@example.com")

    assert resp["statusCode"] == 200
    record = _json_body(resp)
    assert record["handoff_state"] == "assigned_to_sa"
    assert record["owning_role"] == SA_ROLE  # ownership moved to the SA
    # Three entries: seed + two transitions, one appended per transition.
    assert len(record["assignment_history"]) == 3


# ===========================================================================
# 4. Illegal transition -> 422, record unchanged (Req 4.4)
# ===========================================================================


def test_illegal_transition_returns_422_and_leaves_record_unchanged(s3):
    """An illegal target by the owner -> 422 and byte-for-byte unchanged record."""
    _create(s3, "p-illegal", "Illegal", actor="ba@example.com")
    before = s3.ddb.get_record("p-illegal")

    # draft -> ready_for_dev is neither the forward successor nor a
    # request-changes edge. Caller IS the owner (BA) so RBAC passes and the
    # validation layer is what rejects it.
    resp = _transition(s3, "p-illegal", target_state="ready_for_dev",
                       groups=[BA_ROLE], actor="ba@example.com")

    assert resp["statusCode"] == 422
    assert _json_body(resp)["code"] == "ILLEGAL_TRANSITION"

    # The stored record is unchanged — same record, still draft, still 1 entry,
    # version not bumped (no write happened).
    after = s3.ddb.get_record("p-illegal")
    assert after == before
    assert after["handoff_state"] == "draft"
    assert after["version"] == 1
    assert len(after["assignment_history"]) == 1


# ===========================================================================
# 5. ETag mismatch -> 409 (Req 4.5)
# ===========================================================================


def test_concurrent_write_causes_version_conflict_409(s3):
    """A concurrent writer between read and write yields HTTP 409 (lost-update guard).

    Optimistic concurrency: the handler reads ``version`` then writes with a
    ``version = :expected`` condition. If another writer bumps ``version`` in
    the read→write window, the conditional PutItem fails and the handler returns
    409 (Req 2.4 / 4.5) without clobbering the concurrent writer's value.
    """
    _create(s3, "p-ver", "Version", actor="ba@example.com")

    # Simulate a concurrent writer: the instant the handler reads the record,
    # bump the stored version (and change a field) so the handler's captured
    # expected-version no longer matches. The conditional PutItem must fail.
    def _concurrent_writer(client):
        client.bump_version(
            "p-ver",
            mutate=lambda rec: rec.update(name="Changed by a concurrent writer"),
        )

    s3.ddb.on_next_get(_concurrent_writer)

    resp = _transition(s3, "p-ver", action="assign", groups=[BA_ROLE],
                       actor="ba@example.com")

    assert resp["statusCode"] == 409
    assert _json_body(resp)["code"] == "ETAG_MISMATCH"

    # The handler's transition did NOT overwrite the concurrent writer's value.
    stored = s3.ddb.get_record("p-ver")
    assert stored["name"] == "Changed by a concurrent writer"
    assert stored["handoff_state"] == "draft"


# ===========================================================================
# 6. RBAC on state-changing routes (Req 2.2, 2.3)
# ===========================================================================


def test_transition_by_non_owning_role_is_403_and_record_unchanged(s3):
    """A non-owning role transitioning -> 403 before any mutation (2.2/2.3)."""
    _create(s3, "p-rbac-t", "Rbac Transition", actor="ba@example.com")
    before = s3.ddb.get_record("p-rbac-t")

    # Project is in draft (owned by BA); an SA attempts the forward transition.
    resp = _transition(s3, "p-rbac-t", action="assign", groups=[SA_ROLE],
                       actor="sa@example.com")

    assert resp["statusCode"] == 403
    assert _json_body(resp)["code"] == "FORBIDDEN"
    # Record is untouched.
    assert s3.ddb.get_record("p-rbac-t") == before


def test_reassign_by_non_owning_role_is_403_and_record_unchanged(s3):
    """A non-owning role on /reassign -> 403, record unchanged (2.2/2.3)."""
    _create(s3, "p-rbac-r", "Rbac Reassign", actor="ba@example.com")
    before = s3.ddb.get_record("p-rbac-r")

    resp = _transition(s3, "p-rbac-r", action="assign", groups=[SA_ROLE],
                       actor="sa@example.com", resource_suffix="reassign")

    assert resp["statusCode"] == 403
    assert s3.ddb.get_record("p-rbac-r") == before


def test_delete_by_non_owning_role_is_403_and_record_preserved(s3):
    """A non-owning role deleting -> 403 and the record still exists (2.2/2.3)."""
    _create(s3, "p-rbac-d", "Rbac Delete", actor="ba@example.com")

    resp = project_api.handler(
        _event("DELETE", "/projects/{projectId}", project_id="p-rbac-d",
               groups=[SA_ROLE], actor="sa@example.com"),
        None,
    )

    assert resp["statusCode"] == 403
    assert _json_body(resp)["code"] == "FORBIDDEN"
    # Not deleted (neither the DynamoDB record nor any S3 object).
    assert s3.ddb.get_record("p-rbac-d") is not None
    assert s3.ddb.delete_calls == 0


def test_delete_by_owning_role_succeeds(s3):
    """The owning role CAN delete — confirms 403 above is about role, not route."""
    _create(s3, "p-del-ok", "Delete OK", actor="ba@example.com")

    resp = project_api.handler(
        _event("DELETE", "/projects/{projectId}", project_id="p-del-ok",
               groups=[BA_ROLE], actor="ba@example.com"),
        None,
    )
    assert resp["statusCode"] == 200
    assert _json_body(resp)["deleted"] is True
    assert s3.ddb.get_record("p-del-ok") is None


def test_delete_removes_artifact_prefix_and_respects_boundary(s3):
    """Deleting a project also removes its artifact tree so a future same-named
    project starts clean (prevents cross-project state bleed), while a sibling
    prefix that merely shares a leading substring is preserved by the
    trailing-slash boundary."""
    rec = _create(s3, "p-del-art", "Cleanup Me", actor="ba@example.com")
    prefix = rec["artifact_prefix"]  # slug of "Cleanup Me" -> "cleanup_me"

    # Artifacts under the project's own prefix (should be removed)...
    s3.seed(BUCKET, f"{prefix}/aidlc-docs/requirements/requirements.md", b"reqs")
    s3.seed(BUCKET, f"{prefix}/aidlc-docs/audit.md", b"audit")
    # ...and a sibling prefix sharing a leading substring (must survive).
    s3.seed(BUCKET, f"{prefix}_other/aidlc-docs/keep.md", b"keep")

    resp = project_api.handler(
        _event("DELETE", "/projects/{projectId}", project_id="p-del-art",
               groups=[BA_ROLE], actor="ba@example.com"),
        None,
    )

    assert resp["statusCode"] == 200
    body = _json_body(resp)
    assert body["deleted"] is True
    assert body["artifacts_removed"] == 2
    # Record (DynamoDB) and the project's artifact tree (S3) are gone.
    assert s3.ddb.get_record("p-del-art") is None
    assert s3.raw_body(BUCKET, f"{prefix}/aidlc-docs/requirements/requirements.md") is None
    assert s3.raw_body(BUCKET, f"{prefix}/aidlc-docs/audit.md") is None
    # The sibling prefix is untouched (boundary guard).
    assert s3.raw_body(BUCKET, f"{prefix}_other/aidlc-docs/keep.md") == b"keep"


def test_read_routes_open_regardless_of_group(s3):
    """GET list and GET one succeed for any (or no) group (read routes not gated)."""
    _create(s3, "p-open", "Open Read", actor="ba@example.com")

    # List with an unrelated group.
    list_resp = project_api.handler(
        _event("GET", "/projects", groups=["some-other-group"],
               actor="stranger@example.com"),
        None,
    )
    assert list_resp["statusCode"] == 200
    assert any(p["project_uuid"] == "p-open"
               for p in _json_body(list_resp)["projects"])

    # Read one with NO groups claim at all.
    get_resp = project_api.handler(
        _event("GET", "/projects/{projectId}", project_id="p-open",
               groups=None, actor="nobody@example.com"),
        None,
    )
    assert get_resp["statusCode"] == 200
    assert _json_body(get_resp)["project_uuid"] == "p-open"


# ===========================================================================
# 7. request-changes records the note in history (Req 4.6)
# ===========================================================================


def test_request_changes_records_note_in_history(s3):
    """request-changes bounces assigned_to_sa -> requirements_ready, noting the reason."""
    _create(s3, "p-rc", "Request Changes", actor="ba@example.com")
    # Drive forward into assigned_to_sa (both forwards are BA-owned states).
    _transition(s3, "p-rc", action="assign", groups=[BA_ROLE], actor="ba@example.com")
    _transition(s3, "p-rc", action="assign", groups=[BA_ROLE], actor="ba@example.com")

    # SA owns assigned_to_sa; request-changes with an explicit note.
    resp = _transition(
        s3, "p-rc", action="request-changes",
        note="Please clarify the non-functional requirements",
        groups=[SA_ROLE], actor="sa@example.com",
    )

    assert resp["statusCode"] == 200
    record = _json_body(resp)
    # Registry-defined backward reassignment: assigned_to_sa -> requirements_ready.
    assert record["handoff_state"] == "requirements_ready"
    assert record["owning_role"] == BA_ROLE

    latest = record["assignment_history"][-1]
    assert latest["from"] == "assigned_to_sa"
    assert latest["to"] == "requirements_ready"
    assert latest["actor"] == "sa@example.com"
    assert latest["note"] == "Please clarify the non-functional requirements"


# ===========================================================================
# 8. Git round-trip (C7): POST /projects/{id}/link-repo + submit gating
# ===========================================================================

DEV_ROLE = "software-developer"


def _link_repo(s3, uuid, *, repo_url, branch=None, groups, actor="dev@example.com"):
    """Drive a link-repo call through the handler; return the raw response."""
    body = {"repo_url": repo_url}
    if branch is not None:
        body["branch"] = branch
    return project_api.handler(
        _event("POST", "/projects/{projectId}/link-repo", project_id=uuid,
               body=body, groups=groups, actor=actor),
        None,
    )


def _drive_to_assigned_to_dev(s3, uuid):
    """Forward the project from draft all the way to assigned_to_dev.

    Uses the owning role at each step so every forward transition passes RBAC:
    draft/requirements_ready are BA-owned; assigned_to_sa..ready_for_dev are
    SA-owned; the forward out of ready_for_dev lands on assigned_to_dev.
    """
    _transition(s3, uuid, action="assign", groups=[BA_ROLE], actor="ba@example.com")            # draft -> requirements_ready
    _transition(s3, uuid, action="assign", groups=[BA_ROLE], actor="ba@example.com")            # requirements_ready -> assigned_to_sa
    _transition(s3, uuid, action="approve", groups=[SA_ROLE], actor="sa@example.com")           # assigned_to_sa -> in_design
    _transition(s3, uuid, action="design-complete", groups=[SA_ROLE], actor="sa@example.com")   # in_design -> design_ready
    _transition(s3, uuid, action="design-complete", groups=[SA_ROLE], actor="sa@example.com")   # design_ready -> ready_for_dev
    resp = _transition(s3, uuid, action="assign", groups=[SA_ROLE], actor="sa@example.com")     # ready_for_dev -> assigned_to_dev
    assert _json_body(resp)["handoff_state"] == "assigned_to_dev", _json_body(resp)


def test_create_record_has_repo_defaults(s3):
    """A freshly created record carries the C7 repo fields at their defaults."""
    record = _create(s3, "p-repo-defaults", "Repo Defaults", actor="ba@example.com")
    assert record["repo_url"] is None
    assert record["repo_provider"] is None
    assert record["repo_branch"] is None
    assert record["repo_commit"] is None
    assert record["repo_ingest_status"] == "not_linked"
    assert record["repo_last_synced_at"] is None
    assert record["repo_ingest_error"] is None


def test_link_repo_by_owning_dev_stores_metadata(s3):
    """The owning developer links a GitHub repo -> 200 and metadata persisted."""
    _create(s3, "p-link", "Link Repo", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-link")

    resp = _link_repo(s3, "p-link", repo_url="https://github.com/acme/widget.git",
                      branch="release", groups=[DEV_ROLE])

    assert resp["statusCode"] == 200, resp
    record = _json_body(resp)
    assert record["repo_url"] == "https://github.com/acme/widget.git"
    assert record["repo_provider"] == "github"
    assert record["repo_branch"] == "release"
    assert record["repo_ingest_status"] == "pending"
    # Handoff state is unchanged by linking (the developer submits separately).
    assert record["handoff_state"] == "assigned_to_dev"

    # Persisted, not just returned.
    stored = s3.ddb.get_record("p-link")
    assert stored["repo_url"] == "https://github.com/acme/widget.git"
    assert stored["repo_ingest_status"] == "pending"


def test_link_repo_defaults_branch_to_main(s3):
    """Omitting the branch defaults it to main."""
    _create(s3, "p-branch", "Branch Default", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-branch")

    resp = _link_repo(s3, "p-branch", repo_url="https://gitlab.com/acme/widget",
                      groups=[DEV_ROLE])
    assert resp["statusCode"] == 200
    record = _json_body(resp)
    assert record["repo_provider"] == "gitlab"
    assert record["repo_branch"] == "main"


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://github.com/acme/widget",          # not https
        "https://evil.com/acme/widget",           # host not allowlisted
        "https://github.com/acme",                # missing repo (no owner/repo)
        "https://github.com/../../etc/passwd",    # path traversal
        "https://169.254.169.254/acme/widget",    # SSRF to metadata endpoint
        "ftp://github.com/acme/widget",           # wrong scheme
        "https://github.com.evil.com/acme/widget",  # look-alike host
        "not-a-url",
        "",
    ],
)
def test_link_repo_rejects_disallowed_urls(s3, bad_url):
    """A repo URL outside the https + allowlisted-host + owner/repo shape -> 400,
    record unchanged (SSRF guard)."""
    _create(s3, "p-badurl", "Bad URL", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-badurl")
    before = s3.ddb.get_record("p-badurl")

    resp = _link_repo(s3, "p-badurl", repo_url=bad_url, groups=[DEV_ROLE])

    assert resp["statusCode"] == 400, (bad_url, resp)
    assert _json_body(resp)["code"] == "INVALID_REPO_URL"
    # Record is untouched by a rejected link.
    assert s3.ddb.get_record("p-badurl") == before


def test_link_repo_by_non_owning_role_is_403_and_record_unchanged(s3):
    """A non-owning role linking a repo -> 403 before any mutation (2.2/2.3)."""
    _create(s3, "p-link-rbac", "Link RBAC", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-link-rbac")
    before = s3.ddb.get_record("p-link-rbac")

    # Project is assigned_to_dev (owned by the developer); an SA attempts to link.
    resp = _link_repo(s3, "p-link-rbac", repo_url="https://github.com/acme/widget",
                      groups=[SA_ROLE], actor="sa@example.com")

    assert resp["statusCode"] == 403
    assert _json_body(resp)["code"] == "FORBIDDEN"
    assert s3.ddb.get_record("p-link-rbac") == before


def test_link_repo_missing_project_is_404(s3):
    """Linking a repo to a non-existent project -> 404."""
    resp = _link_repo(s3, "p-nope", repo_url="https://github.com/acme/widget",
                      groups=[DEV_ROLE])
    assert resp["statusCode"] == 404
    assert _json_body(resp)["code"] == "NOT_FOUND"


def test_submit_transition_blocked_until_repo_linked(s3):
    """assigned_to_dev -> implementation_submitted is rejected (422) with no repo,
    then permitted once a repo is linked."""
    _create(s3, "p-submit", "Submit Gate", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-submit")

    # No repo yet: the forward submit is a legal registry transition but the C7
    # precondition rejects it and leaves the record unchanged.
    before = s3.ddb.get_record("p-submit")
    blocked = _transition(s3, "p-submit", action="submit", groups=[DEV_ROLE],
                          actor="dev@example.com")
    assert blocked["statusCode"] == 422
    assert _json_body(blocked)["code"] == "REPO_NOT_LINKED"
    assert s3.ddb.get_record("p-submit") == before

    # Link a repo, then the submit forward succeeds.
    _link_repo(s3, "p-submit", repo_url="https://github.com/acme/widget",
               groups=[DEV_ROLE])
    ok = _transition(s3, "p-submit", action="submit", groups=[DEV_ROLE],
                     actor="dev@example.com")
    assert ok["statusCode"] == 200, _json_body(ok)
    record = _json_body(ok)
    assert record["handoff_state"] == "implementation_submitted"
    assert record["owning_role"] == DEV_ROLE


# ===========================================================================
# 9. Git round-trip ingestion (C7-D): StartBuild trigger + ingest-callback
# ===========================================================================


class _FakeCodeBuild:
    """Records start_build calls so the trigger can be asserted."""

    def __init__(self):
        self.calls = []

    def start_build(self, **kwargs):
        self.calls.append(kwargs)
        return {"build": {"id": "build-1"}}


def _ingest_callback(s3, uuid, *, status, commit=None, reason=None):
    body = {"status": status}
    if commit is not None:
        body["commit"] = commit
    if reason is not None:
        body["reason"] = reason
    return project_api.handler(
        _event("POST", "/projects/{projectId}/ingest-callback",
               project_id=uuid, body=body),
        None,
    )


def _drive_to_implementation_submitted(s3, uuid):
    """Reach implementation_submitted: to assigned_to_dev, link a repo, submit."""
    _drive_to_assigned_to_dev(s3, uuid)
    _link_repo(s3, uuid, repo_url="https://github.com/acme/widget", groups=[DEV_ROLE])
    resp = _transition(s3, uuid, action="submit", groups=[DEV_ROLE], actor="dev@example.com")
    assert _json_body(resp)["handoff_state"] == "implementation_submitted", _json_body(resp)


def test_submit_triggers_codebuild_when_configured(s3, monkeypatch):
    """Submitting the implementation (entering implementation_submitted) starts
    the ingest build with the project identity passed as environment overrides.
    Linking alone does not start a build."""
    fake_cb = _FakeCodeBuild()
    monkeypatch.setenv("REPO_INGEST_CODEBUILD_PROJECT", "aidlc-repo-ingest")
    monkeypatch.setattr(project_api, "_codebuild", lambda: fake_cb)

    _create(s3, "p-cb", "CodeBuild Trigger", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-cb")
    _link_repo(s3, "p-cb", repo_url="https://github.com/acme/widget",
               branch="release", groups=[DEV_ROLE])
    # Linking alone does not start a build.
    assert fake_cb.calls == []

    # Submitting (assigned_to_dev -> implementation_submitted) does.
    resp = _transition(s3, "p-cb", action="submit", groups=[DEV_ROLE], actor="dev@example.com")
    assert resp["statusCode"] == 200
    assert _json_body(resp)["handoff_state"] == "implementation_submitted"

    assert len(fake_cb.calls) == 1
    call = fake_cb.calls[0]
    assert call["projectName"] == "aidlc-repo-ingest"
    overrides = {e["name"]: e["value"] for e in call["environmentVariablesOverride"]}
    assert overrides["PROJECT_UUID"] == "p-cb"
    assert overrides["REPO_URL"] == "https://github.com/acme/widget"
    assert overrides["REPO_BRANCH"] == "release"


def test_submit_does_not_trigger_codebuild_when_unconfigured(s3, monkeypatch):
    """With no ingest project configured, submitting still succeeds and no build
    is started (graceful degradation)."""
    fake_cb = _FakeCodeBuild()
    monkeypatch.delenv("REPO_INGEST_CODEBUILD_PROJECT", raising=False)
    monkeypatch.setattr(project_api, "_codebuild", lambda: fake_cb)

    _create(s3, "p-nocb", "No CodeBuild", actor="ba@example.com")
    _drive_to_implementation_submitted(s3, "p-nocb")

    assert fake_cb.calls == []
    stored = s3.ddb.get_record("p-nocb")
    assert stored["handoff_state"] == "implementation_submitted"


def test_ingest_callback_ready_advances_to_qa(s3):
    """A successful ingest callback advances implementation_submitted ->
    assigned_to_qa (owning role qa-engineer) with a system-actor history entry."""
    _create(s3, "p-ing", "Ingest OK", actor="ba@example.com")
    _drive_to_implementation_submitted(s3, "p-ing")

    resp = _ingest_callback(s3, "p-ing", status="ready", commit="abc123")

    assert resp["statusCode"] == 200, resp
    body = _json_body(resp)
    assert body["advanced"] is True
    record = body["record"]
    assert record["handoff_state"] == "assigned_to_qa"
    assert record["owning_role"] == "qa-engineer"
    assert record["repo_ingest_status"] == "ready"
    assert record["repo_commit"] == "abc123"
    assert record["repo_ingest_error"] is None
    latest = record["assignment_history"][-1]
    assert latest["from"] == "implementation_submitted"
    assert latest["to"] == "assigned_to_qa"
    assert latest["actor"] == "system:repo-ingest"

    # Persisted.
    stored = s3.ddb.get_record("p-ing")
    assert stored["handoff_state"] == "assigned_to_qa"


def test_ingest_callback_failed_sets_status_without_advancing(s3):
    """A failed ingest callback marks repo_ingest_status=failed and does NOT
    advance the handoff state."""
    _create(s3, "p-ingfail", "Ingest Fail", actor="ba@example.com")
    _drive_to_implementation_submitted(s3, "p-ingfail")

    resp = _ingest_callback(s3, "p-ingfail", status="failed")

    assert resp["statusCode"] == 200
    body = _json_body(resp)
    assert body["advanced"] is False
    record = body["record"]
    assert record["repo_ingest_status"] == "failed"
    assert record["handoff_state"] == "implementation_submitted"  # unchanged


def test_ingest_callback_failed_records_reason(s3):
    """A failed ingest callback stores the human-readable reason (e.g. the size
    guardrail message) in repo_ingest_error and does not advance."""
    _create(s3, "p-ingreason", "Ingest Reason", actor="ba@example.com")
    _drive_to_implementation_submitted(s3, "p-ingreason")

    resp = _ingest_callback(
        s3, "p-ingreason", status="failed",
        reason="Repository exceeds the ingest size limit (120000 files, 4200000000 bytes)",
    )

    assert resp["statusCode"] == 200
    record = _json_body(resp)["record"]
    assert record["repo_ingest_status"] == "failed"
    assert record["repo_ingest_error"] == (
        "Repository exceeds the ingest size limit (120000 files, 4200000000 bytes)"
    )
    assert record["handoff_state"] == "implementation_submitted"  # unchanged


def test_ingest_callback_is_idempotent_when_not_in_submit_state(s3):
    """A duplicate/late ready callback for a project not in implementation_submitted
    updates status only and does not re-transition."""
    _create(s3, "p-ingdup", "Ingest Dup", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-ingdup")  # state is assigned_to_dev, not submitted
    _link_repo(s3, "p-ingdup", repo_url="https://github.com/acme/widget", groups=[DEV_ROLE])

    resp = _ingest_callback(s3, "p-ingdup", status="ready", commit="def456")

    assert resp["statusCode"] == 200
    body = _json_body(resp)
    assert body["advanced"] is False
    assert body["record"]["handoff_state"] == "assigned_to_dev"  # unchanged
    assert body["record"]["repo_ingest_status"] == "ready"
    assert body["record"]["repo_commit"] == "def456"


def test_ingest_callback_invalid_status_is_400(s3):
    _create(s3, "p-ingbad", "Ingest Bad", actor="ba@example.com")
    _drive_to_implementation_submitted(s3, "p-ingbad")
    resp = _ingest_callback(s3, "p-ingbad", status="in-progress")
    assert resp["statusCode"] == 400
    assert _json_body(resp)["code"] == "INVALID_REQUEST"


def test_ingest_callback_missing_project_is_404(s3):
    resp = _ingest_callback(s3, "p-ghost", status="ready")
    assert resp["statusCode"] == 404
    assert _json_body(resp)["code"] == "NOT_FOUND"


# ===========================================================================
# 10. Private-repo git token provisioning (Secrets Manager)
# ===========================================================================

from botocore.exceptions import ClientError as _BotoClientError  # noqa: E402


class _FakeSecrets:
    """Records Secrets Manager put/create; simulates absent-then-created."""

    def __init__(self, existing=False):
        self.exists = existing
        self.put_calls = []
        self.create_calls = []

    def put_secret_value(self, SecretId, SecretString):  # noqa: N803
        if not self.exists:
            raise _BotoClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "nf"}},
                "PutSecretValue",
            )
        self.put_calls.append((SecretId, SecretString))
        return {}

    def create_secret(self, Name, SecretString):  # noqa: N803
        self.create_calls.append((Name, SecretString))
        self.exists = True
        return {}


def _link_with_token(s3, uuid, token, *, repo_url="https://github.com/acme/private",
                     groups=None):
    return project_api.handler(
        _event("POST", "/projects/{projectId}/link-repo", project_id=uuid,
               body={"repo_url": repo_url, "git_token": token},
               groups=groups if groups is not None else [DEV_ROLE], actor="dev@example.com"),
        None,
    )


def test_link_repo_creates_git_secret_when_absent(s3, monkeypatch):
    """A supplied git_token is created in Secrets Manager on first use, and is
    never echoed back in the response."""
    fake_secrets = _FakeSecrets(existing=False)
    monkeypatch.setattr(project_api, "_secrets", lambda: fake_secrets)

    _create(s3, "p-tok-new", "Token New", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-tok-new")
    resp = _link_with_token(s3, "p-tok-new", "ghp_secrettoken123")

    assert resp["statusCode"] == 200, _json_body(resp)
    # put raised ResourceNotFoundException -> create_secret used.
    assert fake_secrets.put_calls == []
    assert len(fake_secrets.create_calls) == 1
    name, secret = fake_secrets.create_calls[0]
    assert name == "aidlc/projects/p-tok-new/git"
    assert secret == "ghp_secrettoken123"  # nosec B105 test fixture

    # Token is never on the record / in the response.
    body = _json_body(resp)
    assert "git_token" not in body
    assert "ghp_secrettoken123" not in json.dumps(body)


def test_link_repo_updates_existing_git_secret(s3, monkeypatch):
    """When the secret already exists, put_secret_value updates it."""
    fake_secrets = _FakeSecrets(existing=True)
    monkeypatch.setattr(project_api, "_secrets", lambda: fake_secrets)

    _create(s3, "p-tok-upd", "Token Upd", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-tok-upd")
    resp = _link_with_token(s3, "p-tok-upd", "glpat_rotated")

    assert resp["statusCode"] == 200
    assert len(fake_secrets.put_calls) == 1
    assert fake_secrets.put_calls[0] == ("aidlc/projects/p-tok-upd/git", "glpat_rotated")
    assert fake_secrets.create_calls == []


def test_link_repo_without_token_does_not_touch_secrets(s3, monkeypatch):
    """A public-repo link (no git_token) never calls Secrets Manager."""
    fake_secrets = _FakeSecrets(existing=True)
    monkeypatch.setattr(project_api, "_secrets", lambda: fake_secrets)

    _create(s3, "p-tok-none", "Token None", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-tok-none")
    resp = _link_repo(s3, "p-tok-none", repo_url="https://github.com/acme/public", groups=[DEV_ROLE])

    assert resp["statusCode"] == 200
    assert fake_secrets.put_calls == []
    assert fake_secrets.create_calls == []


def test_link_repo_secret_write_failure_is_500_and_record_unchanged(s3, monkeypatch):
    """If storing the token fails (non-NotFound error), the link returns 500 and
    the record is not mutated."""
    class _BrokenSecrets:
        def put_secret_value(self, **_):
            raise _BotoClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "no"}},
                "PutSecretValue",
            )

    monkeypatch.setattr(project_api, "_secrets", lambda: _BrokenSecrets())
    _create(s3, "p-tok-fail", "Token Fail", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-tok-fail")
    before = s3.ddb.get_record("p-tok-fail")

    resp = _link_with_token(s3, "p-tok-fail", "tok")

    assert resp["statusCode"] == 500
    assert _json_body(resp)["code"] == "SECRET_WRITE_FAILED"
    # Record not mutated (repo not linked) because the secret write happens
    # before the record write.
    assert s3.ddb.get_record("p-tok-fail") == before


# ===========================================================================
# 11. QA test-runner: run-tests (QA-owner-gated) + test-callback
# ===========================================================================

QA_ROLE = "qa-engineer"


def _drive_to_assigned_to_qa(s3, uuid):
    """Reach assigned_to_qa: submit implementation, then a ready ingest callback."""
    _drive_to_implementation_submitted(s3, uuid)
    resp = _ingest_callback(s3, uuid, status="ready", commit="c0ffee")
    assert _json_body(resp)["record"]["handoff_state"] == "assigned_to_qa", _json_body(resp)


def _run_tests(s3, uuid, *, groups, actor="qa@example.com"):
    return project_api.handler(
        _event("POST", "/projects/{projectId}/run-tests", project_id=uuid,
               groups=groups, actor=actor),
        None,
    )


def _test_callback(s3, uuid, *, status, summary=None):
    body = {"status": status}
    if summary is not None:
        body["summary"] = summary
    return project_api.handler(
        _event("POST", "/projects/{projectId}/test-callback", project_id=uuid, body=body),
        None,
    )


def test_create_record_has_qa_test_defaults(s3):
    record = _create(s3, "p-qadef", "QA Defaults", actor="ba@example.com")
    assert record["qa_test_status"] == "not_run"
    assert record["qa_test_summary"] is None
    assert record["qa_test_last_run_at"] is None


def test_run_tests_by_qa_owner_sets_running_and_triggers_build(s3, monkeypatch):
    fake_cb = _FakeCodeBuild()
    monkeypatch.setenv("REPO_TEST_CODEBUILD_PROJECT", "aidlc-repo-test")
    monkeypatch.setattr(project_api, "_codebuild", lambda: fake_cb)

    _create(s3, "p-runtests", "Run Tests", actor="ba@example.com")
    _drive_to_assigned_to_qa(s3, "p-runtests")

    resp = _run_tests(s3, "p-runtests", groups=[QA_ROLE])
    assert resp["statusCode"] == 202, _json_body(resp)
    rec = _json_body(resp)
    assert rec["qa_test_status"] == "running"
    assert rec["qa_test_last_run_at"]
    assert len(fake_cb.calls) == 1
    overrides = {e["name"]: e["value"] for e in fake_cb.calls[0]["environmentVariablesOverride"]}
    assert overrides["PROJECT_UUID"] == "p-runtests"


def test_run_tests_by_non_owner_is_403(s3):
    _create(s3, "p-rt-rbac", "RT RBAC", actor="ba@example.com")
    _drive_to_assigned_to_qa(s3, "p-rt-rbac")
    # Project is QA-owned; a developer cannot start the tests.
    resp = _run_tests(s3, "p-rt-rbac", groups=[DEV_ROLE], actor="dev@example.com")
    assert resp["statusCode"] == 403
    assert _json_body(resp)["code"] == "FORBIDDEN"


def test_test_callback_records_passed_status_and_summary(s3):
    _create(s3, "p-tcb", "Test CB", actor="ba@example.com")
    _drive_to_assigned_to_qa(s3, "p-tcb")

    resp = _test_callback(s3, "p-tcb", status="passed", summary="42 passed in 3.1s")
    assert resp["statusCode"] == 200
    rec = _json_body(resp)["record"]
    assert rec["qa_test_status"] == "passed"
    assert rec["qa_test_summary"] == "42 passed in 3.1s"
    assert rec["qa_test_last_run_at"]
    # Handoff state is unchanged (QA decides whether to advance).
    assert rec["handoff_state"] == "assigned_to_qa"


def test_test_callback_failed_status(s3):
    _create(s3, "p-tcbf", "Test CB Fail", actor="ba@example.com")
    _drive_to_assigned_to_qa(s3, "p-tcbf")
    resp = _test_callback(s3, "p-tcbf", status="failed", summary="1 failed")
    assert resp["statusCode"] == 200
    assert _json_body(resp)["record"]["qa_test_status"] == "failed"


def test_test_callback_invalid_status_is_400(s3):
    _create(s3, "p-tcbbad", "Test CB Bad", actor="ba@example.com")
    _drive_to_assigned_to_qa(s3, "p-tcbbad")
    resp = _test_callback(s3, "p-tcbbad", status="maybe")
    assert resp["statusCode"] == 400


# ===========================================================================
# 12. Indexed listing: broad selection (GSI2), role queue (GSI1), pagination
#     (Req 3.1, 3.4, 3.5, 12.2, 12.3)
# ===========================================================================


def _list(s3, *, groups=None, actor="user@example.com", query=None):
    """Drive GET /projects with optional query-string parameters."""
    event = _event("GET", "/projects", groups=groups, actor=actor)
    event["queryStringParameters"] = query
    return project_api.handler(event, None)


def test_list_broad_selection_returns_all_projects(s3):
    """GET /projects with no params lists every project via the broad GSI2
    selection index (cross-creator visibility, Req 3.1/3.2)."""
    _create(s3, "p-l1", "L1", actor="alice@example.com")
    _create(s3, "p-l2", "L2", actor="bob@example.com")
    _create(s3, "p-l3", "L3", actor="carol@example.com")

    status, body = _json_body_and_status(_list(s3, groups=[SA_ROLE]))
    assert status == 200
    uuids = {p["project_uuid"] for p in body["projects"]}
    assert {"p-l1", "p-l2", "p-l3"} <= uuids
    # A single broad-selection query served the list (no per-record scan).
    assert s3.ddb.query_calls >= 1


def test_list_role_queue_filters_to_owning_role(s3):
    """GET /projects?role=<role> returns only projects currently owned by that
    role via the GSI1 role queue (Req 3.4 / 12.2)."""
    # p-ba stays BA-owned (draft); p-sa is advanced into SA ownership.
    _create(s3, "p-ba", "BA Owned", actor="ba@example.com")
    _create(s3, "p-sa", "SA Owned", actor="ba@example.com")
    _transition(s3, "p-sa", action="assign", groups=[BA_ROLE], actor="ba@example.com")
    _transition(s3, "p-sa", action="assign", groups=[BA_ROLE], actor="ba@example.com")

    # SA role queue: only p-sa (now owned by system-architect).
    status, body = _json_body_and_status(_list(s3, groups=[SA_ROLE], query={"role": SA_ROLE}))
    assert status == 200
    sa_uuids = {p["project_uuid"] for p in body["projects"]}
    assert sa_uuids == {"p-sa"}

    # BA role queue: only p-ba (still draft, BA-owned).
    status, body = _json_body_and_status(_list(s3, groups=[BA_ROLE], query={"role": BA_ROLE}))
    assert status == 200
    ba_uuids = {p["project_uuid"] for p in body["projects"]}
    assert ba_uuids == {"p-ba"}


def test_list_pagination_with_limit_and_next_cursor(s3):
    """A ``limit`` returns one page + a ``next`` cursor; following the cursor
    returns the remaining records with no overlap (Req 3.5)."""
    for i in range(5):
        _create(s3, f"p-page-{i}", f"Page {i}", actor="ba@example.com")

    status, page1 = _json_body_and_status(_list(s3, groups=[BA_ROLE], query={"limit": "2"}))
    assert status == 200
    assert len(page1["projects"]) == 2
    assert "next" in page1  # more remain

    seen = [p["project_uuid"] for p in page1["projects"]]
    cursor = page1["next"]
    # Walk the remaining pages.
    while cursor:
        status, page = _json_body_and_status(
            _list(s3, groups=[BA_ROLE], query={"limit": "2", "next": cursor})
        )
        assert status == 200
        seen.extend(p["project_uuid"] for p in page["projects"])
        cursor = page.get("next")

    # Every created project was seen exactly once across the pages.
    assert sorted(seen) == [f"p-page-{i}" for i in range(5)]
    assert len(seen) == len(set(seen))


def test_list_rejects_non_integer_limit(s3):
    """A non-integer ``limit`` is a 400 (INVALID_REQUEST), not a 500."""
    _create(s3, "p-badlimit", "Bad Limit", actor="ba@example.com")
    status, body = _json_body_and_status(
        _list(s3, groups=[BA_ROLE], query={"limit": "abc"})
    )
    assert status == 400
    assert body["code"] == "INVALID_REQUEST"


def _json_body_and_status(response):
    """Return ``(statusCode, parsed body)`` for a handler response."""
    return response["statusCode"], _json_body(response)


# ===========================================================================
# 13. Stage-brief endpoint: registry-derived active stage contract
#     (Req 6.2, 6.4, 7.1)
# ===========================================================================


def _stage_brief(s3, uuid, *, groups=None, actor="user@example.com"):
    """Drive GET /projects/{id}/stage-brief through the handler."""
    return project_api.handler(
        _event(
            "GET",
            "/projects/{projectId}/stage-brief",
            project_id=uuid,
            groups=groups,
            actor=actor,
        ),
        None,
    )


def _set_current_stage(s3, uuid, stage):
    """Move a persisted record's stage cursor (stands in for complete-stage,
    which is a later task) without touching the handler."""
    s3.ddb.bump_version(uuid, mutate=lambda r: r.__setitem__("current_stage", stage))


def test_stage_brief_owner_reads_seeded_stage_without_contract(s3):
    """The seeded cursor (`workspace_detection`) has no registry contract yet:
    the owner still gets a well-formed brief with the segment/owner resolved and
    empty produces/consumes (graceful degradation, Req 6.4)."""
    _create(s3, "p-brief-seed", "Brief Seed", actor="ba@example.com")

    status, brief = _json_body_and_status(
        _stage_brief(s3, "p-brief-seed", groups=[BA_ROLE], actor="ba@example.com")
    )
    assert status == 200
    assert brief["stage"] == "workspace_detection"
    assert brief["segment"] == "ba_segment"
    assert brief["owning_role"] == BA_ROLE
    # No contract declared for this stage → nulls + empty lists, not an error.
    assert brief["generator"] is None
    assert brief["skill"] is None
    assert brief["produces"] == []
    assert brief["consumes"] == []
    assert brief["approval_required"] is False


def test_stage_brief_derives_contract_with_slug_substituted_paths(s3):
    """For a stage WITH a registry contract, the brief carries generator/skill,
    the produces (required + acceptance) and consumes, and every declared path
    is slug-substituted under the project's artifact_prefix (Req 6.4)."""
    created = _create(s3, "p-brief", "Brief Project", actor="ba@example.com")
    prefix = created["artifact_prefix"]  # "brief_project"
    _set_current_stage(s3, "p-brief", "requirements_analysis")

    status, brief = _json_body_and_status(
        _stage_brief(s3, "p-brief", groups=[BA_ROLE], actor="ba@example.com")
    )
    assert status == 200
    assert brief["stage"] == "requirements_analysis"
    assert brief["segment"] == "ba_segment"
    assert brief["owning_role"] == BA_ROLE
    assert brief["generator"] == "kiro"
    assert brief["skill"] == "requirements-analysis"
    # Kiro-generated stage → completing it constitutes the recorded approval.
    assert brief["approval_required"] is True

    # produces path is slug-substituted; required + acceptance carried through.
    assert len(brief["produces"]) == 1
    produced = brief["produces"][0]
    assert produced["path"] == f"{prefix}/aidlc-docs/inception/requirements/requirements.md"
    assert produced["required"] is True
    assert "nonempty" in produced["acceptance"]

    # consumes are objects with a slug-substituted path.
    assert brief["consumes"] == [
        {"path": f"{prefix}/aidlc-docs/inception/requirements/uploaded"}
    ]


def test_stage_brief_by_non_owning_role_is_403(s3):
    """A non-owning role requesting the brief is rejected (owner-read, Req 6.4).
    The brief (which reveals artifact keys) is not disclosed off-turn."""
    _create(s3, "p-brief-rbac", "Brief RBAC", actor="ba@example.com")

    status, body = _json_body_and_status(
        _stage_brief(s3, "p-brief-rbac", groups=[SA_ROLE], actor="sa@example.com")
    )
    assert status == 403
    assert body["code"] == "FORBIDDEN"


def test_stage_brief_missing_project_is_404(s3):
    """The brief for an unknown project id is a 404 (not a 403)."""
    status, body = _json_body_and_status(
        _stage_brief(s3, "does-not-exist", groups=[BA_ROLE], actor="ba@example.com")
    )
    assert status == 404
    assert body["code"] == "NOT_FOUND"


def test_stage_brief_derivation_is_pure_over_registry(s3):
    """`_derive_stage_brief` is a pure function of the record + registry: it
    slug-substitutes paths for a `user_stories` cursor without a handler call."""
    record = {
        "artifact_prefix": "acme_widgets",
        "current_stage": "user_stories",
        "owning_role": BA_ROLE,
    }
    brief = project_api._derive_stage_brief(record)
    assert brief["stage"] == "user_stories"
    assert brief["segment"] == "ba_segment"
    assert brief["owning_role"] == BA_ROLE
    assert brief["generator"] == "kiro"
    assert brief["skill"] == "user-stories"
    assert brief["produces"][0]["path"] == \
        "acme_widgets/aidlc-docs/inception/user-stories/stories.md"
    assert brief["consumes"] == [
        {"path": "acme_widgets/aidlc-docs/inception/requirements/requirements.md"}
    ]

# ===========================================================================
# 14. Stage-artifact-url endpoint: owner-gated, produces-constrained presign
#     (Req 5.1, 7.2, 9.1, 9.4)
# ===========================================================================

# The BA `requirements_analysis` stage's single declared produces path
# (registry-relative). Slug-substituted, the concrete key is
# `{artifact_prefix}/` + this.
_REQ_ANALYSIS_PRODUCES = "aidlc-docs/inception/requirements/requirements.md"


def _stage_artifact_url(s3, uuid, path, *, groups=None, actor="user@example.com"):
    """Drive POST /projects/{id}/stage-artifact-url through the handler."""
    return project_api.handler(
        _event(
            "POST",
            "/projects/{projectId}/stage-artifact-url",
            project_id=uuid,
            body={"path": path} if path is not None else {},
            groups=groups,
            actor=actor,
        ),
        None,
    )


def test_stage_artifact_url_owner_registry_relative_path_mints_presign(s3):
    """The owner submitting a registry-relative produces path gets a short-TTL
    presigned PUT scoped to the slug-prefixed `{prefix}/aidlc-docs/...` key."""
    created = _create(s3, "p-art", "Art Project", actor="ba@example.com")
    prefix = created["artifact_prefix"]  # "art_project"
    _set_current_stage(s3, "p-art", "requirements_analysis")

    status, body = _json_body_and_status(
        _stage_artifact_url(
            s3, "p-art", _REQ_ANALYSIS_PRODUCES,
            groups=[BA_ROLE], actor="ba@example.com",
        )
    )
    assert status == 200, body
    assert body["key"] == f"{prefix}/{_REQ_ANALYSIS_PRODUCES}"
    assert body["upload_url"].startswith("https://")
    # Short-lived TTL (design Section 3.10 — "short TTL").
    assert 0 < body["expires_in"] <= 3600


def test_stage_artifact_url_accepts_already_slug_prefixed_path(s3):
    """A client may send the slug-prefixed form (as returned by stage-brief);
    it resolves to the same canonical key (Req 7.2 robustness)."""
    created = _create(s3, "p-art2", "Art Two", actor="ba@example.com")
    prefix = created["artifact_prefix"]
    _set_current_stage(s3, "p-art2", "requirements_analysis")

    slug_prefixed = f"{prefix}/{_REQ_ANALYSIS_PRODUCES}"
    status, body = _json_body_and_status(
        _stage_artifact_url(
            s3, "p-art2", slug_prefixed, groups=[BA_ROLE], actor="ba@example.com"
        )
    )
    assert status == 200, body
    assert body["key"] == slug_prefixed


def test_stage_artifact_url_by_non_owning_role_is_403(s3):
    """A non-owning role is rejected before any URL is minted (owner-gate,
    Req 5.1/9.4)."""
    _create(s3, "p-art-rbac", "Art RBAC", actor="ba@example.com")
    _set_current_stage(s3, "p-art-rbac", "requirements_analysis")

    status, body = _json_body_and_status(
        _stage_artifact_url(
            s3, "p-art-rbac", _REQ_ANALYSIS_PRODUCES,
            groups=[SA_ROLE], actor="sa@example.com",
        )
    )
    assert status == 403
    assert body["code"] == "FORBIDDEN"
    # No presigned URL was minted for the denied caller.
    assert s3.presigned_calls == 0


def test_stage_artifact_url_off_contract_path_is_400(s3):
    """A path that is not one of the active stage's produces is rejected as
    off-contract (Req 7.2) and mints no URL."""
    _create(s3, "p-art-off", "Art Off", actor="ba@example.com")
    _set_current_stage(s3, "p-art-off", "requirements_analysis")

    status, body = _json_body_and_status(
        _stage_artifact_url(
            s3, "p-art-off", "aidlc-docs/inception/requirements/not-declared.md",
            groups=[BA_ROLE], actor="ba@example.com",
        )
    )
    assert status == 400
    assert body["code"] == "OFF_CONTRACT_PATH"
    assert s3.presigned_calls == 0


def test_stage_artifact_url_missing_path_is_400(s3):
    """A request without a `path` is a 400 INVALID_REQUEST."""
    _create(s3, "p-art-nopath", "Art NoPath", actor="ba@example.com")
    _set_current_stage(s3, "p-art-nopath", "requirements_analysis")

    status, body = _json_body_and_status(
        _stage_artifact_url(
            s3, "p-art-nopath", None, groups=[BA_ROLE], actor="ba@example.com"
        )
    )
    assert status == 400
    assert body["code"] == "INVALID_REQUEST"


def test_stage_artifact_url_missing_project_is_404(s3):
    """An unknown project id is a 404 (not a 403)."""
    status, body = _json_body_and_status(
        _stage_artifact_url(
            s3, "does-not-exist", _REQ_ANALYSIS_PRODUCES,
            groups=[BA_ROLE], actor="ba@example.com",
        )
    )
    assert status == 404
    assert body["code"] == "NOT_FOUND"


def test_stage_artifact_url_seeded_stage_without_contract_is_off_contract(s3):
    """The seeded cursor (`workspace_detection`) declares no produces, so any
    path is off-contract → 400 (nothing to submit yet)."""
    _create(s3, "p-art-seed", "Art Seed", actor="ba@example.com")
    # cursor left at the seeded `workspace_detection` (no contract).

    status, body = _json_body_and_status(
        _stage_artifact_url(
            s3, "p-art-seed", _REQ_ANALYSIS_PRODUCES,
            groups=[BA_ROLE], actor="ba@example.com",
        )
    )
    assert status == 400
    assert body["code"] == "OFF_CONTRACT_PATH"


# ===========================================================================
# 15. Complete-stage endpoint: validate (lenient) then advance the cursor /
#     coarse state atomically (Req 6.3, 7.3, 7.4, 8.1, 8.2, 9.3, 14.1)
# ===========================================================================

# The BA `user_stories` stage's single declared produces path (registry-relative)
# and the coarse state it declares it advances to (`advances_state`).
_USER_STORIES_PRODUCES = "aidlc-docs/inception/user-stories/stories.md"


def _complete_stage(s3, uuid, *, groups=None, actor="user@example.com", note=None):
    """Drive POST /projects/{id}/complete-stage through the handler."""
    body = {}
    if note is not None:
        body["note"] = note
    return project_api.handler(
        _event(
            "POST",
            "/projects/{projectId}/complete-stage",
            project_id=uuid,
            body=body,
            groups=groups,
            actor=actor,
        ),
        None,
    )


def _set_record_fields(s3, uuid, **fields):
    """Mutate a persisted record's fields directly (stands in for prior steps)."""
    s3.ddb.bump_version(uuid, mutate=lambda r: r.update(fields))


def _good_requirements_doc():
    """A requirements.md that passes the requirements_analysis acceptance rules
    (nonempty, min_words:200, has_sections Functional/Non-Functional/Assumptions)."""
    body = " ".join(["requirement"] * 210)
    return (
        "# Requirements\n\n"
        f"## Functional Requirements\n\n{body}\n\n"
        f"## Non-Functional Requirements\n\n{body}\n\n"
        f"## Assumptions\n\n{body}\n"
    )


def _good_user_stories_doc():
    """A stories.md that passes the user_stories acceptance rules."""
    body = " ".join(["story"] * 160)
    return f"# User Stories\n\n{body}\n"


def _seed_artifact(s3, prefix, rel_path, content):
    """Seed a produced artifact at the canonical `{prefix}/{rel}` key."""
    s3.seed(BUCKET, f"{prefix}/{rel_path}", content.encode("utf-8"))


def test_complete_stage_intra_segment_advances_cursor_only(s3):
    """Completing `requirements_analysis` (no advances_state) advances the cursor
    to the next BA stage while leaving the coarse handoff_state unchanged
    (Req 6.3/7.3), records the artifact manifest (Req 9.3), and appends a
    stage-complete HIST item (Req 14.1)."""
    created = _create(s3, "p-cs-intra", "CS Intra", actor="ba@example.com")
    prefix = created["artifact_prefix"]
    _set_current_stage(s3, "p-cs-intra", "requirements_analysis")
    key = f"{prefix}/{_REQ_ANALYSIS_PRODUCES}"
    _seed_artifact(s3, prefix, _REQ_ANALYSIS_PRODUCES, _good_requirements_doc())

    status, body = _json_body_and_status(
        _complete_stage(s3, "p-cs-intra", groups=[BA_ROLE], actor="ba@example.com")
    )

    assert status == 200, body
    assert body["status"] == "advanced"
    assert body["warnings"] == []
    # Cursor advanced to the next BA stage; coarse state unchanged (still draft).
    assert body["current_stage"] == "user_stories"
    assert body["handoff_state"] == "draft"

    stored = s3.ddb.get_record("p-cs-intra")
    assert stored["current_stage"] == "user_stories"
    assert stored["handoff_state"] == "draft"
    assert stored["owning_role"] == BA_ROLE
    # Artifact manifest recorded (who/when/etag) for the produced key (Req 9.3).
    manifest = stored["artifacts"][key]
    assert manifest["submitted_by"] == "ba@example.com"
    assert manifest["submitted_at"]
    assert manifest["etag"]

    # Exactly one stage-complete HIST item appended atomically (Req 14.1).
    hist = s3.ddb.history_items("p-cs-intra")
    assert len(hist) == 1
    assert hist[0]["kind"] == "stage-complete"
    assert hist[0]["stage"] == "requirements_analysis"
    assert hist[0]["next_stage"] == "user_stories"
    assert hist[0]["from_state"] == "draft"
    assert hist[0]["to_state"] == "draft"
    assert hist[0]["warnings"] == []


def test_complete_stage_advances_coarse_state_when_declared(s3):
    """Completing the segment-terminal `user_stories` stage (advances_state=
    requirements_ready) advances the coarse handoff_state within the same owner
    and leaves the cursor on the terminal stage (Req 6.3, design Section 3.11)."""
    created = _create(s3, "p-cs-adv", "CS Advance", actor="ba@example.com")
    prefix = created["artifact_prefix"]
    _set_current_stage(s3, "p-cs-adv", "user_stories")
    _seed_artifact(s3, prefix, _USER_STORIES_PRODUCES, _good_user_stories_doc())

    status, body = _json_body_and_status(
        _complete_stage(s3, "p-cs-adv", groups=[BA_ROLE], actor="ba@example.com")
    )

    assert status == 200, body
    assert body["status"] == "advanced"
    # draft -> requirements_ready (legal forward for the BA), still BA-owned.
    assert body["handoff_state"] == "requirements_ready"
    # No next stage in the segment: the cursor stays on the terminal stage.
    assert body["current_stage"] == "user_stories"

    stored = s3.ddb.get_record("p-cs-adv")
    assert stored["handoff_state"] == "requirements_ready"
    assert stored["owning_role"] == BA_ROLE  # same owner (cross-persona handoff is separate)
    assert stored["current_stage"] == "user_stories"

    hist = s3.ddb.history_items("p-cs-adv")
    assert hist[-1]["from_state"] == "draft"
    assert hist[-1]["to_state"] == "requirements_ready"


def test_complete_stage_missing_required_artifact_blocks(s3):
    """A missing required `produces` artifact is the one hard precondition:
    status:"blocked", no advance, and NOTHING written (Req 7.4/8.1)."""
    _create(s3, "p-cs-miss", "CS Missing", actor="ba@example.com")
    _set_current_stage(s3, "p-cs-miss", "requirements_analysis")
    before = s3.ddb.get_record("p-cs-miss")
    # Deliberately do NOT seed the required requirements.md artifact.

    status, body = _json_body_and_status(
        _complete_stage(s3, "p-cs-miss", groups=[BA_ROLE], actor="ba@example.com")
    )

    assert status == 200, body
    assert body["status"] == "blocked"
    assert body["current_stage"] == "requirements_analysis"  # not advanced
    assert body["handoff_state"] == "draft"
    assert len(body["missing"]) == 1
    assert body["missing"][0]["reason"] == "missing"

    # No write happened: the record is byte-for-byte unchanged (Req 7.4).
    assert s3.ddb.get_record("p-cs-miss") == before


def test_complete_stage_empty_required_artifact_blocks(s3):
    """A present-but-empty (zero-length) required artifact also blocks (Req 8.1)."""
    created = _create(s3, "p-cs-empty", "CS Empty", actor="ba@example.com")
    prefix = created["artifact_prefix"]
    _set_current_stage(s3, "p-cs-empty", "requirements_analysis")
    _seed_artifact(s3, prefix, _REQ_ANALYSIS_PRODUCES, "")  # empty artifact

    status, body = _json_body_and_status(
        _complete_stage(s3, "p-cs-empty", groups=[BA_ROLE], actor="ba@example.com")
    )

    assert status == 200, body
    assert body["status"] == "blocked"
    assert body["missing"][0]["reason"] == "empty"
    assert s3.ddb.get_record("p-cs-empty")["current_stage"] == "requirements_analysis"


def test_complete_stage_lenient_warnings_recorded_and_advances(s3):
    """In lenient v1, non-required acceptance failures are surfaced as warnings
    but DO NOT block: the stage still advances and the warnings are recorded on
    the HIST item (Req 8.2/14.1)."""
    created = _create(s3, "p-cs-warn", "CS Warn", actor="ba@example.com")
    prefix = created["artifact_prefix"]
    _set_current_stage(s3, "p-cs-warn", "requirements_analysis")
    # Present + non-empty (clears the hard block) but fails min_words + sections.
    _seed_artifact(s3, prefix, _REQ_ANALYSIS_PRODUCES, "# Notes\n\nA short note.")

    status, body = _json_body_and_status(
        _complete_stage(s3, "p-cs-warn", groups=[BA_ROLE], actor="ba@example.com")
    )

    assert status == 200, body
    assert body["status"] == "advanced"
    assert body["current_stage"] == "user_stories"  # advanced despite warnings
    rules = {w["rule"] for w in body["warnings"]}
    assert any(r.startswith("min_words") for r in rules)
    assert any(r.startswith("has_sections") for r in rules)

    # Warnings are persisted on the stage-complete HIST item for observability.
    hist = s3.ddb.history_items("p-cs-warn")
    assert hist[-1]["warnings"], "warnings should be recorded in history"


def test_complete_stage_no_contract_stage_advances_cursor(s3):
    """A stage without a registry contract (the seeded `workspace_detection`)
    has nothing to validate; complete-stage simply advances the cursor."""
    _create(s3, "p-cs-nocontract", "CS NoContract", actor="ba@example.com")
    # cursor left at the seeded `workspace_detection` (no contract).

    status, body = _json_body_and_status(
        _complete_stage(s3, "p-cs-nocontract", groups=[BA_ROLE], actor="ba@example.com")
    )

    assert status == 200, body
    assert body["status"] == "advanced"
    assert body["current_stage"] == "reverse_engineering"
    assert body["warnings"] == []


def test_complete_stage_by_non_owning_role_is_403(s3):
    """A non-owning role completing the stage -> 403 before any validation/write."""
    created = _create(s3, "p-cs-rbac", "CS RBAC", actor="ba@example.com")
    prefix = created["artifact_prefix"]
    _set_current_stage(s3, "p-cs-rbac", "requirements_analysis")
    _seed_artifact(s3, prefix, _REQ_ANALYSIS_PRODUCES, _good_requirements_doc())
    before = s3.ddb.get_record("p-cs-rbac")

    status, body = _json_body_and_status(
        _complete_stage(s3, "p-cs-rbac", groups=[SA_ROLE], actor="sa@example.com")
    )

    assert status == 403
    assert body["code"] == "FORBIDDEN"
    # Untouched — no advance, no HIST item.
    assert s3.ddb.get_record("p-cs-rbac") == before
    assert s3.ddb.history_items("p-cs-rbac") == []


def test_complete_stage_missing_project_is_404(s3):
    """Completing a stage for an unknown project id -> 404 (not 403)."""
    status, body = _json_body_and_status(
        _complete_stage(s3, "does-not-exist", groups=[BA_ROLE], actor="ba@example.com")
    )
    assert status == 404
    assert body["code"] == "NOT_FOUND"


def test_complete_stage_illegal_advances_state_is_422(s3):
    """When a stage's declared advances_state is not a legal transition from the
    project's current coarse state, complete-stage rejects with 422 and leaves
    the record unchanged (Req 6.3, mirrors transition semantics).

    Contrived state: cursor on `user_stories` (advances_state=requirements_ready)
    while the project sits in `in_design` (SA-owned). in_design -> requirements_ready
    is neither a forward nor a request-changes edge, so it is illegal."""
    created = _create(s3, "p-cs-422", "CS FourTwoTwo", actor="ba@example.com")
    prefix = created["artifact_prefix"]
    # Move to an SA-owned coarse state with the cursor on user_stories, and seed
    # the required artifact so the missing/acceptance checks pass first.
    _set_record_fields(
        s3, "p-cs-422",
        handoff_state="in_design", owning_role=SA_ROLE, current_stage="user_stories",
    )
    _seed_artifact(s3, prefix, _USER_STORIES_PRODUCES, _good_user_stories_doc())
    before = s3.ddb.get_record("p-cs-422")

    status, body = _json_body_and_status(
        _complete_stage(s3, "p-cs-422", groups=[SA_ROLE], actor="sa@example.com")
    )

    assert status == 422, body
    assert body["code"] == "ILLEGAL_TRANSITION"
    # Unchanged: no cursor/state advance, no HIST item.
    assert s3.ddb.get_record("p-cs-422") == before
    assert s3.ddb.history_items("p-cs-422") == []


def test_complete_stage_concurrent_write_causes_409(s3):
    """A concurrent writer between the read and the transactional write yields
    HTTP 409 (the TransactWriteItems version+owner condition is violated)."""
    created = _create(s3, "p-cs-409", "CS Conflict", actor="ba@example.com")
    prefix = created["artifact_prefix"]
    _set_current_stage(s3, "p-cs-409", "requirements_analysis")
    _seed_artifact(s3, prefix, _REQ_ANALYSIS_PRODUCES, _good_requirements_doc())

    # The instant the handler reads the record, a concurrent writer bumps the
    # stored version so the transaction's `version = :expected` condition fails.
    def _concurrent_writer(client):
        client.bump_version(
            "p-cs-409",
            mutate=lambda rec: rec.update(name="Changed concurrently"),
        )

    s3.ddb.on_next_get(_concurrent_writer)

    status, body = _json_body_and_status(
        _complete_stage(s3, "p-cs-409", groups=[BA_ROLE], actor="ba@example.com")
    )

    assert status == 409, body
    assert body["code"] == "ETAG_MISMATCH"
    # The concurrent writer's value survived; the stage did not advance.
    stored = s3.ddb.get_record("p-cs-409")
    assert stored["name"] == "Changed concurrently"
    assert stored["current_stage"] == "requirements_analysis"
    assert s3.ddb.history_items("p-cs-409") == []


# ===========================================================================
# 16. Observability & audit (Req 14.1, 14.2, 14.3)
#
# The single append-only HIST# trail captures transitions AND stage
# completions (14.1); the read-only Maintenance persona can view cross-project
# status (broad list + ?state= GSI3 dashboard) but is 403 on any state-changing
# route (14.2); get_project exposes current_stage + handoff_state (14.3).
# ===========================================================================

MAINT_ROLE = "maintenance-specialist"
DEVOPS_ROLE = "devops-engineer"


def test_transition_appends_transition_hist_item(s3):
    """A forward transition by the owner appends an append-only HIST# item
    (kind:"transition") capturing who/from/to/when/note, atomically with the
    META update, WHILE keeping the in-record assignment_history additive
    (Req 14.1)."""
    _create(s3, "p-hist-t", "Hist Transition", actor="ba@example.com")

    resp = _transition(
        s3, "p-hist-t", action="assign", note="assigning downstream",
        groups=[BA_ROLE], actor="ba@example.com",
    )
    assert resp["statusCode"] == 200, _json_body(resp)

    # A single transition HIST item was written with the full audit tuple.
    hist = s3.ddb.history_items("p-hist-t")
    assert len(hist) == 1
    entry = hist[0]
    assert entry["kind"] == "transition"
    assert entry["from_state"] == "draft"
    assert entry["to_state"] == "requirements_ready"
    assert entry["actor"] == "ba@example.com"       # who
    assert entry["note"] == "assigning downstream"  # note
    assert entry["timestamp"]                        # when

    # The in-record assignment_history is preserved (additive), so existing
    # consumers keep working: seed + this transition = 2 entries.
    stored = s3.ddb.get_record("p-hist-t")
    assert len(stored["assignment_history"]) == 2


def test_hist_trail_captures_transitions_and_stage_completions(s3):
    """The one HIST# trail interleaves both kinds: a stage completion
    (kind:"stage-complete") and a later transition (kind:"transition"), ordered
    by the HIST#{ts}#{seq} sort key (Req 14.1)."""
    created = _create(s3, "p-hist-mix", "Hist Mix", actor="ba@example.com")
    prefix = created["artifact_prefix"]

    # Complete requirements_analysis (stage-complete HIST item).
    _set_current_stage(s3, "p-hist-mix", "requirements_analysis")
    _seed_artifact(s3, prefix, _REQ_ANALYSIS_PRODUCES, _good_requirements_doc())
    done = _complete_stage(s3, "p-hist-mix", groups=[BA_ROLE], actor="ba@example.com")
    assert done["statusCode"] == 200, _json_body(done)

    # Then a forward transition (transition HIST item).
    moved = _transition(s3, "p-hist-mix", action="assign", groups=[BA_ROLE],
                        actor="ba@example.com")
    assert moved["statusCode"] == 200, _json_body(moved)

    hist = s3.ddb.history_items("p-hist-mix")
    kinds = [h["kind"] for h in hist]
    assert kinds == ["stage-complete", "transition"]


def test_stage_complete_hist_records_validation_warnings(s3):
    """Validation warnings are recorded on the stage-complete HIST item for
    observability (Req 14.1/8.4) — confirms task 7 behavior."""
    created = _create(s3, "p-hist-warn", "Hist Warn", actor="ba@example.com")
    prefix = created["artifact_prefix"]
    _set_current_stage(s3, "p-hist-warn", "requirements_analysis")
    # A short doc that satisfies "nonempty" (required, present) but fails other
    # acceptance rules → lenient warnings, still advances.
    _seed_artifact(s3, prefix, _REQ_ANALYSIS_PRODUCES, "# Notes\n\nA short note.")

    resp = _complete_stage(s3, "p-hist-warn", groups=[BA_ROLE], actor="ba@example.com")
    assert resp["statusCode"] == 200, _json_body(resp)

    hist = s3.ddb.history_items("p-hist-warn")
    assert hist[-1]["kind"] == "stage-complete"
    assert hist[-1]["warnings"], "acceptance warnings must be recorded in history"


def test_maintenance_can_list_projects_cross_project(s3):
    """The read-only Maintenance persona can view every project via the broad
    (GSI2) list — cross-project status without owning any segment (Req 14.2)."""
    _create(s3, "p-m1", "Maint One", actor="ba@example.com")
    _create(s3, "p-m2", "Maint Two", actor="ba@example.com")

    status, body = _json_body_and_status(
        _list(s3, groups=[MAINT_ROLE], actor="maint@example.com")
    )
    assert status == 200
    uuids = {p["project_uuid"] for p in body["projects"]}
    assert {"p-m1", "p-m2"} <= uuids


def test_maintenance_state_dashboard_filters_via_gsi3(s3):
    """`GET /projects?state=<state>` returns only projects in that handoff state
    via the GSI3 state dashboard — the Maintenance/SLA cross-project view
    (Req 14.2/14.3)."""
    # p-draft stays in draft; p-adv is advanced to requirements_ready.
    _create(s3, "p-draft", "Still Draft", actor="ba@example.com")
    _create(s3, "p-adv", "Advanced", actor="ba@example.com")
    _transition(s3, "p-adv", action="assign", groups=[BA_ROLE], actor="ba@example.com")

    # State dashboard for requirements_ready → only p-adv.
    status, body = _json_body_and_status(
        _list(s3, groups=[MAINT_ROLE], actor="maint@example.com",
              query={"state": "requirements_ready"})
    )
    assert status == 200
    ready_uuids = {p["project_uuid"] for p in body["projects"]}
    assert ready_uuids == {"p-adv"}

    # State dashboard for draft → only p-draft.
    status, body = _json_body_and_status(
        _list(s3, groups=[MAINT_ROLE], actor="maint@example.com",
              query={"state": "draft"})
    )
    assert status == 200
    draft_uuids = {p["project_uuid"] for p in body["projects"]}
    assert draft_uuids == {"p-draft"}


def test_maintenance_can_read_one_project(s3):
    """The Maintenance persona can GET one project record (read route, not
    owner-gated) exposing its current_stage + handoff_state (Req 14.2/14.3)."""
    _create(s3, "p-m-read", "Maint Read", actor="ba@example.com")

    resp = project_api.handler(
        _event("GET", "/projects/{projectId}", project_id="p-m-read",
               groups=[MAINT_ROLE], actor="maint@example.com"),
        None,
    )
    status, body = _json_body_and_status(resp)
    assert status == 200
    assert body["current_stage"]
    assert body["handoff_state"] == "draft"


def test_maintenance_is_403_on_state_changing_routes(s3):
    """The Maintenance persona owns no segment, so every state-changing route
    rejects it with 403 and leaves the record unchanged (Req 14.2 — read-only)."""
    created = _create(s3, "p-m-ro", "Maint ReadOnly", actor="ba@example.com")
    prefix = created["artifact_prefix"]
    _set_current_stage(s3, "p-m-ro", "requirements_analysis")
    _seed_artifact(s3, prefix, _REQ_ANALYSIS_PRODUCES, _good_requirements_doc())
    before = s3.ddb.get_record("p-m-ro")

    # transition
    resp = _transition(s3, "p-m-ro", action="assign", groups=[MAINT_ROLE],
                       actor="maint@example.com")
    assert resp["statusCode"] == 403
    assert _json_body(resp)["code"] == "FORBIDDEN"

    # reassign
    resp = _transition(s3, "p-m-ro", action="assign", groups=[MAINT_ROLE],
                       actor="maint@example.com", resource_suffix="reassign")
    assert resp["statusCode"] == 403

    # complete-stage
    resp = _complete_stage(s3, "p-m-ro", groups=[MAINT_ROLE], actor="maint@example.com")
    assert resp["statusCode"] == 403

    # delete
    resp = project_api.handler(
        _event("DELETE", "/projects/{projectId}", project_id="p-m-ro",
               groups=[MAINT_ROLE], actor="maint@example.com"),
        None,
    )
    assert resp["statusCode"] == 403

    # The record is untouched and no HIST item was written by any attempt.
    assert s3.ddb.get_record("p-m-ro") == before
    assert s3.ddb.history_items("p-m-ro") == []


def test_get_project_exposes_current_stage_and_handoff_state(s3):
    """GET /projects/{id} surfaces both the coarse handoff_state and the
    fine-grained current_stage cursor for monitoring (Req 14.3)."""
    _create(s3, "p-expose", "Expose", actor="ba@example.com")

    resp = project_api.handler(
        _event("GET", "/projects/{projectId}", project_id="p-expose",
               groups=[BA_ROLE], actor="ba@example.com"),
        None,
    )
    status, body = _json_body_and_status(resp)
    assert status == 200
    # Coarse lifecycle state + fine-grained stage cursor both present. The
    # cursor is seeded to the first stage of the initial (BA) segment.
    assert body["handoff_state"] == "draft"
    assert body["current_stage"] == "workspace_detection"


def test_ingest_callback_auto_advance_appends_transition_hist(s3):
    """The ingest-callback auto-advance (implementation_submitted →
    assigned_to_qa) is a transition and is captured in the append-only HIST#
    trail (kind:"transition", system actor) — Req 14.1."""
    _create(s3, "p-ing-hist", "Ingest Hist", actor="ba@example.com")
    _drive_to_implementation_submitted(s3, "p-ing-hist")
    hist_before = len(s3.ddb.history_items("p-ing-hist"))

    resp = _ingest_callback(s3, "p-ing-hist", status="ready", commit="abc123")
    assert resp["statusCode"] == 200, _json_body(resp)
    assert _json_body(resp)["advanced"] is True

    hist = s3.ddb.history_items("p-ing-hist")
    # Exactly one NEW transition HIST item recorded by the auto-advance.
    assert len(hist) == hist_before + 1
    entry = hist[-1]
    assert entry["kind"] == "transition"
    assert entry["from_state"] == "implementation_submitted"
    assert entry["to_state"] == "assigned_to_qa"
    assert entry["actor"] == project_api._SYSTEM_INGEST_ACTOR


def test_ingest_callback_status_only_writes_no_hist_item(s3):
    """A status-only ingest callback (no state advance — e.g. a failed ingest)
    is not a transition and writes NO HIST item (Req 14.1: history captures
    transitions, not every field update)."""
    _create(s3, "p-ing-nohist", "Ingest NoHist", actor="ba@example.com")
    _drive_to_implementation_submitted(s3, "p-ing-nohist")
    hist_before = len(s3.ddb.history_items("p-ing-nohist"))

    resp = _ingest_callback(s3, "p-ing-nohist", status="failed")
    assert resp["statusCode"] == 200, _json_body(resp)
    assert _json_body(resp)["advanced"] is False
    # No state advance → no NEW HIST item beyond what the drive-to-submit
    # transitions already recorded.
    assert len(s3.ddb.history_items("p-ing-nohist")) == hist_before


def test_transitions_share_the_atomic_meta_plus_history_helper(s3):
    """Guard: a transition writes its META update and its HIST audit item
    together (one atomic op). Observed via the store: after a transition the
    project's version is bumped AND exactly one matching HIST item exists — the
    two can never diverge (Req 14.1)."""
    _create(s3, "p-atomic", "Atomic", actor="ba@example.com")
    resp = _transition(s3, "p-atomic", action="assign", groups=[BA_ROLE],
                       actor="ba@example.com")
    assert resp["statusCode"] == 200

    stored = s3.ddb.get_record("p-atomic")
    hist = s3.ddb.history_items("p-atomic")
    assert stored["version"] == 2                     # META updated
    assert len(hist) == 1 and hist[0]["kind"] == "transition"  # + its HIST item
    assert hist[0]["to_state"] == stored["handoff_state"]


# ===========================================================================
# 16. End-to-end pilot: BA `requirements_analysis` generation flow (Task 15)
#     create -> (cursor walk) -> stage-brief -> stage-artifact-url ->
#     [client PUT] -> complete-stage -> cursor advances to `user_stories`.
#     Every step drives the REAL handler; only the S3 upload (the presigned
#     PUT that a Kiro client would perform) is faked by seeding the exact key
#     the API minted. Kiro generation itself is faked by a fixture doc.
#     (Req 1.1, 6.3, 7.1, 7.2, 7.3, 8.1, 8.2)
# ===========================================================================


def test_e2e_ba_requirements_analysis_pilot_advances_to_user_stories(s3):
    """Genuine end-to-end of the BA `requirements_analysis` pilot, all through
    the real Project API handler.

    1. BA creates a project (cursor seeded to `workspace_detection`).
    2. The cursor is walked to `requirements_analysis` via real complete-stage
       calls over the two no-contract BA stages (registry-driven advancement).
    3. `get stage-brief` returns the Kiro contract (generator/skill/produces/
       consumes) for `requirements_analysis`.
    4. `stage-artifact-url` mints an owner-gated, produces-constrained presign
       for the declared `requirements.md`; the key it returns is exactly where
       the client uploads.
    5. The Kiro-generated artifact (a fixture doc satisfying acceptance) is
       PUT to that minted key (seed = the faked presigned upload).
    6. `complete-stage` validates acceptance (clean) and advances the cursor to
       `user_stories`; because `requirements_analysis` declares NO
       `advances_state`, the coarse `handoff_state` stays `draft` (intra-segment
       advance — design Section 3.11).
    """
    # 1. BA creates the project through the real handler.
    created = _create(s3, "p-e2e", "E2E Pilot", actor="ba@example.com")
    prefix = created["artifact_prefix"]  # "e2e_pilot"
    assert created["handoff_state"] == "draft"
    assert created["current_stage"] == "workspace_detection"

    # 2. Walk the cursor to requirements_analysis via REAL complete-stage calls
    #    over the two no-contract BA stages (nothing to validate; cursor moves).
    status, body = _json_body_and_status(
        _complete_stage(s3, "p-e2e", groups=[BA_ROLE], actor="ba@example.com")
    )
    assert status == 200 and body["current_stage"] == "reverse_engineering"
    status, body = _json_body_and_status(
        _complete_stage(s3, "p-e2e", groups=[BA_ROLE], actor="ba@example.com")
    )
    assert status == 200 and body["current_stage"] == "requirements_analysis"
    assert body["handoff_state"] == "draft"  # still BA-owned, coarse state unchanged

    # 3. Fetch the stage brief — the Kiro-native contract for this stage.
    status, brief = _json_body_and_status(
        _stage_brief(s3, "p-e2e", groups=[BA_ROLE], actor="ba@example.com")
    )
    assert status == 200
    assert brief["stage"] == "requirements_analysis"
    assert brief["generator"] == "kiro"
    assert brief["skill"] == "requirements-analysis"
    produced = brief["produces"][0]
    assert produced["required"] is True
    # The brief hands back the slug-substituted produces key to submit against.
    assert produced["path"] == f"{prefix}/{_REQ_ANALYSIS_PRODUCES}"
    # It consumes the uploaded requirements (slug-substituted).
    assert brief["consumes"] == [
        {"path": f"{prefix}/aidlc-docs/inception/requirements/uploaded"}
    ]

    # 4. Mint the owner-gated, produces-constrained presigned PUT for the
    #    declared artifact path (registry-relative form).
    status, url_body = _json_body_and_status(
        _stage_artifact_url(
            s3, "p-e2e", _REQ_ANALYSIS_PRODUCES,
            groups=[BA_ROLE], actor="ba@example.com",
        )
    )
    assert status == 200
    minted_key = url_body["key"]
    assert minted_key == f"{prefix}/{_REQ_ANALYSIS_PRODUCES}"
    assert url_body["upload_url"].startswith("https://")

    # 5. Kiro generates the deliverable (faked by a fixture) and the client PUTs
    #    it to the exact key the API minted — modeled by seeding that key.
    s3.seed(BUCKET, minted_key, _good_requirements_doc().encode("utf-8"))

    # 6. Complete the stage through the real handler: acceptance passes, the
    #    cursor advances to `user_stories`, coarse state stays `draft`.
    status, done = _json_body_and_status(
        _complete_stage(s3, "p-e2e", groups=[BA_ROLE],
                        actor="ba@example.com", note="requirements look complete")
    )
    assert status == 200, done
    assert done["status"] == "advanced"
    assert done["warnings"] == []
    assert done["current_stage"] == "user_stories"   # cursor advanced (Req 6.3/7.3)
    assert done["handoff_state"] == "draft"          # intra-segment: no advances_state

    # The advancement is durable and the artifact manifest was recorded (Req 9.3).
    stored = s3.ddb.get_record("p-e2e")
    assert stored["current_stage"] == "user_stories"
    assert stored["handoff_state"] == "draft"
    assert stored["owning_role"] == BA_ROLE
    manifest = stored["artifacts"][minted_key]
    assert manifest["submitted_by"] == "ba@example.com"
    assert manifest["etag"]

    # The stage-complete is auditable (Req 14.1): a HIST item for this stage.
    hist = s3.ddb.history_items("p-e2e")
    complete_items = [h for h in hist if h.get("kind") == "stage-complete"
                      and h.get("stage") == "requirements_analysis"]
    assert len(complete_items) == 1
    assert complete_items[0]["next_stage"] == "user_stories"
    assert complete_items[0]["warnings"] == []
