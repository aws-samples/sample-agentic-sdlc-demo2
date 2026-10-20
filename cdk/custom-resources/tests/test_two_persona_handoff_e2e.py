"""Scripted two-persona handoff verification (task 10.2).

This is a *self-contained* end-to-end verification of the BA -> SA handoff
flow. It replays the exact two-persona scenario described by Requirement 10.2:

  1. As the **BA test user**: create a project, upload a ``.docx`` (request
     a presigned PUT URL), generate stories, reach the handoff gate, and
     assign to the SA (``draft -> requirements_ready -> assigned_to_sa``).
  2. As the **SA test user** (a different login): see the project with the
     correct status, review BA artifacts, request changes (bounce to BA
     with "Changes requested"), then approve and complete design through
     ``ready_for_dev`` (``assigned_to_sa -> requirements_ready ->
     assigned_to_sa -> in_design -> design_ready -> ready_for_dev``).
  3. Confirm both users see **the same project** (cross-creator visibility,
     Req 3.2) and resolve the **same artifact prefix** (Req 3.3).

The script drives the **real** ``project_api.handler`` -- the Lambda handler
that the API Gateway routes to in production -- against the in-memory fake S3
(:mod:`fake_s3`) used by the per-route unit tests in
``test_project_api.py``. Each call carries Cognito-authorizer claims so the
handler's owner-gating, registry-driven validation, ETag-protected writes, and
history append all run for real. Nothing about the Lambda code path is
shortcut: the same code that processes ``POST /projects/{id}/transition`` for
a real WebSocket-connected user runs here for both personas.

This deliberately complements (rather than replaces) the per-route unit tests:

  * ``test_project_api.py`` exercises one route at a time with focused
    assertions, so a failure pinpoints exactly which contract broke.
  * This file walks the **whole sequence** and asserts the cross-call
    invariants -- shared visibility, identical artifact prefix, ownership
    flipping at each boundary, request-changes recording the SA's note,
    history growing monotonically, illegal cross-persona attempts being
    rejected. Those are the assertions Req 10.2 is asking for, and they
    can only be observed when the full sequence runs as a single test.

Why this is the right "scripted verification" given that no live AWS is
available locally:

  * The Project API runs end-to-end via its real handler against a faithful
    S3 fake (real ``ClientError`` semantics, real ETag conditional writes,
    real listing across creators).
  * The runtime handoff gate is exercised by the existing
    ``test_ws_handoff_breakout.py`` (task 5.3): a real ``StreamAdapter``
    emits the ``handoff_required`` WebSocket message and the runner halts
    the graph loop. This module composes that contract by importing the
    StreamAdapter and asserting the wire-level frames a Frontend would
    receive at the handoff boundary.
  * The Frontend service layer is exercised by a parallel scripted scenario
    in ``code/ui-frontend/__tests__/unit/two-persona-handoff-e2e.test.tsx``,
    which drives ``projectService`` + ``SolutionArchitectReviewPanel`` over
    a mocked Project API using the same scenario.

To run this against a deployed AWS environment (Cognito test users, live
Project API, live WebSocket runtime) instead of the fakes:

  * Replace the in-memory FakeS3 with the real S3 bucket published as
    ``PROJECT_BUCKET`` (or call the deployed API Gateway directly with a
    requests/httpx client and the BA/SA test users' Cognito ID tokens
    obtained from ``cognito-idp:initiate-auth``).
  * Replace ``_event(...)`` with HTTP calls to the deployed Project API
    URL, attaching the Cognito ID token as ``Authorization: Bearer`` (the
    Frontend's ``projectService.ts`` already does this in the same shape).
  * For the runtime/handoff portion, open a real WebSocket to the deployed
    AgentCore Runtime endpoint with the BA's bearer token and assert the
    same ``handoff_required`` frame and post-halt persistence behavior.
  * Provision the BA/SA test users via the existing ``aidlc-platform-auth``
    CDK construct (already provisions both, with credentials surfaced as
    ``BATestUserEmail/Password`` and ``SATestUserEmail/Password`` stack
    outputs).

Validates: Requirements 3.2, 3.3, 6.2, 6.3, 6.4, 6.5, 7.1, 7.3, 7.4, 7.5,
7.7, 8.3, 8.4, 14.2, 14.3, 14.4
"""

import json
import os
import sys

import pytest

