# Kiro IDE frontend — Setup

This sets up the Platform MCP server so a persona can drive the Agentic SDLC platform from Kiro.
Additive and server-preserving: it calls the existing Project API + S3; no backend change.

## Deployment target (this repo)

This solution deploys to your **own** AWS account. Set the deploy account via the
git-ignored `cdk/.env`:

- `AIDLC_DEPLOY_ACCOUNT=<your-account-id>`
- optional `AWS_PROFILE=<your-aws-profile>` (if you use named profiles; otherwise
  the default AWS credential chain is used)
- `CDK_DEFAULT_REGION=us-east-1`

The tracked source keeps a placeholder account (`123456789012`,
`cdk/lib/utils/account-guard.ts`) guarded by the account-mismatch check, so no real
account id is committed. Deploy with:

```bash
set -a; source cdk/.env; set +a
yarn deploy-infrastructure
```

The account-mismatch guard passes because both the profile-derived account and
`AIDLC_DEPLOY_ACCOUNT` resolve to your account id.


## 1. Install the MCP server

```bash
cd code/kiro-mcp
python3 -m venv .venv
.venv/bin/python -m pip install -e .        # installs mcp (official SDK) + boto3
.venv/bin/python -m pytest -q               # optional: run the unit tests
```

Note the two paths you'll reference from Kiro:

- `AIDLC_MCP_PYTHON` = absolute path to `code/kiro-mcp/.venv/bin/python`
- `AIDLC_MCP_HOME`   = absolute path to `code/kiro-mcp`

## 2. Cognito app client

The server authenticates non-interactively per persona. The Cognito **app client** it uses
(`KiroCognitoClientId`) is provisioned by CDK with `USER_PASSWORD_AUTH` enabled, so it can mint the
access tokens the Project API authorizes out of the box. AWS also requires ambient AWS credentials for
S3 artifact reads (the environment's default profile/role).

## 3. Configuration (from CDK stack outputs)

There is no web UI / `/config.json`. The deploy publishes the values as **stack outputs**
(`AIDLCPlatformWorkshop.Kiro*`). Read them with:

```bash
aws cloudformation describe-stacks --stack-name AIDLCPlatformWorkshop \
  --query "Stacks[0].Outputs" --output table --no-cli-pager
```

Map the outputs to these env vars:

| Env var | From stack output |
| --- | --- |
| `AIDLC_PROJECT_API_URL` | `KiroProjectApiUrl` |
| `AIDLC_COGNITO_USER_POOL_ID` | `KiroCognitoUserPoolId` |
| `AIDLC_COGNITO_CLIENT_ID` | `KiroCognitoClientId` (the Kiro client; has `USER_PASSWORD_AUTH`) |
| `AIDLC_AWS_REGION` | deploy region (`us-east-1`) |
| `AIDLC_FILES_BUCKET` | `KiroFilesBucket` |

Plus:

| Env var | Meaning |
| --- | --- |
| `AIDLC_AUTH_MODE` | `password` (default) or `refresh_token` |
| `AIDLC_STATE_DIR` | (optional) shared marker dir for the review hook; both server and hooks must agree — set an absolute path or rely on the default temp dir |
| `AIDLC_TEST_COMMAND` | (optional) override the developer's local test command |

Persona credentials — two models:

- **Per-persona (repo agents, supports in-window switching):** set one pair per persona used by the
  agent files: `AIDLC_BA_USERNAME`/`AIDLC_BA_PASSWORD`, `AIDLC_SA_USERNAME`/`AIDLC_SA_PASSWORD`,
  `AIDLC_DEV_*`, `AIDLC_QA_*`, `AIDLC_DEVOPS_*`, `AIDLC_MAINT_*`.
- **Generic (Power default server, one persona per environment):** set
  `AIDLC_PERSONA_USERNAME`/`AIDLC_PERSONA_PASSWORD` (or `AIDLC_PERSONA_REFRESH_TOKEN`).

Use the six pre-provisioned Cognito **Test_Users** the stack outputs (BA/SA/Dev/QA/DevOps/Maint).
Never commit credentials; export them in your shell/OS environment so Kiro's `${VAR}` expansion
resolves them.

## 4. Load into Kiro

Either:

- **Open this repo in Kiro** — the six persona agents (`.kiro/agents/`), enforcement hooks
  (`.kiro/hooks/`), and skills (`.kiro/skills/`) are picked up automatically; or
- **Install the Power** — Kiro Powers panel -> Add Custom Power -> Import from a folder ->
  `powers/aidlc-platform/` (provides the MCP server config, skills, and steering).

## 5. Verify

Select your persona agent (e.g. Solution Architect) and ask: **"Show me the projects I'm working
on."** A successful `list_projects` (projects tagged `mine`/`awaiting_me`/...) confirms auth,
config, and connectivity. If it fails fast at startup, the error names the missing config value.
