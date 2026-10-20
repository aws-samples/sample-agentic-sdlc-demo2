#!/usr/bin/env bash
# Provision an external folder as a fully-working Kiro test workspace for the
# AIDLC personas - no env exports and no "Mcp Approved Env Vars" needed.
#
# Usage:  bash scripts/setup-test-workspace.sh /path/to/workspace [default-persona]
#           default-persona is one of: ba sa dev qa devops maint  (default: ba)
#
# Why this exists: Kiro does NOT expand ${VAR} in an MCP server's `command`
# field (only in the `env` block), so `command: ${AIDLC_MCP_PYTHON}` fails with
# "spawn ... ENOENT". This script writes the ABSOLUTE venv python into `command`
# and inlines the resolved config/credentials, for both:
#   - the six persona agents (.kiro/agents/*.md)  -> in-window persona switching
#   - a workspace .kiro/settings/mcp.json         -> default agent + panel visibility
#
# Config + the six persona test-user credentials are pulled live from the
# CloudFormation stack outputs. The generated files contain machine-specific
# absolute paths and test-user credentials, so they belong in a local test
# folder - do not commit them.
set -euo pipefail

AST_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:?usage: setup-test-workspace.sh /path/to/workspace [default-persona]}"
DEFAULT_PERSONA="${2:-ba}"

VENV_PY="$AST_ROOT/code/kiro-mcp/.venv/bin/python"
MCP_HOME="$AST_ROOT/code/kiro-mcp"
STATE_DIR="$AST_ROOT/.kiro/.aidlc-state"
AWS_PROFILE_VAL="${AWS_PROFILE:-agentic-sdlc-kiro}"
REGION="${AWS_REGION:-us-east-1}"

# --- preflight ------------------------------------------------------------
if [ ! -x "$VENV_PY" ]; then
  echo "ERROR: venv python missing at $VENV_PY" >&2
  echo "       Install:  (cd \"$MCP_HOME\" && python3 -m venv .venv && .venv/bin/python -m pip install -e .)" >&2
  exit 1
fi
if ! "$VENV_PY" -c "import aidlc_mcp" 2>/dev/null; then
  echo "ERROR: 'aidlc_mcp' not importable by the venv. Install it:" >&2
  echo "       (cd \"$MCP_HOME\" && .venv/bin/python -m pip install -e .)" >&2
  exit 1
fi
echo "Fetching stack outputs from AIDLCPlatformWorkshop ($AWS_PROFILE_VAL / $REGION)..."
OUT_JSON="$(AWS_PROFILE="$AWS_PROFILE_VAL" aws cloudformation describe-stacks \
  --stack-name AIDLCPlatformWorkshop --query "Stacks[0].Outputs" --output json --no-cli-pager 2>/dev/null)"
if [ -z "$OUT_JSON" ] || [ "$OUT_JSON" = "null" ]; then
  echo "ERROR: could not read stack outputs. Refresh creds ('mwinit') and check AWS_PROFILE=$AWS_PROFILE_VAL." >&2
  exit 1
fi
mkdir -p "$STATE_DIR" "$TARGET/.kiro"

# --- copy skills + steering verbatim (agents' resources resolve here) -----
for d in skills steering; do
  rm -rf "${TARGET:?}/.kiro/$d"
  cp -R "$AST_ROOT/.kiro/$d" "$TARGET/.kiro/$d"
  echo "copied .kiro/$d"
done

# --- hooks: copy with command rewritten to this repo's absolute scripts ---
mkdir -p "$TARGET/.kiro/hooks"
AST_ROOT="$AST_ROOT" TARGET="$TARGET" "$VENV_PY" - <<'PY'
import json, os, glob
ast = os.environ["AST_ROOT"]; target = os.environ["TARGET"]
for src in sorted(glob.glob(os.path.join(ast, ".kiro/hooks/*.json"))):
    data = json.load(open(src))
    for h in data.get("hooks", []):
        a = h.get("action", {})
        cmd = a.get("command")
        if cmd and "scripts/kiro-hooks/" in cmd:
            a["command"] = cmd.replace("scripts/kiro-hooks/", f"{ast}/scripts/kiro-hooks/")
    with open(os.path.join(target, ".kiro/hooks", os.path.basename(src)), "w") as f:
        json.dump(data, f, indent=2)
print("hooks rewritten to absolute paths")
PY