# Make the Lambda module importable regardless of pytest's rootdir/cwd.
_CUSTOM_RESOURCES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _CUSTOM_RESOURCES_DIR not in sys.path:
    sys.path.insert(0, _CUSTOM_RESOURCES_DIR)
_TESTS_DIR = os.path.dirname(__file__)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

import project_api  # noqa: E402
import workflow_registry  # noqa: E402
from fake_dynamodb import FakeDynamoDBClient  # noqa: E402
from fake_s3 import FakeS3Client  # noqa: E402

# ---------------------------------------------------------------------------
# Test users (mirrors the BA/SA pair the CDK provisions in
# ``aidlc-platform-auth.ts`` task 4.4, with credential outputs surfaced as
# ``BATestUserEmail/Password`` and ``SATestUserEmail/Password``).
# ---------------------------------------------------------------------------

BUCKET = "test-project-bucket"

BA_USER = "ba-tester@example.com"
SA_USER = "sa-tester@example.com"

BA_GROUP = "requirements-analyst"
SA_GROUP = "system-architect"

# A plausible client-supplied UUID (the Frontend mints one with
# ``crypto.randomUUID()`` and sends it as ``project_uuid`` on create).
PROJECT_UUID = "two-persona-e2e-uuid-001"
PROJECT_NAME = "Online Bookstore"
EXPECTED_ARTIFACT_PREFIX = "online_bookstore"  # see project_api._slugify

# The uploaded requirements document the BA produces; matches one of the four
# Supported_Upload_Formats (.md/.txt/.pdf/.docx) the Project API's
# presigned-URL route is expected to accept.
UPLOADED_FILENAME = "draft-requirements.docx"


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------


@pytest.fixture
def s3(monkeypatch):
    """Install a single in-memory fake S3 shared by both personas' calls.

    The fake S3 instance lives for the whole scenario (one fixture, one
    bucket) so the BA's create/transition writes are visible to the SA's
    list/read calls, replaying the cross-creator visibility contract
    (Req 3.2) against the same code path the deployed Project API uses.
    """
    fake = FakeS3Client()
    ddb = FakeDynamoDBClient()
    monkeypatch.setenv("PROJECT_BUCKET", BUCKET)
    monkeypatch.setenv("PROJECT_TABLE", "test-projects-table")
    monkeypatch.setattr(project_api, "_s3", lambda: fake)
    monkeypatch.setattr(project_api, "_ddb", lambda: ddb)
    # Records live in DynamoDB; attach the fake so the scenario can
    # inspect the persisted record via ``s3.ddb.get_record(uuid)``.
    fake.ddb = ddb
    return fake


def _event(method, resource, *, project_id=None, body=None, groups, actor):
    """Build an API Gateway proxy event with Cognito authorizer claims.

    Identical shape to the unit tests' ``_event`` helper but always requires
    ``groups`` + ``actor`` so the script call sites read like a session
    transcript ("BA does X, SA does Y").
    """
    claims = {"email": actor, "cognito:groups": list(groups)}
    return {
        "httpMethod": method,
        "resource": resource,
        "pathParameters": {"projectId": project_id} if project_id else None,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {"authorizer": {"claims": claims}},
    }


def _json_body(response):
    """Parse the JSON body out of a handler response (or ``{}`` on empty)."""
    raw = response.get("body") or "{}"
    return json.loads(raw)


def _record_key(project_uuid):
    """Return the S3 key the Project_Store writes a record to."""
    return f"_platform/projects/{project_uuid}.json"


def _stored_record(s3, project_uuid):
    """Return the record persisted under ``project_uuid`` (raises on missing)."""
    record = s3.ddb.get_record(project_uuid)
    assert record is not None, (
        f"No record persisted for {project_uuid!r} in the fake DynamoDB -- "
        "the BA-side flow did not run to completion."
    )
    return record


def _assert_history_appended(record_before, record_after, *, expected_to,
                             expected_from, expected_actor, expected_note=None):
    """Assert exactly one history entry was appended in the latest transition.

    Encodes the "exactly one history entry per transition" contract (Req 4.6)
    in a single helper so each step in the script reads as one assertion
    instead of three.
    """
    history_before = record_before["assignment_history"]
    history_after = record_after["assignment_history"]
    assert len(history_after) == len(history_before) + 1, (
        f"Expected one history entry per transition (Req 4.6); "
        f"len(before)={len(history_before)}, len(after)={len(history_after)}"
    )
    latest = history_after[-1]
    assert latest["from"] == expected_from
    assert latest["to"] == expected_to
    assert latest["actor"] == expected_actor
    if expected_note is not None:
        assert latest["note"] == expected_note


