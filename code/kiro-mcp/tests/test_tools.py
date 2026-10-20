import pytest
from aidlc_mcp.tools import PlatformTools, _display_key
from aidlc_mcp.registry import load_registry
from aidlc_mcp.state_store import StateStore
from aidlc_mcp.s3_artifacts import S3ArtifactReader
from aidlc_mcp.errors import ProjectApiError
from conftest import FakeS3

REG = load_registry()


class FakeCreds:
    def __init__(self, groups):
        self._g = groups
    def get_id_token(self): return "ID.TOK.SIG"
    def get_claims(self): return {"cognito:groups": self._g, "email": "persona@example.com"}
    def get_groups(self): return list(self._g)


class FakeApi:
    """Registry-faithful, stateful fake Project API."""
    def __init__(self, projects):
        # projects: {uuid: record dict}
        self.p = {r["project_uuid"]: dict(r) for r in projects}
        self.calls = []

    def list_projects(self):
        return [dict(r) for r in self.p.values()]

    def get_project(self, uuid):
        return dict(self.p[uuid])

    def create_project(self, name, description="", type_="development", project_uuid=None):
        rec = {"project_uuid": project_uuid, "project_id": name.lower().replace(" ", "_"),
               "name": name, "handoff_state": REG.initial_state(),
               "owning_role": REG.owning_role(REG.initial_state()),
               "artifact_prefix": name.lower().replace(" ", "_"), "assignment_history": []}
        self.p[project_uuid] = rec
        return dict(rec)

    def delete_project(self, uuid):
        self.p.pop(uuid, None); return None

    def transition(self, uuid, action=None, note=None, target_state=None):
        self.calls.append({"uuid": uuid, "action": action, "note": note})
        rec = self.p[uuid]
        cur = rec["handoff_state"]
        if action == "request-changes":
            rc = REG.request_changes(cur)
            if not rc:
                raise ProjectApiError(422, "illegal", "ILLEGAL_TRANSITION")
            target = rc["target"]
        else:  # any forward synonym
            target = REG.forward(cur)
            if not target:
                raise ProjectApiError(422, "illegal", "ILLEGAL_TRANSITION")
        rec["assignment_history"].append({"from": cur, "to": target, "actor": "tester", "note": note})
        rec["handoff_state"] = target
        rec["owning_role"] = REG.owning_role(target)
        return dict(rec)

    def link_repo(self, uuid, repo_url, branch=None, git_token=None):
        rec = self.p[uuid]; rec["repo_url"] = repo_url; rec["repo_branch"] = branch or "main"
        return dict(rec)

    def run_tests(self, uuid):
        rec = self.p[uuid]; rec["qa_test_status"] = "running"; return dict(rec)

    def upload_url(self, uuid, filename):
        return {"upload_url": "https://s3.example/put", "key": f"{uuid}/aidlc-docs/inception/requirements/uploaded/{filename}"}

    # -- Kiro-native generation flow -----------------------------------------
    def stage_brief(self, uuid):
        rec = self.p[uuid]
        return {
            "stage": rec.get("current_stage") or "requirements_analysis",
            "segment": "ba_segment",
            "owning_role": rec["owning_role"],
            "generator": "kiro",
            "skill": "requirements-analysis",
            "consumes": [{"path": "aidlc-docs/inception/requirements/uploaded/**"}],
            "produces": [{"path": "aidlc-docs/inception/requirements/requirements.md",
                          "required": True, "acceptance": ["min_words:300"]}],
            "approval_required": True,
        }

    def stage_artifact_url(self, uuid, path):
        self.calls.append({"uuid": uuid, "action": "stage-artifact-url", "path": path})
        return {"upload_url": "https://s3.example/put",
                "key": f"{uuid}/{path.lstrip('/')}", "expires_in": 300}

    def complete_stage(self, uuid, note=None):
        self.calls.append({"uuid": uuid, "action": "complete-stage", "note": note})
        rec = self.p[uuid]
        rec["current_stage"] = "user_stories"
        return {"status": "advanced", "warnings": [],
                "current_stage": "user_stories",
                "handoff_state": rec["handoff_state"]}


def _rec(uuid, state, **kw):
    d = {"project_uuid": uuid, "name": uuid, "handoff_state": state,
         "owning_role": REG.owning_role(state), "artifact_prefix": uuid,
         "assignment_history": []}
    d.update(kw); return d


def tools(api, groups, tmp_path, s3=None, raw_put=None):
    reader = s3 or S3ArtifactReader("bucket", "us-east-1", s3_client=FakeS3({}))
    return PlatformTools(api, reader, FakeCreds(groups),
                         StateStore(str(tmp_path)), REG,
                         raw_put=raw_put or (lambda u, d: 200),
                         uuid_factory=lambda: "fixed-uuid")


