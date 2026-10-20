"""Task 9.4 — config validation for agents, skills, and the Power.

Validates that the persona agents, skills, plugin.json, and mcp.json parse and
reference each other correctly, and that no literal secret is committed (only
${VAR} placeholders for credentials).
"""
import json
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
AGENTS = REPO / ".kiro" / "agents"
SKILLS = REPO / ".kiro" / "skills"
POWER = REPO / "powers" / "aidlc-platform"

PERSONAS = ["business-analyst", "solution-architect", "software-developer",
            "qa-engineer", "devops-engineer", "maintenance-specialist"]
SKILL_PERSONAS = PERSONAS[:-1]  # maintenance has no skill


def _frontmatter(path):
    text = path.read_text()
    assert text.startswith("---\n"), f"{path} missing frontmatter"
    _, fm, _ = text.split("---\n", 2)
    return yaml.safe_load(fm)


@pytest.mark.parametrize("persona", PERSONAS)
def test_agent_is_valid(persona):
    fm = _frontmatter(AGENTS / f"{persona}.md")
    assert fm["name"] == persona
    assert fm["includeMcpJson"] is False
    srv = fm["mcpServers"]["aidlc-platform"]
    assert srv["command"] == "${AIDLC_MCP_PYTHON}"
    assert srv["args"] == ["-m", "aidlc_mcp"]
    env = srv["env"]
    # Credentials must be placeholders, never literals.
    assert env["AIDLC_PERSONA_USERNAME"].startswith("${")
    assert env["AIDLC_PERSONA_PASSWORD"].startswith("${")
    assert env["PYTHONPATH"] == "${AIDLC_MCP_HOME}"
    # Steering is referenced; skill referenced when the persona has one.
    res = fm.get("resources", [])
    assert any("steering" in r for r in res)
    if persona in SKILL_PERSONAS:
        assert f"skill://.kiro/skills/{persona}/SKILL.md" in res


@pytest.mark.parametrize("persona", PERSONAS)
def test_agent_writes_require_ask(persona):
    fm = _frontmatter(AGENTS / f"{persona}.md")
    rules = (fm.get("permissions") or {}).get("rules", [])
    if persona == "maintenance-specialist":
        assert rules == [] or all(r.get("effect") == "ask" for r in rules)
    else:
        # every state-changing tool listed is gated to ask
        assert any(r.get("effect") == "ask" for r in rules)


@pytest.mark.parametrize("persona", SKILL_PERSONAS)
def test_skill_is_valid(persona):
    fm = _frontmatter(SKILLS / persona / "SKILL.md")
    assert fm["name"] == persona
    assert fm.get("description")


def test_plugin_manifest_valid():
    m = json.loads((POWER / "plugin.json").read_text())
    assert m["name"] == "aidlc-platform"
    for key in ("version", "description", "keywords"):
        assert m.get(key)
    assert "author" in m


def test_power_mcp_json_only_placeholders():
    m = json.loads((POWER / "mcp.json").read_text())
    env = m["mcpServers"]["aidlc-platform"]["env"]
    for k, v in env.items():
        assert v.startswith("${") and v.endswith("}"), f"{k} is not a placeholder: {v}"


def test_power_skills_mirror_workspace_skills():
    power_skills = {p.name for p in (POWER / "skills").iterdir() if p.is_dir()}
    assert power_skills == set(SKILL_PERSONAS)


def test_no_literal_secret_in_agents_or_power():
    suspicious = ("password:", "ghp_", "glpat-", "AKIA")
    files = list(AGENTS.glob("*.md")) + [POWER / "mcp.json", POWER / "plugin.json"]
    for f in files:
        text = f.read_text()
        # allow the literal "AIDLC_AUTH_MODE: password" mode value (not a secret)
        text_wo_mode = text.replace("AIDLC_AUTH_MODE: password", "")
        for pat in suspicious:
            assert pat not in text_wo_mode, f"possible secret '{pat}' in {f}"