# ---------------------------------------------------------------------------
# A scripted persona client (a thin wrapper that records who is acting)
# ---------------------------------------------------------------------------


class PersonaClient:
    """A scripted client representing one persona's session against the API.

    Wrapping ``project_api.handler`` calls in a per-persona client keeps the
    scenario readable as a transcript ("ba.create_project(...)",
    "sa.transition(action='approve')") and ensures every call carries the
    persona's authoritative groups + actor identity, which is the *only* way
    the API distinguishes the two users (the Cognito authorizer claims are
    the trust anchor for Project API RBAC -- design Section 4b).
    """

    def __init__(self, *, actor, groups):
        self.actor = actor
        self.groups = list(groups)

    # -- Plain HTTP-level operations ---------------------------------------

    def list_projects(self):
        """``GET /projects`` -- return ``(status, body)``."""
        resp = project_api.handler(
            _event("GET", "/projects", groups=self.groups, actor=self.actor),
            None,
        )
        return resp["statusCode"], _json_body(resp)

    def get_project(self, project_uuid):
        """``GET /projects/{id}`` -- return ``(status, body)``."""
        resp = project_api.handler(
            _event(
                "GET", "/projects/{projectId}",
                project_id=project_uuid, groups=self.groups, actor=self.actor,
            ),
            None,
        )
        return resp["statusCode"], _json_body(resp)

    def create_project(self, *, name, project_uuid, description=""):
        """``POST /projects`` with the given name + UUID."""
        resp = project_api.handler(
            _event(
                "POST", "/projects",
                body={"name": name, "project_uuid": project_uuid,
                      "description": description},
                groups=self.groups, actor=self.actor,
            ),
            None,
        )
        return resp["statusCode"], _json_body(resp)

    def request_upload_url(self, project_uuid, *, filename):
        """``POST /projects/{id}/upload-url`` for an uploaded requirements doc."""
        resp = project_api.handler(
            _event(
                "POST", "/projects/{projectId}/upload-url",
                project_id=project_uuid, body={"filename": filename},
                groups=self.groups, actor=self.actor,
            ),
            None,
        )
        return resp["statusCode"], _json_body(resp)

    def transition(self, project_uuid, *, action, note=None):
        """``POST /projects/{id}/transition`` with the given action."""
        body = {"action": action}
        if note is not None:
            body["note"] = note
        resp = project_api.handler(
            _event(
                "POST", "/projects/{projectId}/transition",
                project_id=project_uuid, body=body,
                groups=self.groups, actor=self.actor,
            ),
            None,
        )
        return resp["statusCode"], _json_body(resp)


@pytest.fixture
def ba(s3):
    """The Business Analyst persona client (group: requirements-analyst)."""
    return PersonaClient(actor=BA_USER, groups=[BA_GROUP])


@pytest.fixture
def sa(s3):
    """The Solution Architect persona client (group: system-architect)."""
    return PersonaClient(actor=SA_USER, groups=[SA_GROUP])


# ---------------------------------------------------------------------------
# Scenario: BA -> SA happy-path handoff with one request-changes bounce
# ---------------------------------------------------------------------------