def test_list_projects_ownership(tmp_path):
    api = FakeApi([_rec("p1", "assigned_to_sa"), _rec("p2", "draft")])
    t = tools(api, ["system-architect"], tmp_path)
    out = t.list_projects()
    own = {p["project_uuid"]: p["ownership"] for p in out["projects"]}
    assert own["p1"] == "mine"           # SA owns assigned_to_sa
    assert own["p2"] in ("awaiting_me", "awaiting_other", "owned_by_other")
    assert "assigned to you" in out["summary"]


def test_get_project_surfaces_change_request(tmp_path):
    hist = [{"from": "assigned_to_sa", "to": "requirements_ready",
             "actor": "sa@x", "note": "Fix the data model"}]
    api = FakeApi([_rec("p1", "requirements_ready", assignment_history=hist)])
    t = tools(api, ["requirements-analyst"], tmp_path)
    out = t.get_project("p1")
    lcr = out["latest_change_request"]
    assert lcr["requester_role"] == "system-architect"
    assert lcr["note"] == "Fix the data model"
    assert "Returned by Solution Architect" in out["summary"]


def test_get_project_surfaces_current_stage(tmp_path):
    api = FakeApi([_rec("p1", "assigned_to_sa", current_stage="nfr_requirements")])
    t = tools(api, ["system-architect"], tmp_path)
    out = t.get_project("p1")
    assert out["project"]["current_stage"] == "nfr_requirements"
    assert "nfr_requirements" in out["summary"]


def test_get_stage_brief_returns_contract(tmp_path):
    api = FakeApi([_rec("p1", "draft", current_stage="requirements_analysis")])
    t = tools(api, ["requirements-analyst"], tmp_path)
    out = t.get_stage_brief("p1")
    brief = out["stage_brief"]
    assert brief["stage"] == "requirements_analysis"
    assert brief["generator"] == "kiro"
    # produces get a presentation-only display_path; stored path is preserved.
    prod = brief["produces"][0]
    assert prod["path"] == "aidlc-docs/inception/requirements/requirements.md"
    assert prod["display_path"] == "agentic-sdlc/inception/requirements/requirements.md"
    assert "requirements-analysis" in out["summary"]


def test_submit_artifact_puts_content_and_preserves_key(tmp_path):
    api = FakeApi([_rec("p1", "draft", current_stage="requirements_analysis")])
    puts = []
    t = tools(api, ["requirements-analyst"], tmp_path,
              raw_put=lambda u, d: puts.append((u, d)) or 200)
    path = "aidlc-docs/inception/requirements/requirements.md"
    out = t.submit_artifact("p1", path, "# Requirements\nlots of content")
    assert puts and puts[0][0] == "https://s3.example/put"
    assert puts[0][1] == b"# Requirements\nlots of content"
    assert out["key"] == f"p1/{path}"                       # stored key preserved
    assert out["display_path"] == "p1/agentic-sdlc/inception/requirements/requirements.md"
    assert api.calls[0]["action"] == "stage-artifact-url"
    assert api.calls[0]["path"] == path


def test_submit_artifact_raises_on_put_failure(tmp_path):
    api = FakeApi([_rec("p1", "draft", current_stage="requirements_analysis")])
    t = tools(api, ["requirements-analyst"], tmp_path, raw_put=lambda u, d: 500)
    with pytest.raises(ProjectApiError):
        t.submit_artifact("p1", "aidlc-docs/inception/requirements/requirements.md", "x")


def test_complete_stage_advances_cursor(tmp_path):
    api = FakeApi([_rec("p1", "draft", current_stage="requirements_analysis")])
    t = tools(api, ["requirements-analyst"], tmp_path)
    out = t.complete_stage("p1", note="looks good")
    assert api.calls[0]["action"] == "complete-stage"
    assert api.calls[0]["note"] == "looks good"
    assert out["status"] == "advanced"
    assert out["current_stage"] == "user_stories"
    assert "user_stories" in out["summary"]


def test_complete_stage_blocked_surfaces_missing(tmp_path):
    api = FakeApi([_rec("p1", "draft", current_stage="requirements_analysis")])

    def _blocked(uuid, note=None):
        api.calls.append({"uuid": uuid, "action": "complete-stage", "note": note})
        return {"status": "blocked",
                "missing": ["aidlc-docs/inception/requirements/requirements.md"],
                "warnings": [], "current_stage": "requirements_analysis",
                "handoff_state": "draft"}

    api.complete_stage = _blocked
    t = tools(api, ["requirements-analyst"], tmp_path)
    out = t.complete_stage("p1")
    assert out["status"] == "blocked"
    assert "requirements.md" in out["summary"]


