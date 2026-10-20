"""Task 9.5 — end-to-end scripted lifecycle through the tool layer.

Mirrors verify-two-persona-handoff.sh in spirit: drives PlatformTools for each
persona against one shared, registry-faithful fake Project API, asserting the
forward chain, the request-changes bounce + feedback round-trip, and the
developer submit — all the way to `deployed`.
"""
from aidlc_mcp.tools import PlatformTools
from aidlc_mcp.s3_artifacts import S3ArtifactReader
from aidlc_mcp.state_store import StateStore
from aidlc_mcp.errors import ProjectApiError
from conftest import FakeS3
from test_tools import FakeApi, FakeCreds, REG


def make(api, groups, tmp_path, s3objs=None):
    reader = S3ArtifactReader("bucket", "us-east-1", s3_client=FakeS3(s3objs or {}))
    return PlatformTools(api, reader, FakeCreds(groups), StateStore(str(tmp_path)), REG,
                         raw_put=lambda u, d: 200, uuid_factory=lambda: "e2e-uuid")


def test_full_lifecycle_with_bounce(tmp_path):
    api = FakeApi([])
    ba = make(api, ["requirements-analyst"], tmp_path / "ba")
    sa = make(api, ["system-architect"], tmp_path / "sa")
    dev = make(api, ["software-developer"], tmp_path / "dev")
    qa = make(api, ["qa-engineer"], tmp_path / "qa")
    devops = make(api, ["devops-engineer"], tmp_path / "devops")

    # BA creates and hands off to SA (draft -> requirements_ready -> assigned_to_sa)
    created = ba.create_project("Widget", "a widget")
    uuid = created["project"]["project_uuid"]
    assert created["project"]["handoff_state"] == "draft"
    h = ba.handoff_project(uuid)
    assert h["handed_off"] is True and h["steps"] == 2
    assert api.get_project(uuid)["handoff_state"] == "assigned_to_sa"

    # SA returns for changes with comments (never blocked); BA sees the feedback
    r = sa.return_for_changes(uuid, "User stories don't cover the audit requirement")
    assert r["project"]["handoff_state"] == "requirements_ready"
    gp = ba.get_project(uuid)
    assert gp["latest_change_request"]["requester_role"] == "system-architect"
    assert gp["latest_change_request"]["note"] == "User stories don't cover the audit requirement"

    # BA re-hands off; SA designs through to the developer. In the Kiro-native
    # architecture the SA
    # walks its owned states via complete_stage; the single explicit
    # handoff_project transfers ownership across every SA-owned state at once
    # (assigned_to_sa -> in_design -> design_ready -> ready_for_dev -> assigned_to_dev).
    ba.handoff_project(uuid)
    assert api.get_project(uuid)["handoff_state"] == "assigned_to_sa"
    hd = sa.handoff_project(uuid)
    assert hd["project"]["handoff_state"] == "assigned_to_dev"

    # Developer links repo and submits -> implementation_submitted
    dev.link_repo(uuid, "https://github.com/acme/widget", git_token="ghp_secret")  # nosec B106 test fixture
    sub = dev.submit_implementation(uuid)
    assert sub["project"]["handoff_state"] == "implementation_submitted"

    # Server-side ingest advances to QA (simulated system callback)
    api.transition(uuid, action="advance")
    assert api.get_project(uuid)["handoff_state"] == "assigned_to_qa"

    # QA runs tests and hands off to DevOps
    qa.run_tests(uuid)
    hq = qa.handoff_project(uuid)  # assigned_to_qa -> in_qa -> qa_passed -> assigned_to_devops
    assert hq["project"]["handoff_state"] == "assigned_to_devops"

    # DevOps releases through to deployed (terminal)
    devops.handoff_project(uuid)
    assert api.get_project(uuid)["handoff_state"] == "deployed"


def test_qa_to_dev_bounce_never_blocked(tmp_path):
    api = FakeApi([])
    # seed a project already in_qa (owned by QA)
    api.p["p1"] = {"project_uuid": "p1", "name": "p1", "handoff_state": "in_qa",
                   "owning_role": REG.owning_role("in_qa"), "artifact_prefix": "p1",
                   "assignment_history": []}
    qa = make(api, ["qa-engineer"], tmp_path / "qa")
    out = qa.return_for_changes("p1", "Fix the failing integration tests")
    assert out["project"]["handoff_state"] == "assigned_to_dev"
    # Developer sees the feedback
    dev = make(api, ["software-developer"], tmp_path / "dev")
    gp = dev.get_project("p1")
    assert gp["latest_change_request"]["requester_role"] == "qa-engineer"
    assert "integration tests" in gp["latest_change_request"]["note"]