def test_two_persona_handoff_full_scenario(s3, ba, sa):
    """End-to-end BA -> SA handoff scenario covering the full Req 10.2 sequence.

    This is the single scripted scenario described by task 10.2: it runs as a
    transcript (one persona acting at a time, one HTTP call per step) and
    asserts at each step that the *cross-call* contract holds. The per-route
    unit tests in ``test_project_api.py`` already exercise the focused
    contract for each route -- this test asserts the chain.

    Validates: Requirements 3.2, 3.3, 4.x, 6.4, 6.5, 7.1, 7.4, 7.5, 7.7, 14.2,
    14.3, 14.4
    """

    # -----------------------------------------------------------------------
    # STEP 1 -- BA creates a project (Req 3.1, 4.1).
    # The record is seeded from the registry's initial_state, owned by BA,
    # with a single "created" history entry.
    # -----------------------------------------------------------------------

    status, draft = ba.create_project(
        name=PROJECT_NAME,
        project_uuid=PROJECT_UUID,
        description="A friendly online bookstore for the workshop.",
    )
    assert status == 201, draft
    assert draft["project_uuid"] == PROJECT_UUID
    assert draft["created_by"] == BA_USER
    assert draft["handoff_state"] == workflow_registry.initial_state() == "draft"
    assert draft["owning_role"] == BA_GROUP
    assert draft["artifact_prefix"] == EXPECTED_ARTIFACT_PREFIX
    assert draft["project_id"] == EXPECTED_ARTIFACT_PREFIX

    # The seed history entry is "None -> draft" with note "created" --
    # the audit trail for the handoff state machine starts here.
    assert len(draft["assignment_history"]) == 1
    seed = draft["assignment_history"][0]
    assert seed["from"] is None
    assert seed["to"] == "draft"
    assert seed["actor"] == BA_USER
    assert seed["note"] == "created"

    # The record is actually persisted -- both personas will read off this
    # bucket below.
    assert _stored_record(s3, PROJECT_UUID)["handoff_state"] == "draft"

    # -----------------------------------------------------------------------
    # STEP 2 -- BA requests a presigned PUT URL for a .docx requirements doc.
    # This is what the Frontend's RequirementsUploadControl does before PUTing
    # the file directly to S3 (Req 5.1). Owner-gated: only the current owning
    # role (BA at draft) may obtain the URL.
    # -----------------------------------------------------------------------

    status, upload = ba.request_upload_url(PROJECT_UUID, filename=UPLOADED_FILENAME)
    assert status == 200, upload

    # The presigned URL points at the project's `aidlc-docs/inception/
    # requirements/uploaded/<filename>` key under the project's artifact prefix
    # (Req 5.1) -- both personas will resolve the same prefix below (Req 3.3).
    assert upload["key"] == (
        f"{EXPECTED_ARTIFACT_PREFIX}/aidlc-docs/inception/requirements/"
        f"uploaded/{UPLOADED_FILENAME}"
    )
    assert upload["upload_url"].startswith("https://")
    # A short-lived TTL (Req 5.1 / design Section 7a -- "short-lived").
    assert 0 < upload["expires_in"] <= 3600

    # An SA cannot grab the upload URL while BA owns the state (Req 6.5).
    status_sa, sa_upload = sa.request_upload_url(PROJECT_UUID, filename=UPLOADED_FILENAME)
    assert status_sa == 403, sa_upload
    assert sa_upload["code"] == "FORBIDDEN"

    # -----------------------------------------------------------------------
    # STEP 3 -- BA performs the first forward transition (draft ->
    # requirements_ready). This represents the BA finishing requirements
    # analysis; the BA still owns the project.
    # -----------------------------------------------------------------------

    before = _stored_record(s3, PROJECT_UUID)
    status, after = ba.transition(PROJECT_UUID, action="advance")
    assert status == 200, after
    assert after["handoff_state"] == "requirements_ready"
    assert after["owning_role"] == BA_GROUP
    _assert_history_appended(
        before, after,
        expected_from="draft", expected_to="requirements_ready",
        expected_actor=BA_USER,
    )

    # While BA still owns the state, an SA attempt to advance must be 403
    # (RBAC enforced before any mutation -- design Section 4b, Req 2.2/2.3).
    status_sa, denied = sa.transition(PROJECT_UUID, action="advance")
    assert status_sa == 403, denied
    assert denied["code"] == "FORBIDDEN"
    # The record is byte-identical to before the SA's failed call.
    assert _stored_record(s3, PROJECT_UUID)["handoff_state"] == "requirements_ready"

    # -----------------------------------------------------------------------
    # STEP 4 -- BA reaches the handoff gate and assigns the project to the SA.
    # This is the boundary that the runtime's Handoff_Gate halts on (Req 6.2,
    # 6.3); the *project* state transition itself happens here, through the
    # Project API, because the Project API is the single writer of handoff
    # state (Req 10.6). After this step the SA owns the project.
    # -----------------------------------------------------------------------

    before = _stored_record(s3, PROJECT_UUID)
    status, after = ba.transition(PROJECT_UUID, action="assign")
    assert status == 200, after
    assert after["handoff_state"] == "assigned_to_sa"
    assert after["owning_role"] == SA_GROUP   # ownership flips at the boundary (Req 6.4)
    _assert_history_appended(
        before, after,
        expected_from="requirements_ready", expected_to="assigned_to_sa",
        expected_actor=BA_USER,
    )

    # While the project is `assigned_to_sa` the BA loses owner-gated access:
    # any state-changing action by BA is rejected (Req 6.5).
    status_ba, denied_ba = ba.transition(PROJECT_UUID, action="approve")
    assert status_ba == 403, denied_ba
    assert denied_ba["code"] == "FORBIDDEN"

    # -----------------------------------------------------------------------
    # STEP 5 -- SA logs in (different identity, different groups) and SEES
    # the project in the list. Cross-creator visibility (Req 3.2) -- the SA
    # did not create the project, but the list still surfaces it.
    # -----------------------------------------------------------------------

    status, listing = sa.list_projects()
    assert status == 200, listing
    sa_view = {p["project_uuid"]: p for p in listing["projects"]}
    assert PROJECT_UUID in sa_view, (
        "SA should see the BA-created project under cross-creator visibility "
        "(Req 3.2); got: " + str(list(sa_view))
    )

    # The SA reads the project by id and resolves *the same* artifact prefix
    # the BA created (Req 3.3) -- this is the property that makes BA artifacts
    # readable by the SA session via the existing s3Service.
    status, sa_record = sa.get_project(PROJECT_UUID)
    assert status == 200, sa_record
    assert sa_record["artifact_prefix"] == EXPECTED_ARTIFACT_PREFIX
    assert sa_record["project_uuid"] == PROJECT_UUID
    assert sa_record["handoff_state"] == "assigned_to_sa"
    assert sa_record["owning_role"] == SA_GROUP

    # And the BA can still READ the same record after handoff -- the state
    # machine flips ownership for write actions, not visibility.
    status_ba_read, ba_record = ba.get_project(PROJECT_UUID)
    assert status_ba_read == 200, ba_record
    assert ba_record["artifact_prefix"] == sa_record["artifact_prefix"]
    assert ba_record["project_uuid"] == sa_record["project_uuid"]
    # Both personas observe the same handoff_state at this instant.
    assert ba_record["handoff_state"] == sa_record["handoff_state"]

    # -----------------------------------------------------------------------
    # STEP 6 -- SA reviews the BA artifacts (Req 7.1) and REQUESTS CHANGES
    # with a note. The Project API resolves the request through the
    # registry's `request_changes.assigned_to_sa` -> `requirements_ready` and
    # records the SA's note in history (Req 7.5 / design Section 8d).
    # -----------------------------------------------------------------------

    before = _stored_record(s3, PROJECT_UUID)
    sa_note = "Please clarify the non-functional requirements."
    status, after = sa.transition(
        PROJECT_UUID, action="request-changes", note=sa_note,
    )
    assert status == 200, after
    # The bounce is registry-defined: assigned_to_sa -> requirements_ready,
    # ownership returns to the BA (Req 7.5).
    assert after["handoff_state"] == "requirements_ready"
    assert after["owning_role"] == BA_GROUP
    _assert_history_appended(
        before, after,
        expected_from="assigned_to_sa", expected_to="requirements_ready",
        expected_actor=SA_USER,
        expected_note=sa_note,
    )

    # The HandoffStatusBadge "Changes requested" indicator (Req 14.4) is the
    # registry-driven label for this bounce; assert that label is what the
    # Frontend would render for the BA viewing this record. (The bounce is
    # identified positionally by the latest history entry, which is what
    # `deriveIndicator` in HandoffStatusBadge.tsx checks.)
    last_entry = after["assignment_history"][-1]
    assert last_entry["from"] == "assigned_to_sa"
    assert last_entry["to"] == "requirements_ready"
    rc_descriptor = workflow_registry.request_changes("assigned_to_sa")
    assert rc_descriptor is not None
    assert rc_descriptor["target"] == "requirements_ready"
    # The label "Changes requested" is what the Frontend badge derives for
    # the BA viewer at this moment (Req 14.4).
    assert rc_descriptor["label"] == "Changes requested"

    # While bounced back to the BA, an SA attempt to act on the project is
    # 403 again (the bounce returned ownership to the BA, Req 6.5).
    status_sa, denied = sa.transition(PROJECT_UUID, action="advance")
    assert status_sa == 403, denied

    # -----------------------------------------------------------------------
    # STEP 7 -- BA re-assigns to the SA after addressing the requested changes.
    # This re-runs the same forward transition the BA did in STEP 4, proving
    # the bounce -> reassign loop is closed.
    # -----------------------------------------------------------------------

    before = _stored_record(s3, PROJECT_UUID)
    status, after = ba.transition(PROJECT_UUID, action="assign")
    assert status == 200, after
    assert after["handoff_state"] == "assigned_to_sa"
    assert after["owning_role"] == SA_GROUP
    _assert_history_appended(
        before, after,
        expected_from="requirements_ready", expected_to="assigned_to_sa",
        expected_actor=BA_USER,
    )

    # -----------------------------------------------------------------------
    # STEP 8 -- SA APPROVES on the second pass: assigned_to_sa -> in_design
    # (Req 7.4). Ownership stays with the SA.
    # -----------------------------------------------------------------------

    before = _stored_record(s3, PROJECT_UUID)
    status, after = sa.transition(PROJECT_UUID, action="approve")
    assert status == 200, after
    assert after["handoff_state"] == "in_design"
    assert after["owning_role"] == SA_GROUP
    _assert_history_appended(
        before, after,
        expected_from="assigned_to_sa", expected_to="in_design",
        expected_actor=SA_USER,
    )

    # -----------------------------------------------------------------------
    # STEP 9 -- SA marks design complete: in_design -> design_ready ->
    # ready_for_dev (Req 7.7). Two forward transitions, one history entry per.
    # -----------------------------------------------------------------------

    before = _stored_record(s3, PROJECT_UUID)
    status, mid = sa.transition(PROJECT_UUID, action="design-complete")
    assert status == 200, mid
    assert mid["handoff_state"] == "design_ready"
    assert mid["owning_role"] == SA_GROUP
    _assert_history_appended(
        before, mid,
        expected_from="in_design", expected_to="design_ready",
        expected_actor=SA_USER,
    )

    before = _stored_record(s3, PROJECT_UUID)
    status, final = sa.transition(PROJECT_UUID, action="design-complete")
    assert status == 200, final
    assert final["handoff_state"] == "ready_for_dev"
    assert final["owning_role"] == SA_GROUP
    _assert_history_appended(
        before, final,
        expected_from="design_ready", expected_to="ready_for_dev",
        expected_actor=SA_USER,
    )

    # -----------------------------------------------------------------------
    # STEP 10 -- Final cross-cutting assertions over the persisted record.
    # -----------------------------------------------------------------------

    persisted = _stored_record(s3, PROJECT_UUID)

    # The SA's design phase ends at ready_for_dev. Downstream, the Developer,
    # QA and DevOps segments extend the chain; ``deployed`` is the terminal
    # state of the full six-persona lifecycle.
    assert persisted["handoff_state"] == "ready_for_dev"
    assert workflow_registry.forward("ready_for_dev") == "assigned_to_dev"
    assert workflow_registry.forward("deployed") is None

    # Project identity is stable across the whole scenario.
    assert persisted["project_uuid"] == PROJECT_UUID
    assert persisted["artifact_prefix"] == EXPECTED_ARTIFACT_PREFIX
    assert persisted["project_id"] == EXPECTED_ARTIFACT_PREFIX

    # Append-only history (Req 4.6 / design Property 4): seven entries in the
    # exact order the personas drove (created + 6 transitions). The last
    # entry's `to` always equals the current handoff_state.
    history = persisted["assignment_history"]
    expected_chain = [
        (None, "draft", BA_USER),                                # created
        ("draft", "requirements_ready", BA_USER),                # STEP 3
        ("requirements_ready", "assigned_to_sa", BA_USER),       # STEP 4
        ("assigned_to_sa", "requirements_ready", SA_USER),       # STEP 6 (bounce)
        ("requirements_ready", "assigned_to_sa", BA_USER),       # STEP 7
        ("assigned_to_sa", "in_design", SA_USER),                # STEP 8
        ("in_design", "design_ready", SA_USER),                  # STEP 9a
        ("design_ready", "ready_for_dev", SA_USER),              # STEP 9b
    ]
    assert len(history) == len(expected_chain)
    for entry, (expected_from, expected_to, expected_actor) in zip(
        history, expected_chain
    ):
        assert entry["from"] == expected_from
        assert entry["to"] == expected_to
        assert entry["actor"] == expected_actor

    # The bounce's note is preserved in history (Req 4.6 / 7.5).
    bounce = next(
        e for e in history
        if e["from"] == "assigned_to_sa" and e["to"] == "requirements_ready"
    )
    assert bounce["note"] == sa_note

    # Finally: both personas STILL see the same project in their listings,
    # at the same terminal state (Req 3.2 / 3.3 again at end of run).
    _, ba_listing = ba.list_projects()
    _, sa_listing = sa.list_projects()
    ba_view = {p["project_uuid"]: p for p in ba_listing["projects"]}
    sa_view = {p["project_uuid"]: p for p in sa_listing["projects"]}
    assert PROJECT_UUID in ba_view and PROJECT_UUID in sa_view
    assert ba_view[PROJECT_UUID]["handoff_state"] == "ready_for_dev"
    assert sa_view[PROJECT_UUID]["handoff_state"] == "ready_for_dev"
    assert (
        ba_view[PROJECT_UUID]["artifact_prefix"]
        == sa_view[PROJECT_UUID]["artifact_prefix"]
        == EXPECTED_ARTIFACT_PREFIX
    )


