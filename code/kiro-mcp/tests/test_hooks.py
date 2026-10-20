"""Task 9.3 — enforcement hook script tests.

Run the actual hook scripts as subprocesses and assert their gating behavior,
including the invariant that request-changes is never in a blocking matcher.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HOOKS = REPO / "scripts" / "kiro-hooks"
HOOK_JSON = REPO / ".kiro" / "hooks"


def run(script, stdin_obj, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        ["bash", str(HOOKS / script)],
        input=json.dumps(stdin_obj).encode(),
        capture_output=True, env=env, timeout=60,
    )
    return p.returncode, p.stdout.decode(), p.stderr.decode()


def _write_marker(state_dir, uuid, data):
    d = Path(state_dir) / "projects"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{uuid}.json").write_text(json.dumps(data))


# -- require-review -----------------------------------------------------
def test_review_blocks_when_not_reviewed(tmp_path):
    _write_marker(tmp_path, "p1", {"handoff_state": "assigned_to_sa"})
    rc, _, err = run("require-review.sh",
                     {"tool_name": "advance_project", "tool_input": {"project_uuid": "p1"}},
                     {"AIDLC_STATE_DIR": str(tmp_path)})
    assert rc == 2
    assert "Review required" in err


def test_review_allows_after_review(tmp_path):
    _write_marker(tmp_path, "p1", {"handoff_state": "assigned_to_sa",
                                   "artifacts_reviewed_at": "2026-01-01T00:00:00Z"})
    rc, _, _ = run("require-review.sh",
                   {"tool_name": "advance_project", "tool_input": {"project_uuid": "p1"}},
                   {"AIDLC_STATE_DIR": str(tmp_path)})
    assert rc == 0


def test_review_allows_non_review_state(tmp_path):
    _write_marker(tmp_path, "p1", {"handoff_state": "draft"})
    rc, _, _ = run("require-review.sh",
                   {"tool_name": "advance_project", "tool_input": {"project_uuid": "p1"}},
                   {"AIDLC_STATE_DIR": str(tmp_path)})
    assert rc == 0


def test_review_allows_when_no_uuid(tmp_path):
    rc, _, _ = run("require-review.sh",
                   {"tool_name": "advance_project", "tool_input": {}},
                   {"AIDLC_STATE_DIR": str(tmp_path)})
    assert rc == 0


# -- require-tests ------------------------------------------------------
def test_tests_allow_on_pass():
    rc, _, _ = run("require-tests.sh", {}, {"AIDLC_TEST_COMMAND": "exit 0"})
    assert rc == 0


def test_tests_block_on_fail():
    rc, _, err = run("require-tests.sh", {}, {"AIDLC_TEST_COMMAND": "echo boom; exit 1"})
    assert rc == 2
    assert "blocked" in err.lower()


# -- audit --------------------------------------------------------------
def test_audit_logs_without_secret(tmp_path):
    log = tmp_path / "audit.log"
    rc, _, _ = run("audit.sh",
                   {"tool_name": "link_repo",
                    "tool_input": {"project_uuid": "p9", "git_token": "ghp_SECRET"}},
                   {"AIDLC_AUDIT_LOG": str(log), "AIDLC_PERSONA_USERNAME": "dev@example.com"})
    assert rc == 0
    content = log.read_text()
    assert "link_repo" in content and "p9" in content
    assert "ghp_SECRET" not in content       # no secret in audit log


# -- invariant: request-changes is never in a blocking matcher (Req 16.5) --
def test_return_for_changes_never_gated():
    for name in ("require-review.json", "require-tests.json"):
        assert "return_for_changes" not in (HOOK_JSON / name).read_text()