# ===========================================================================
# Task 15 — MCP-layer end-to-end pilot: BA `requirements_analysis`
#
# Drives the Kiro-native generation flow through the PlatformTools tool layer
# (get_stage_brief -> submit_artifact -> complete_stage) against a
# registry-faithful fake Project API whose cursor advancement is driven by the
# workflow registry (REG), not canned. Kiro's generation is faked by a fixture
# doc; the presigned PUT is faked by recording the upload against the minted
# key so complete_stage can enforce the missing-required precondition for real.
# ===========================================================================


class FaithfulBackend:
    """A registry-faithful, stateful fake Project API for the Kiro-native flow.

    Unlike the canned FakeApi in test_tools, this models the stage cursor, an
    artifact store, and drives cursor advancement + the missing-required block
    from the registry (REG) — so the E2E asserts *real* registry-driven
    advancement rather than a hard-coded next stage. It is still a FAKE (no
    Lambda import); it mirrors the backend's observable contract.
    """

    def __init__(self):
        self.p = {}
        self.artifacts = {}        # minted key -> bytes (the faked S3 upload)
        self._uploads = {}         # upload_url -> minted key
        self.calls = []

    # -- registry helpers (read the loaded SSOT mirror directly) ----------
    @staticmethod
    def _segment_of(stage):
        for seg, spec in REG._data["segments"].items():
            if stage in spec["stages"]:
                return seg, spec
        return None, None

    @staticmethod
    def _contract(stage):
        return REG._data.get("stage_contracts", {}).get(stage)

    @classmethod
    def _next_stage(cls, stage):
        _seg, spec = cls._segment_of(stage)
        stages = spec["stages"]
        i = stages.index(stage)
        return stages[i + 1] if i + 1 < len(stages) else None

    # -- basic record ops -------------------------------------------------
    def create_project(self, name, description="", type_="development", project_uuid=None):
        ba_stages = REG._data["segments"]["ba_segment"]["stages"]
        rec = {
            "project_uuid": project_uuid,
            "project_id": name.lower().replace(" ", "_"),
            "name": name,
            "handoff_state": REG.initial_state(),
            "owning_role": REG.owning_role(REG.initial_state()),
            "artifact_prefix": project_uuid,      # slug == uuid here (fake)
            "current_stage": ba_stages[0],
            "assignment_history": [],
        }
        self.p[project_uuid] = rec
        return dict(rec)

    def get_project(self, uuid):
        return dict(self.p[uuid])

    def list_projects(self):
        return [dict(r) for r in self.p.values()]

    # -- Kiro-native generation flow -------------------------------------
    def stage_brief(self, uuid):
        rec = self.p[uuid]
        stage = rec["current_stage"]
        seg, _spec = self._segment_of(stage)
        contract = self._contract(stage) or {}
        prefix = rec["artifact_prefix"]

        def _sub(path):
            return f"{prefix}/{path}"

        produces = [
            {"path": _sub(pr["path"]), "required": pr.get("required", False),
             "acceptance": list(pr.get("acceptance", []))}
            for pr in contract.get("produces", [])
        ]
        consumes = [{"path": _sub(c)} for c in contract.get("consumes", [])]
        return {
            "stage": stage,
            "segment": seg,
            "owning_role": rec["owning_role"],
            "generator": contract.get("generator", "kiro"),
            "skill": contract.get("skill"),
            "consumes": consumes,
            "produces": produces,
            "approval_required": True,
        }

    def stage_artifact_url(self, uuid, path):
        self.calls.append({"uuid": uuid, "action": "stage-artifact-url", "path": path})
        rec = self.p[uuid]
        prefix = rec["artifact_prefix"]
        contract = self._contract(rec["current_stage"]) or {}
        declared = {f"{prefix}/{pr['path']}" for pr in contract.get("produces", [])}
        # accept both registry-relative and slug-prefixed forms
        key = path if path.startswith(f"{prefix}/") else f"{prefix}/{path.lstrip('/')}"
        if key not in declared:
            raise ProjectApiError(400, f"off-contract path: {path}", "INVALID_REQUEST")
        upload_url = f"https://s3.example/put/{key}"
        self._uploads[upload_url] = key
        return {"upload_url": upload_url, "key": key, "expires_in": 300}

    def raw_put(self, url, data):
        """Faked presigned PUT: record the upload against the minted key so
        complete_stage can enforce the missing-required precondition for real."""
        key = self._uploads.get(url)
        if key is None:
            return 404
        self.artifacts[key] = data
        return 200

    def complete_stage(self, uuid, note=None):
        self.calls.append({"uuid": uuid, "action": "complete-stage", "note": note})
        rec = self.p[uuid]
        stage = rec["current_stage"]
        prefix = rec["artifact_prefix"]
        contract = self._contract(stage) or {}

        # Hard precondition: every required produces artifact must exist + be
        # non-empty (missing/empty blocks — Req 7.4/8.1). Not weakened.
        missing = []
        for pr in contract.get("produces", []):
            if not pr.get("required"):
                continue
            key = f"{prefix}/{pr['path']}"
            blob = self.artifacts.get(key)
            if not blob:
                missing.append(pr["path"])
        if missing:
            return {"status": "blocked", "missing": missing, "warnings": [],
                    "current_stage": stage, "handoff_state": rec["handoff_state"]}

        # Advance the cursor via the registry; apply advances_state if declared.
        nxt = self._next_stage(stage)
        if nxt:
            rec["current_stage"] = nxt
        adv = contract.get("advances_state")
        if adv:
            rec["handoff_state"] = adv
            rec["owning_role"] = REG.owning_role(adv)
        return {"status": "advanced", "warnings": [],
                "current_stage": rec["current_stage"],
                "handoff_state": rec["handoff_state"]}