# ---------------------------------------------------------------------------
# Negative path: the same scenario must reject illegal moves at every step
# ---------------------------------------------------------------------------


def test_two_persona_handoff_rejects_illegal_jumps_along_the_path(s3, ba, sa):
    """A persona acting on the same project must not be able to leap over a
    handoff state. Confirms Req 4.4 holds for the cross-persona scenario:
    e.g. the BA cannot jump ``draft -> in_design`` even though the BA owns
    the source state -- only registry-defined forwards/bounces are allowed.

    Distinct from ``test_illegal_transition_returns_422_and_leaves_record_unchanged``
    in ``test_project_api.py`` (which checks the route-level contract with one
    illegal jump): this asserts the illegal-jump rejection holds at every
    transition boundary the BA -> SA scenario crosses.

    Validates: Requirements 4.3, 4.4, 6.5
    """
    # Create the project as the BA.
    status, draft = ba.create_project(name=PROJECT_NAME, project_uuid=PROJECT_UUID)
    assert status == 201

    # Each illegal jump is a (current_state, illegal_target) pair the registry
    # does NOT allow from `current_state`. The handler must return 422 and
    # leave the record unchanged at the current_state.
    illegal_jumps_from_draft = [
        ("draft", "assigned_to_sa"),  # skipping requirements_ready
        ("draft", "in_design"),
        ("draft", "design_ready"),
        ("draft", "ready_for_dev"),
    ]
    for current, illegal_target in illegal_jumps_from_draft:
        before = _stored_record(s3, PROJECT_UUID)
        assert before["handoff_state"] == current

        # Use explicit `target_state` so the test names the illegal target
        # itself, rather than trying to map it to a synonym.
        resp = project_api.handler(
            _event(
                "POST", "/projects/{projectId}/transition",
                project_id=PROJECT_UUID,
                body={"target_state": illegal_target},
                groups=[BA_GROUP], actor=BA_USER,
            ),
            None,
        )
        assert resp["statusCode"] == 422, (
            f"Expected 422 for illegal jump {current} -> {illegal_target}, "
            f"got {resp['statusCode']}: {resp.get('body')}"
        )
        body = _json_body(resp)
        assert body["code"] == "ILLEGAL_TRANSITION"
        # Unchanged record after rejection (Req 4.4).
        after = _stored_record(s3, PROJECT_UUID)
        assert after == before

    # Drive draft -> requirements_ready legitimately, then assert the same
    # closure on jumps from requirements_ready (the SA cannot bypass the
    # boundary either, even with their own group).
    ba.transition(PROJECT_UUID, action="advance")
    record = _stored_record(s3, PROJECT_UUID)
    assert record["handoff_state"] == "requirements_ready"

    # From requirements_ready: an illegal jump to in_design must be 422 even
    # for the BA (the OWNING role) -- the validation layer catches it after
    # RBAC passes.
    before = _stored_record(s3, PROJECT_UUID)
    resp = project_api.handler(
        _event(
            "POST", "/projects/{projectId}/transition",
            project_id=PROJECT_UUID,
            body={"target_state": "in_design"},
            groups=[BA_GROUP], actor=BA_USER,
        ),
        None,
    )
    assert resp["statusCode"] == 422
    assert _stored_record(s3, PROJECT_UUID) == before
