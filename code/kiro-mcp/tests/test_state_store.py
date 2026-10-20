from aidlc_mcp.state_store import StateStore


def test_record_seen_and_review(tmp_path):
    s = StateStore(str(tmp_path))
    s.record_seen("p1", "assigned_to_sa", "system-architect")
    data = s.read("p1")
    assert data["handoff_state"] == "assigned_to_sa"
    assert "artifacts_reviewed_at" not in data
    s.record_review("p1")
    assert "artifacts_reviewed_at" in s.read("p1")


def test_state_change_invalidates_review(tmp_path):
    s = StateStore(str(tmp_path))
    s.record_seen("p1", "assigned_to_sa", "system-architect")
    s.record_review("p1")
    assert "artifacts_reviewed_at" in s.read("p1")
    # project moved on -> review marker cleared
    s.record_seen("p1", "in_design", "system-architect")
    assert "artifacts_reviewed_at" not in s.read("p1")


def test_unknown_project_returns_empty(tmp_path):
    assert StateStore(str(tmp_path)).read("nope") == {}


def test_no_secrets_stored(tmp_path):
    s = StateStore(str(tmp_path))
    s.record_seen("p1", "draft", "requirements-analyst")
    raw = (tmp_path / "projects" / "p1.json").read_text()
    assert "token" not in raw.lower() and "password" not in raw.lower()