def test_handoff_project_multistep_ba_to_sa(tmp_path):
    api = FakeApi([_rec("p1", "draft")])  # BA owns draft & requirements_ready
    t = tools(api, ["requirements-analyst"], tmp_path)
    out = t.handoff_project("p1", note="over to you")
    assert out["handed_off"] is True
    assert out["steps"] == 2                       # draft->requirements_ready->assigned_to_sa
    assert out["project"]["handoff_state"] == "assigned_to_sa"
    assert out["project"]["owning_role"] == "system-architect"
    assert api.calls[0]["note"] == "over to you"   # note only on first step
    assert api.calls[1]["note"] is None


def test_handoff_when_not_owner(tmp_path):
    api = FakeApi([_rec("p1", "assigned_to_sa")])   # SA owns it
    t = tools(api, ["requirements-analyst"], tmp_path)  # caller is BA
    out = t.handoff_project("p1")
    assert out["handed_off"] is False
    assert api.calls == []                          # no transition attempted


def test_return_for_changes_passes_note(tmp_path):
    api = FakeApi([_rec("p1", "assigned_to_sa")])
    t = tools(api, ["system-architect"], tmp_path)
    out = t.return_for_changes("p1", "Rework the NFRs")
    assert api.calls[0]["action"] == "request-changes"
    assert api.calls[0]["note"] == "Rework the NFRs"
    assert out["project"]["handoff_state"] == "requirements_ready"


def test_return_for_changes_requires_comments(tmp_path):
    api = FakeApi([_rec("p1", "assigned_to_sa")])
    t = tools(api, ["system-architect"], tmp_path)
    with pytest.raises(ValueError):
        t.return_for_changes("p1", "   ")


def test_link_repo_does_not_echo_token(tmp_path):
    api = FakeApi([_rec("p1", "assigned_to_dev")])
    t = tools(api, ["software-developer"], tmp_path)
    out = t.link_repo("p1", "https://github.com/o/r", git_token="ghp_secret")  # nosec B106 test fixture
    assert "ghp_secret" not in str(out)


def test_submit_uses_submit_action(tmp_path):
    api = FakeApi([_rec("p1", "assigned_to_dev", repo_url="https://github.com/o/r")])
    t = tools(api, ["software-developer"], tmp_path)
    out = t.submit_implementation("p1")
    assert api.calls[0]["action"] == "submit"
    assert out["project"]["handoff_state"] == "implementation_submitted"


def test_run_tests(tmp_path):
    api = FakeApi([_rec("p1", "assigned_to_qa")])
    t = tools(api, ["qa-engineer"], tmp_path)
    out = t.run_tests("p1")
    assert "results will appear" in out["summary"]


def test_upload_requirements_rejects_bad_ext(tmp_path):
    api = FakeApi([_rec("p1", "draft")])
    t = tools(api, ["requirements-analyst"], tmp_path)
    bad = tmp_path / "notes.exe"; bad.write_text("x")
    with pytest.raises(ValueError):
        t.upload_requirements("p1", str(bad))


def test_upload_requirements_happy(tmp_path):
    api = FakeApi([_rec("p1", "draft")])
    puts = []
    t = tools(api, ["requirements-analyst"], tmp_path,
              raw_put=lambda u, d: puts.append((u, d)) or 200)
    f = tmp_path / "reqs.md"; f.write_text("# reqs")
    out = t.upload_requirements("p1", str(f))
    assert out["filename"] == "reqs.md"
    assert puts and puts[0][1] == b"# reqs"


def test_artifacts_mark_review_and_preserve_key(tmp_path):
    api = FakeApi([_rec("p1", "assigned_to_sa")])
    reader = S3ArtifactReader("bucket", "us-east-1", s3_client=FakeS3({"p1/aidlc-docs/requirements.md": "hello"}))
    store = StateStore(str(tmp_path))
    t = PlatformTools(api, reader, FakeCreds(["system-architect"]), store, REG)
    lst = t.list_artifacts("p1")
    assert lst["artifacts"][0]["key"] == "p1/aidlc-docs/requirements.md"
    assert lst["artifacts"][0]["display_path"] == "p1/agentic-sdlc/requirements.md"
    got = t.get_artifact("p1", "aidlc-docs/requirements.md")
    assert got["key"] == "p1/aidlc-docs/requirements.md"   # stored key preserved
    assert "artifacts_reviewed_at" in store.read("p1")      # review marker set


def test_display_key_helper():
    assert _display_key("proj/aidlc-docs/x.md") == "proj/agentic-sdlc/x.md"
    assert _display_key(None) is None