def _good_requirements_doc():
    """A fixture requirements.md satisfying the requirements_analysis acceptance
    (nonempty, min_words:200, has_sections Functional/Non-Functional/Assumptions).
    Stands in for Kiro's generation."""
    body = " ".join(["requirement"] * 210)
    return (
        "# Requirements\n\n"
        f"## Functional Requirements\n\n{body}\n\n"
        f"## Non-Functional Requirements\n\n{body}\n\n"
        f"## Assumptions\n\n{body}\n"
    )


def _ba_tools(api, tmp_path):
    reader = S3ArtifactReader("bucket", "us-east-1", s3_client=FakeS3({}))
    return PlatformTools(api, reader, FakeCreds(["requirements-analyst"]),
                         StateStore(str(tmp_path)), REG,
                         raw_put=api.raw_put, uuid_factory=lambda: "e2e-ra-uuid")


def test_e2e_ba_requirements_analysis_through_tool_layer(tmp_path):
    """Full BA `requirements_analysis` pilot through PlatformTools against a
    registry-faithful backend: create -> (cursor walk) -> get_stage_brief ->
    submit_artifact (presigned PUT faked) -> complete_stage -> cursor advances
    to `user_stories`; coarse handoff_state stays `draft` (intra-segment)."""
    api = FaithfulBackend()
    ba = _ba_tools(api, tmp_path)

    # 1. BA creates the project (cursor seeded to the first BA stage).
    created = ba.create_project("Widget", "a widget")
    uuid = created["project"]["project_uuid"]
    assert api.get_project(uuid)["current_stage"] == "workspace_detection"

    # 2. Walk the cursor to requirements_analysis via real complete_stage over
    #    the two no-contract BA stages (registry-driven).
    assert ba.complete_stage(uuid)["current_stage"] == "reverse_engineering"
    assert ba.complete_stage(uuid)["current_stage"] == "requirements_analysis"

    # 3. get_stage_brief returns the Kiro contract for requirements_analysis.
    brief = ba.get_stage_brief(uuid)["stage_brief"]
    assert brief["stage"] == "requirements_analysis"
    assert brief["generator"] == "kiro"
    assert brief["skill"] == "requirements-analysis"
    produced = brief["produces"][0]
    assert produced["required"] is True
    produces_path = produced["path"]  # slug(uuid)-prefixed
    assert produces_path == f"{uuid}/aidlc-docs/inception/requirements/requirements.md"
    assert brief["consumes"][0]["path"].endswith(
        "aidlc-docs/inception/requirements/uploaded")

    # Sanity: completing now (nothing submitted) is BLOCKED (missing required).
    blocked = ba.complete_stage(uuid)
    assert blocked["status"] == "blocked"
    assert api.get_project(uuid)["current_stage"] == "requirements_analysis"

    # 4. Kiro generates the doc (fixture) and submits it via the tool layer;
    #    submit_artifact obtains the presign then PUTs (faked into api.artifacts).
    out = ba.submit_artifact(uuid, produces_path, _good_requirements_doc())
    assert out["key"] == produces_path
    assert api.artifacts.get(produces_path)  # the upload landed at the minted key

    # 5. complete_stage now advances the cursor to user_stories; coarse state
    #    stays draft (requirements_analysis declares no advances_state).
    done = ba.complete_stage(uuid, note="looks complete")
    assert done["status"] == "advanced"
    assert done["warnings"] == []
    assert done["current_stage"] == "user_stories"
    assert done["handoff_state"] == "draft"
    assert api.get_project(uuid)["current_stage"] == "user_stories"
    assert api.get_project(uuid)["handoff_state"] == "draft"