# --- agents: materialize (absolute command, inlined env) + mcp.json -------
mkdir -p "$TARGET/.kiro/agents" "$TARGET/.kiro/settings"
OUT_JSON="$OUT_JSON" VENV_PY="$VENV_PY" MCP_HOME="$MCP_HOME" STATE_DIR="$STATE_DIR" \
AWS_PROFILE_VAL="$AWS_PROFILE_VAL" REGION="$REGION" AST_ROOT="$AST_ROOT" TARGET="$TARGET" \
DEFAULT_PERSONA="$DEFAULT_PERSONA" "$VENV_PY" - <<'PY'
import json, os, glob, re
o = {d["OutputKey"]: d["OutputValue"] for d in json.loads(os.environ["OUT_JSON"])}
venv = os.environ["VENV_PY"]; home = os.environ["MCP_HOME"]; state = os.environ["STATE_DIR"]
prof = os.environ["AWS_PROFILE_VAL"]; region = os.environ["REGION"]
ast = os.environ["AST_ROOT"]; target = os.environ["TARGET"]

vals = {
    "AIDLC_MCP_PYTHON": venv, "AIDLC_MCP_HOME": home, "AIDLC_AWS_REGION": region,
    "AIDLC_PROJECT_API_URL": o["KiroProjectApiUrl"],
    "AIDLC_COGNITO_USER_POOL_ID": o["KiroCognitoUserPoolId"],
    "AIDLC_COGNITO_CLIENT_ID": o["KiroCognitoClientId"],
    "AIDLC_FILES_BUCKET": o["KiroFilesBucket"],
    "AIDLC_BA_USERNAME": o["BATestUserEmail"], "AIDLC_BA_PASSWORD": o["BATestUserPassword"],
    "AIDLC_SA_USERNAME": o["SATestUserEmail"], "AIDLC_SA_PASSWORD": o["SATestUserPassword"],
    "AIDLC_DEV_USERNAME": o["TestUserEmail"], "AIDLC_DEV_PASSWORD": o["TestUserPassword"],
    "AIDLC_QA_USERNAME": o["QATestUserEmail"], "AIDLC_QA_PASSWORD": o["QATestUserPassword"],
    "AIDLC_DEVOPS_USERNAME": o["DevOpsTestUserEmail"], "AIDLC_DEVOPS_PASSWORD": o["DevOpsTestUserPassword"],
    "AIDLC_MAINT_USERNAME": o["MaintTestUserEmail"], "AIDLC_MAINT_PASSWORD": o["MaintTestUserPassword"],
}

def subst(text):
    return re.sub(r'\$\{([A-Z_]+)\}',
                  lambda m: ('"%s"' % vals[m.group(1)]) if m.group(1) in vals else m.group(0),
                  text)

for src in sorted(glob.glob(os.path.join(ast, ".kiro/agents/*.md"))):
    t = subst(open(src).read())
    out = []
    for ln in t.splitlines():
        out.append(ln)
        if re.match(r'^\s+PYTHONPATH:', ln):
            indent = ln[:len(ln) - len(ln.lstrip())]
            out.append(f'{indent}AWS_PROFILE: "{prof}"')
            out.append(f'{indent}AIDLC_STATE_DIR: "{state}"')
    with open(os.path.join(target, ".kiro/agents", os.path.basename(src)), "w") as f:
        f.write("\n".join(out) + "\n")
print("materialized 6 persona agents (absolute command + inlined env)")

p = os.environ["DEFAULT_PERSONA"].upper()
mcp = {"mcpServers": {"aidlc-platform": {
    "command": venv, "args": ["-m", "aidlc_mcp"],
    "env": {
        "PYTHONPATH": home, "AIDLC_AUTH_MODE": "password",
        "AIDLC_PERSONA_USERNAME": vals[f"AIDLC_{p}_USERNAME"],
        "AIDLC_PERSONA_PASSWORD": vals[f"AIDLC_{p}_PASSWORD"],
        "AIDLC_PROJECT_API_URL": vals["AIDLC_PROJECT_API_URL"],
        "AIDLC_COGNITO_USER_POOL_ID": vals["AIDLC_COGNITO_USER_POOL_ID"],
        "AIDLC_COGNITO_CLIENT_ID": vals["AIDLC_COGNITO_CLIENT_ID"],
        "AIDLC_AWS_REGION": region, "AIDLC_FILES_BUCKET": vals["AIDLC_FILES_BUCKET"],
        "AIDLC_STATE_DIR": state, "AWS_PROFILE": prof,
    },
    "disabled": False,
    "autoApprove": ["list_projects", "get_project", "list_artifacts", "get_artifact", "get_stage_brief"],
}}}
with open(os.path.join(target, ".kiro/settings/mcp.json"), "w") as f:
    json.dump(mcp, f, indent=2)
print(f"generated .kiro/settings/mcp.json (default agent persona: {p})")
PY

echo ""
echo "Workspace ready: $TARGET"
echo "Open it in Kiro (no env exports needed), reload the window, and either:"
echo "  - ask the default agent to 'list projects', or"
echo "  - pick a persona agent (Business Analyst = cmd+alt+1) and go."
