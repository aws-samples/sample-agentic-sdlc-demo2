import json
import pytest
from aidlc_mcp.project_api import ProjectApiClient
from aidlc_mcp.errors import ProjectApiError
from conftest import FakeTransport


class StubCreds:
    def get_id_token(self): return "ID.TOKEN.SIG"
    def get_claims(self): return {}
    def get_groups(self): return ["system-architect"]


def client(responses):
    t = FakeTransport(responses)
    return ProjectApiClient("https://api.example.com/prod/", StubCreds(), transport=t), t


def test_list_projects_returns_array_and_sets_bearer():
    c, t = client([(200, {"projects": [{"project_uuid": "p1"}]})])
    assert c.list_projects() == [{"project_uuid": "p1"}]
    method, url, headers, body = t.calls[0]
    assert method == "GET" and url.endswith("/projects")
    assert headers["Authorization"] == "Bearer ID.TOKEN.SIG"


def test_get_project_encodes_uuid():
    c, t = client([(200, {"project_uuid": "a b"})])
    c.get_project("a b")
    assert t.calls[0][1].endswith("/projects/a%20b")


def test_transition_sends_action_and_note():
    c, t = client([(200, {"handoff_state": "in_design"})])
    c.transition("p1", action="approve", note="looks good")
    body = json.loads(t.calls[0][3])
    assert body == {"action": "approve", "note": "looks good"}


def test_error_maps_status_and_code():
    c, _ = client([(422, {"error": "Illegal transition: x -> y", "code": "ILLEGAL_TRANSITION"})])
    with pytest.raises(ProjectApiError) as ei:
        c.transition("p1", action="approve")
    assert ei.value.status == 422
    assert ei.value.code == "ILLEGAL_TRANSITION"


def test_403_forbidden_maps():
    c, _ = client([(403, {"error": "not owner", "code": "FORBIDDEN"})])
    with pytest.raises(ProjectApiError) as ei:
        c.delete_project("p1")
    assert ei.value.code == "FORBIDDEN"


def test_delete_204_returns_none():
    c, _ = client([(204, None)])
    assert c.delete_project("p1") is None


def test_link_repo_omits_optional_fields():
    c, t = client([(200, {"repo_url": "https://github.com/o/r"})])
    c.link_repo("p1", "https://github.com/o/r")
    assert json.loads(t.calls[0][3]) == {"repo_url": "https://github.com/o/r"}
