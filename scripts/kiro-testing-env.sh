#!/usr/bin/env bash
# Agentic SDLC - testing environment for a Kiro window.
#
# Usage (from the repo root):
#     source scripts/kiro-testing-env.sh
# Then launch a NEW Kiro window FROM THIS SAME SHELL so the MCP server inherits
# these variables:
#     kiro -n "$PWD"
#
# It pulls all config + the six persona test-user credentials live from the
# CloudFormation stack outputs, so per-deploy values stay current. Nothing
# sensitive is hard-coded in this file.

# Uses whatever AWS_PROFILE is already set in your environment; if unset, the
# default AWS credential chain is used. If you use named profiles, run
# `export AWS_PROFILE=<your-profile>` before sourcing this script.
export AWS_REGION="${AWS_REGION:-us-east-1}"
export CDK_DEFAULT_REGION="$AWS_REGION"
export AIDLC_AWS_REGION="$AWS_REGION"   # required by the MCP server config

REPO_ROOT="${AIDLC_REPO_ROOT:-$PWD}"
if [ ! -d "$REPO_ROOT/code/kiro-mcp" ]; then
  echo "ERROR: run this from the repo root (no code/kiro-mcp under $REPO_ROOT)." >&2
  echo "       cd into the repo first, or export AIDLC_REPO_ROOT=/path/to/AST." >&2
  return 1 2>/dev/null || exit 1
fi

export AIDLC_MCP_HOME="$REPO_ROOT/code/kiro-mcp"
export AIDLC_MCP_PYTHON="$AIDLC_MCP_HOME/.venv/bin/python"
export AIDLC_AUTH_MODE="password"
export AIDLC_STATE_DIR="${AIDLC_STATE_DIR:-$REPO_ROOT/.kiro/.aidlc-state}"
mkdir -p "$AIDLC_STATE_DIR"

if [ ! -x "$AIDLC_MCP_PYTHON" ]; then
  echo "WARNING: MCP venv missing at $AIDLC_MCP_PYTHON" >&2
  echo "         Install:  (cd \"$AIDLC_MCP_HOME\" && python3 -m venv .venv && .venv/bin/python -m pip install -e .)" >&2
fi

echo "Fetching stack outputs from AIDLCPlatformWorkshop ($AWS_PROFILE / $AWS_REGION)..."
_AIDLC_OUT="$(aws cloudformation describe-stacks --stack-name AIDLCPlatformWorkshop \
  --query "Stacks[0].Outputs" --output json --no-cli-pager 2>/dev/null)"
if [ -z "$_AIDLC_OUT" ] || [ "$_AIDLC_OUT" = "null" ]; then
  echo "ERROR: could not read stack outputs. Refresh your AWS credentials and check AWS_PROFILE=$AWS_PROFILE." >&2
  return 1 2>/dev/null || exit 1
fi

eval "$(AIDLC_OUT="$_AIDLC_OUT" python3 - <<'PY'
import json, os
o = {d["OutputKey"]: d["OutputValue"] for d in json.loads(os.environ["AIDLC_OUT"])}
def em(var, key):
    if key in o:
        print(f'export {var}={json.dumps(o[key])}')
em("AIDLC_PROJECT_API_URL", "KiroProjectApiUrl")
em("AIDLC_COGNITO_USER_POOL_ID", "KiroCognitoUserPoolId")
em("AIDLC_COGNITO_CLIENT_ID", "KiroCognitoClientId")
em("AIDLC_FILES_BUCKET", "KiroFilesBucket")
em("AIDLC_BA_USERNAME", "BATestUserEmail");        em("AIDLC_BA_PASSWORD", "BATestUserPassword")
em("AIDLC_SA_USERNAME", "SATestUserEmail");        em("AIDLC_SA_PASSWORD", "SATestUserPassword")
em("AIDLC_DEV_USERNAME", "TestUserEmail");         em("AIDLC_DEV_PASSWORD", "TestUserPassword")
em("AIDLC_QA_USERNAME", "QATestUserEmail");        em("AIDLC_QA_PASSWORD", "QATestUserPassword")
em("AIDLC_DEVOPS_USERNAME", "DevOpsTestUserEmail");em("AIDLC_DEVOPS_PASSWORD", "DevOpsTestUserPassword")
em("AIDLC_MAINT_USERNAME", "MaintTestUserEmail");  em("AIDLC_MAINT_PASSWORD", "MaintTestUserPassword")
PY
)"

echo ""
echo "Environment ready:"
echo "  API      = $AIDLC_PROJECT_API_URL"
echo "  POOL     = $AIDLC_COGNITO_USER_POOL_ID   CLIENT = $AIDLC_COGNITO_CLIENT_ID"
echo "  BUCKET   = $AIDLC_FILES_BUCKET"
echo "  personas = BA:$AIDLC_BA_USERNAME  SA:$AIDLC_SA_USERNAME  DEV:$AIDLC_DEV_USERNAME  QA:$AIDLC_QA_USERNAME  DEVOPS:$AIDLC_DEVOPS_USERNAME  MAINT:$AIDLC_MAINT_USERNAME"
echo "  (passwords set in env but not printed)"
echo ""
echo "Now launch a NEW Kiro window on this repo FROM THIS SHELL:"
echo "  kiro -n \"$REPO_ROOT\""
