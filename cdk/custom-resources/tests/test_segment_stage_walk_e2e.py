"""Per-segment Kiro-native stage-walk verification (kiro-native-platform task 16).

Task 15 piloted the BA ``requirements_analysis`` stage end to end through the
real ``complete-stage`` handler. Task 16 rolls the remaining active stages onto
the Kiro-native architecture (``generator: kiro`` contracts + skills + ``advances_state``). This
module is the per-segment E2E coverage for that roll-forward: it drives the
**real** ``project_api.handler`` ``POST /complete-stage`` route against the
in-memory fakes (:mod:`fake_s3` / :mod:`fake_dynamodb`) and asserts that
completing a segment's stages **in order** walks the coarse ``handoff_state``
along the segment's owned forward chain to the segment-terminal owned state:

  * **SA**   ``assigned_to_sa -> in_design -> design_ready -> ready_for_dev``
             over workflow_planning, nfr_requirements, nfr_design,
             application_design, units_generation, inception_complete.
  * **QA**   ``assigned_to_qa -> in_qa -> qa_passed`` over test_planning,
             test_execution, qa_report.
  * **DevOps** ``assigned_to_devops -> in_deployment -> deployed`` over
             deployment_planning, release.

For each stage the test seeds the stage's **required** ``produces`` artifact(s)
into the fake S3 (content synthesized to satisfy the declared ``acceptance``
rules — standing in for Kiro's generation), then calls ``complete-stage`` for
real: the handler HeadObjects the required artifact (the missing/empty hard
block), runs lenient acceptance, advances the cursor and — where the stage
declares ``advances_state`` — the coarse state via ``validate_transition``.

The **Dev -> QA boundary stays the Git round-trip** (design Section 3.8/6.5):
``construction_handoff`` produces the construction brief but declares **no**
``advances_state`` — completing it must NOT move the coarse state off
``assigned_to_dev``. The Dev test asserts that, then drives the retained Git
round-trip (submit gated on a linked repo -> ingest callback -> ``assigned_to_qa``)
to confirm the boundary is unchanged.

Validates: Requirements 4.3, 4.4, 6.1, 6.3, 7.1, 7.2, 7.3
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

import acceptance  # noqa: E402
import project_api  # noqa: E402
import workflow_registry as reg  # noqa: E402
from fake_dynamodb import FakeDynamoDBClient  # noqa: E402
from fake_s3 import FakeS3Client  # noqa: E402

BUCKET = "test-project-bucket"

BA_USER, BA_GROUP = "ba-tester@example.com", "requirements-analyst"
SA_USER, SA_GROUP = "sa-tester@example.com", "system-architect"
DEV_USER, DEV_GROUP = "dev-tester@example.com", "software-developer"
QA_USER, QA_GROUP = "qa-tester@example.com", "qa-engineer"
OPS_USER, OPS_GROUP = "devops-tester@example.com", "devops-engineer"

REPO_URL = "https://github.com/acme/widget"


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3Client()
    ddb = FakeDynamoDBClient()
    monkeypatch.setenv("PROJECT_BUCKET", BUCKET)
    monkeypatch.setenv("PROJECT_TABLE", "test-projects-table")
    # Lenient acceptance is the v1 default; make it explicit so the walk is not
    # affected by an ambient ACCEPTANCE_MODE=strict in the environment.
    monkeypatch.setenv("ACCEPTANCE_MODE", "lenient")
    # Ingestion CodeBuild is not configured in the scripted run.
    monkeypatch.delenv("REPO_INGEST_CODEBUILD_PROJECT", raising=False)
    monkeypatch.setattr(project_api, "_s3", lambda: fake)
    monkeypatch.setattr(project_api, "_ddb", lambda: ddb)
    fake.ddb = ddb
    return fake


# ---------------------------------------------------------------------------
# Event + call helpers (mirror the six-persona harness)
# ---------------------------------------------------------------------------


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


def _create(uuid, name):
    resp = project_api.handler(
        _event("POST", "/projects", body={"name": name, "project_uuid": uuid},
               groups=[BA_GROUP], actor=BA_USER),
        None,
    )
    assert resp["statusCode"] == 201, _body(resp)
    return _body(resp)


def _seed_segment(s3, uuid, name, *, state, stage):
    """Create a project and place it at ``state`` with the cursor at ``stage``.

    Creates the record for real (seeded from the registry initial_state), then
    mutates the persisted DynamoDB item to the target segment's entry state,
    owning role, and stage cursor so the ``complete-stage`` handler runs for
    real from that point. Returns the created record (for its ``artifact_prefix``).
    """
    created = _create(uuid, name)
    owner = reg.owning_role(state)
    s3.ddb.bump_version(uuid, mutate=lambda r: r.update(
        {"handoff_state": state, "owning_role": owner, "current_stage": stage}))
    return created


def _compliant_content(acceptance_rules):
    """Synthesize markdown content that satisfies the declared acceptance rules.

    Stands in for Kiro's generation: emits every ``has_sections`` heading and
    pads to the ``min_words`` threshold so the lenient acceptance run produces no
    warnings (the walk asserts a clean ``advanced`` at each step).
    """
    headings = []
    min_words = 0
    for rule in acceptance_rules:
        name, param = acceptance.parse_rule(rule)
        if name == "has_sections" and param:
            headings.extend(s.strip() for s in param.split(",") if s.strip())
        elif name == "min_words" and param:
            try:
                min_words = max(min_words, int(param.strip()))
            except ValueError:
                pass
    lines = ["# Deliverable", ""]
    for h in headings:
        lines.append(f"## {h}")
        lines.append("Section content generated for the stage-walk E2E fixture.")
        lines.append("")
    body = " ".join(["content"] * (min_words + 20))
    lines.append(body)
    return "\n".join(lines) + "\n"


def _seed_required_artifacts(s3, prefix, stage):
    """Seed the stage's required ``produces`` artifacts into the fake S3.

    Content is synthesized per artifact to satisfy that artifact's ``acceptance``
    rules; keys use the same ``{prefix}/{path}`` shape the handler HeadObjects.
    """
    contract = reg.stage_contract(stage) or {}
    for art in contract.get("produces", []):
        if not art.get("required"):
            continue
        key = f"{prefix}/{art['path']}"
        s3.seed(BUCKET, key, _compliant_content(art.get("acceptance", [])).encode("utf-8"))


def _complete(uuid, *, groups, actor):
    resp = project_api.handler(
        _event("POST", "/projects/{projectId}/complete-stage", project_id=uuid,
               body={}, groups=groups, actor=actor),
        None,
    )
    return resp["statusCode"], _body(resp)


def _walk_segment(s3, uuid, prefix, *, groups, actor, steps):
    """Complete each stage in ``steps`` and assert the resulting cursor + state.

    ``steps`` is a list of ``(expected_current_stage_after, expected_state_after)``
    tuples in completion order. Before completing each stage the current stage's
    required artifacts are seeded so the missing-required hard block never fires
    for a well-formed submission.
    """
    for expected_stage, expected_state in steps:
        current_stage = s3.ddb.get_record(uuid)["current_stage"]
        _seed_required_artifacts(s3, prefix, current_stage)
        status, out = _complete(uuid, groups=groups, actor=actor)
        assert status == 200, out
        assert out["status"] == "advanced", (current_stage, out)
        assert out["warnings"] == [], (current_stage, out["warnings"])
        assert out["current_stage"] == expected_stage, (current_stage, out)
        assert out["handoff_state"] == expected_state, (current_stage, out)
        rec = s3.ddb.get_record(uuid)
        assert rec["handoff_state"] == expected_state
        assert rec["owning_role"] == reg.owning_role(expected_state)


# ---------------------------------------------------------------------------
# SA segment: assigned_to_sa -> in_design -> design_ready -> ready_for_dev
# ---------------------------------------------------------------------------


def test_sa_segment_stage_walk_reaches_ready_for_dev(s3):
    uuid = "sa-walk-uuid-001"
    created = _seed_segment(s3, uuid, "SA Walk",
                            state="assigned_to_sa", stage="workflow_planning")
    prefix = created["artifact_prefix"]

    _walk_segment(
        s3, uuid, prefix, groups=[SA_GROUP], actor=SA_USER,
        steps=[
            ("nfr_requirements", "in_design"),      # workflow_planning advances_state
            ("nfr_design", "in_design"),            # nfr_requirements cursor-only
            ("application_design", "in_design"),    # nfr_design cursor-only
            ("units_generation", "in_design"),      # application_design cursor-only
            ("inception_complete", "design_ready"), # units_generation advances_state
            ("inception_complete", "ready_for_dev"),# inception_complete advances_state, no next stage
        ],
    )

    final = s3.ddb.get_record(uuid)
    # Segment-terminal owned state; still SA-owned. The cross-persona handoff to
    # assigned_to_dev is an explicit handoff_project action (design Section 3.11),
    # NOT performed by complete-stage.
    assert final["handoff_state"] == "ready_for_dev"
    assert final["owning_role"] == SA_GROUP
    assert reg.forward("ready_for_dev") == "assigned_to_dev"

    # The stage-complete audit trail grew one HIST item per completion (6).
    hist = s3.ddb.history_items(uuid)
    stage_completes = [h for h in hist if h.get("kind") == "stage-complete"]
    assert len(stage_completes) == 6


# ---------------------------------------------------------------------------
# QA segment: assigned_to_qa -> in_qa -> qa_passed
# ---------------------------------------------------------------------------


def test_qa_segment_stage_walk_reaches_qa_passed(s3):
    uuid = "qa-walk-uuid-001"
    created = _seed_segment(s3, uuid, "QA Walk",
                            state="assigned_to_qa", stage="test_planning")
    prefix = created["artifact_prefix"]

    _walk_segment(
        s3, uuid, prefix, groups=[QA_GROUP], actor=QA_USER,
        steps=[
            ("test_execution", "in_qa"),   # test_planning advances_state
            ("qa_report", "in_qa"),        # test_execution cursor-only
            ("qa_report", "qa_passed"),    # qa_report advances_state, no next stage
        ],
    )

    final = s3.ddb.get_record(uuid)
    assert final["handoff_state"] == "qa_passed"
    assert final["owning_role"] == QA_GROUP
    # Handoff to DevOps is explicit.
    assert reg.forward("qa_passed") == "assigned_to_devops"


# ---------------------------------------------------------------------------
# DevOps segment: assigned_to_devops -> in_deployment -> deployed
# ---------------------------------------------------------------------------


def test_devops_segment_stage_walk_reaches_deployed(s3):
    uuid = "devops-walk-uuid-001"
    created = _seed_segment(s3, uuid, "DevOps Walk",
                            state="assigned_to_devops", stage="deployment_planning")
    prefix = created["artifact_prefix"]

    _walk_segment(
        s3, uuid, prefix, groups=[OPS_GROUP], actor=OPS_USER,
        steps=[
            ("release", "in_deployment"),  # deployment_planning advances_state
            ("release", "deployed"),       # release advances_state, no next stage (terminal)
        ],
    )

    final = s3.ddb.get_record(uuid)
    assert final["handoff_state"] == "deployed"
    assert final["owning_role"] == OPS_GROUP
    assert reg.forward("deployed") is None  # terminal


# ---------------------------------------------------------------------------
# Dev boundary: construction_handoff produces the brief but does NOT advance;
# the Dev -> QA boundary stays the Git round-trip.
# ---------------------------------------------------------------------------


def test_construction_handoff_does_not_bypass_git_round_trip(s3):
    uuid = "dev-walk-uuid-001"
    created = _seed_segment(s3, uuid, "Dev Walk",
                            state="assigned_to_dev", stage="construction_handoff")
    prefix = created["artifact_prefix"]

    # Complete construction_handoff with a well-formed brief. It has no
    # advances_state and is the only Dev stage (no next stage), so the coarse
    # state must stay assigned_to_dev — the Git round-trip is NOT bypassed.
    _seed_required_artifacts(s3, prefix, "construction_handoff")
    status, out = _complete(uuid, groups=[DEV_GROUP], actor=DEV_USER)
    assert status == 200, out
    assert out["status"] == "advanced", out
    assert out["handoff_state"] == "assigned_to_dev", out
    assert out["current_stage"] == "construction_handoff", out  # no next stage

    # The Dev -> QA boundary is the Git round-trip (retained, no bypass):
    # submit is blocked until a repo is linked.
    blocked = project_api.handler(
        _event("POST", "/projects/{projectId}/transition", project_id=uuid,
               body={"action": "submit"}, groups=[DEV_GROUP], actor=DEV_USER),
        None,
    )
    assert blocked["statusCode"] == 422, _body(blocked)
    assert _body(blocked)["code"] == "REPO_NOT_LINKED"

    # Link the repo, submit, then the ingest callback advances to QA.
    linked = project_api.handler(
        _event("POST", "/projects/{projectId}/link-repo", project_id=uuid,
               body={"repo_url": REPO_URL}, groups=[DEV_GROUP], actor=DEV_USER),
        None,
    )
    assert linked["statusCode"] == 200, _body(linked)

    submitted = project_api.handler(
        _event("POST", "/projects/{projectId}/transition", project_id=uuid,
               body={"action": "submit"}, groups=[DEV_GROUP], actor=DEV_USER),
        None,
    )
    assert submitted["statusCode"] == 200, _body(submitted)
    assert _body(submitted)["handoff_state"] == "implementation_submitted"

    ingest = project_api.handler(
        _event("POST", "/projects/{projectId}/ingest-callback", project_id=uuid,
               body={"status": "ready", "commit": "cafe123"}),
        None,
    )
    assert ingest["statusCode"] == 200, _body(ingest)
    rec = _body(ingest)["record"]
    assert rec["handoff_state"] == "assigned_to_qa"
    assert rec["owning_role"] == QA_GROUP
