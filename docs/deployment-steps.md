# Deploying Agentic SDLC Transformation to an AWS Account

Complete, verified deployment steps for getting this platform into a fresh AWS
account. These steps were traced against the actual scripts (`package.json`,
`scripts/package-assets.sh`, `cdk/bin/cdk.ts`, `cdk/lib/utils/account-guard.ts`).

The platform runs the **Kiro-native** architecture: **Kiro generates
every deliverable** (its own agent + model + per-stage Skills) and the deployed
stack is a **thin serverless backend** that is the authoritative system of
record. There is **no web UI** and **no Amazon Bedrock AgentCore** — so no
CloudFront distribution, no agent container image, and no always-on runtime to
build or pay for.

> **Heads-up (the #1 gotcha):** the CDK app's account guard is pinned to a
> **placeholder** account (`123456789012`). A naïve deploy **halts with an
> "Account mismatch" error** unless you explicitly supply your own account.
> Step 3 handles this.

---

## Overview — what gets deployed

The single CloudFormation stack (`AIDLCPlatformWorkshop`) provisions:

- **Amazon Cognito** — user pool with the six persona groups, a
  `USER_PASSWORD_AUTH` app client for the Kiro MCP server, a post-confirmation
  Lambda that maps each user's `custom:role` to a Cognito group, and six
  pre-provisioned test users (one per persona).
- **Project API** — an **AWS Lambda** fronted by **Amazon API Gateway**. This is
  the only network-exposed application endpoint (Cognito access-token
  authorized). It owns RBAC, workflow state, the per-project stage cursor,
  acceptance validation, and handoff transitions — all server-side.
- **Amazon DynamoDB** — the `AidlcProjects` table (single-table design with
  `GSI1` role-queue / `GSI2` broad selection / `GSI3` state dashboards) holding
  project records, workflow state, the stage cursor, and history.
- **Amazon S3** — the artifact / ingested-source bucket.
- **AWS CodeBuild** — a repo-ingest project (clone → `s3 sync` the developer's
  repo into `{slug}/source/`) and a test-runner project.
- **A signature-verified Git webhook** — an API Gateway endpoint that can
  re-trigger repo ingestion on push.

RBAC, state, and validation are enforced in the Project API: **HTTP 403** for a
non-owning caller, **HTTP 422** for an illegal transition, **HTTP 409** for a
stale conditional write.

Generation itself is done by **Kiro** (its own agent + model + the per-stage
Skills under `.kiro/skills/`). The deployed backend runs no model, so **Bedrock
model access is not a deploy prerequisite**.

---

## Prerequisites (one-time, on the deployer's machine)

| Tool | Version | Notes |
|---|---|---|
| **AWS CLI v2** | latest | `aws configure` with credentials for the **target account** |
| **Node.js** | 20+ | |
| **Yarn** | classic (1.x) | `npm install -g yarn` |
| **AWS CDK v2** | — | Pinned as a dev dependency; run via `yarn workspace cdk cdk …` (no global install needed) |
| **Docker** | **NOT required** | Lambdas bundle from `cdk/custom-resources/` via CDK `Code.fromAsset`; there is no container build |
| **Bedrock model access** | **NOT required by the stack** | Generation is done by Kiro. Only **Kiro itself** needs model access — the deployed backend runs no model |
| **Kiro IDE** | latest | The only frontend; used after deploy to drive the workflow |
| **AWS account** | — | Administrator access for the first deploy |

The default region is **`us-east-1`**. Everything below assumes that unless you
override it.

---

## 1. Clone the code and install dependencies

```bash
git clone <your-repo-url>
cd <repo-root>          # the folder containing package.json + cdk/ + code/
yarn install
```

---

## 2. Configure AWS credentials and verify the account

```bash
aws configure                 # or: export AWS_PROFILE=<your-profile>
aws sts get-caller-identity   # verify: "Account" must be the account you intend to deploy to
```

Note the account ID it prints — you'll need it in the next step.

---

## 3. Set the deploy account (handle the account guard — critical)

The CDK app has a safety guard pinned to a **placeholder** account
(`123456789012`). If you just run the deploy, it aborts with:

```
Account mismatch: the resolved AWS account '<your-id>' does not match the
expected deployment account '123456789012'. …
```

You must tell it your real account. Two repo-sanctioned ways (no source edits,
both git-ignored):

**Option A — git-ignored `cdk/.env` (persists across shells):**

```bash
cp cdk/.env.example cdk/.env
# then set in cdk/.env:
#   AIDLC_DEPLOY_ACCOUNT=<your-account-id>
#   CDK_DEFAULT_REGION=us-east-1        # optional, if not us-east-1
```

**Option B — export in the shell (most reliable across all commands):**

```bash
export AIDLC_DEPLOY_ACCOUNT=<your-account-id>
export CDK_DEFAULT_REGION=<your-region>   # optional, if not us-east-1
```

`bin/cdk.ts` treats `AIDLC_DEPLOY_ACCOUNT` as an explicit override that
**bypasses the placeholder guard**. (A one-off `cdk` invocation can also pass
`-c account=<id>` for the same effect.) Keep the value set for **all** remaining
steps in the same shell session.

---

## 4. Bootstrap CDK (once per account/region)

Use the **explicit environment form** so bootstrap doesn't need to synthesize the
app (which would otherwise require the assets-bucket context):

```bash
yarn workspace cdk cdk bootstrap aws://${AIDLC_DEPLOY_ACCOUNT}/${CDK_DEFAULT_REGION:-us-east-1}
```

---

## 5. Deploy

```bash
yarn deploy-infrastructure
```

This single command (see the `deploy-infrastructure` script in `package.json`)
does two things:

1. Runs `scripts/package-assets.sh`, which:
   - creates/reuses the assets bucket `aidlc-assets-<account>-<region>`,
   - stages the deployment assets and uploads them to `s3://aidlc-assets-…/assets/`,
   - prints the bucket name.
2. Runs `cdk deploy --require-approval never -c assetsBucketName=<that-bucket>`.

**The first deploy is fast** — there is no ARM64 agent container build and no UI
build to wait on; Lambdas bundle straight from `cdk/custom-resources/`.

> - If the deploy stops on the account-mismatch error, `AIDLC_DEPLOY_ACCOUNT`
>   isn't set in this shell — redo Step 3.
> - If a deploy fails midway and you want to inspect state instead of
>   auto-rolling-back, use `yarn deploy-infrastructure-no-rollback`.

---

## 6. Collect the stack outputs

When the deploy finishes, read the outputs:

```bash
aws cloudformation describe-stacks --stack-name AIDLCPlatformWorkshop \
  --query "Stacks[0].Outputs" --output table --no-cli-pager
```

| Output | What it is |
|---|---|
| `KiroProjectApiUrl` | Project API base URL → `AIDLC_PROJECT_API_URL` |
| `KiroCognitoUserPoolId` | Cognito user pool id → `AIDLC_COGNITO_USER_POOL_ID` |
| `KiroCognitoClientId` | Cognito app client id (`USER_PASSWORD_AUTH`) → `AIDLC_COGNITO_CLIENT_ID` |
| `KiroFilesBucket` | Artifact / source S3 bucket → `AIDLC_FILES_BUCKET` |
| `TestUserEmail` / `TestUserPassword` | Software Developer test login |
| `BATestUserEmail` / `BATestUserPassword` | Business Analyst test login |
| `SATestUserEmail` / `SATestUserPassword` | Solution Architect test login |
| `QATestUserEmail` / `QATestUserPassword` | QA Engineer test login |
| `DevOpsTestUserEmail` / `DevOpsTestUserPassword` | DevOps Engineer test login |
| `MaintTestUserEmail` / `MaintTestUserPassword` | Maintenance Specialist test login |
| `AIDLCPlatformAgentsGitWebhookApiEndpoint…` | Public Git push webhook URL (append `git-webhook`) |

There is **no** `AIDLCPlatformURL` (no web UI) and **no** `KiroRuntimeArn` (no
AgentCore runtime).

---

## 7. Configure and launch the Kiro frontend

Kiro IDE is the only frontend. Full detail is in
[`../code/kiro-mcp/docs/setup.md`](../code/kiro-mcp/docs/setup.md); the essentials:

1. **Install the MCP server** (its own venv):

   ```bash
   cd code/kiro-mcp
   python3 -m venv .venv
   .venv/bin/python -m pip install -e .
   ```

2. **Map the stack outputs → `AIDLC_*` env vars** (see the table in Step 6 and
   the setup guide). You can set them by hand, or let the helper scripts pull
   them from the stack automatically:

   ```bash
   source scripts/kiro-testing-env.sh        # exports AIDLC_* from the stack outputs
   # or scaffold a ready-to-open workspace:
   bash scripts/setup-test-workspace.sh
   ```

3. **Load into Kiro** — either **open this repo in Kiro** (the per-persona agents
   in `.kiro/agents/`, the stage Skills in `.kiro/skills/`, and the enforcement
   hooks in `.kiro/hooks/` are picked up automatically), or **install the Power**
   from `powers/aidlc-platform/` (Kiro Powers panel → Add Custom Power → Import
   from folder).

4. **Pick a persona agent** (e.g. Business Analyst) and **sanity check**: ask
   *"show me my projects"* → a successful `list_projects` confirms auth, config,
   and connectivity. If it fails fast at startup, the error names the missing
   config value.

---

## 8. Verify (optional but recommended)

From the repo root, run the three suites (each uses its own venv; see
[`../.kiro/steering/60-testing.md`](../.kiro/steering/60-testing.md)):

```bash
# CDK assertions + account guard
yarn workspace cdk test

# Project API + record store + validators + post-confirm + e2e (in-memory fakes, no AWS)
cdk/custom-resources/.venv-project-api/bin/python -m pytest cdk/custom-resources/tests/

# Kiro MCP server (platform tools, multi-persona e2e, hook-gating, config)
code/kiro-mcp/.venv/bin/python -m pytest code/kiro-mcp/tests/
```

If a venv is missing, the corresponding suite can't run in this environment —
create it (Step 7 for the MCP one) rather than skipping verification silently.

---

## 9. Tear down

```bash
yarn delete-infrastructure       # cdk destroy
```

> **Not removed by `cdk destroy` — delete manually for a clean account:** the
> **assets bucket** `aidlc-assets-<account>-<region>` (created outside the stack
> by `package-assets.sh`). Empty and delete it manually.

⚠️ This is a high-impact operation — it deletes the Cognito pool, the DynamoDB
table, and S3 project data. Confirm you're in the right account
(`aws sts get-caller-identity`) before running it.

---

## Cost notes

The stack is entirely **serverless / pay-per-use** — there is no always-on
runtime and no NAT gateway:

- **AWS Lambda** (Project API, post-confirm, webhook) — per-request.
- **Amazon DynamoDB** (`AidlcProjects`) — on-demand capacity.
- **Amazon API Gateway** — per-request.
- **Amazon S3** — storage + requests for artifacts and ingested source.
- **AWS CodeBuild** — per-build minutes (only when a repo is ingested or tests
  run).
- **Amazon Cognito** — monthly active users.

At idle the platform costs effectively nothing beyond stored data. Kiro's model
usage is billed separately through Kiro (not the deployed stack).

---

## Troubleshooting

- **"Account mismatch" error** → `AIDLC_DEPLOY_ACCOUNT` not set in this shell
  (Step 3), or set to the wrong id.
- **"Missing CDK context 'assetsBucketName'"** → you ran `cdk deploy` directly
  instead of `yarn deploy-infrastructure`; use the yarn script (it injects the
  bucket), or pass `-c assetsBucketName=<bucket>` yourself.
- **Bootstrap tries to synth and errors on `assetsBucketName`** → use the
  explicit `aws://account/region` form shown in Step 4.
- **MCP server returns 401** → Cognito credentials expired/invalid; refresh the
  persona credentials (re-source `scripts/kiro-testing-env.sh` or re-export the
  `AIDLC_*_USERNAME`/`_PASSWORD` pair).
- **MCP server returns 403 / 422** → not a config problem: **403** means it isn't
  your persona's turn (you don't own the project's current state), **422** means
  the move is an illegal workflow transition. This is RBAC/state working as
  intended.
- **`spawn ENOENT` when Kiro starts the MCP server** → the command needs the
  **absolute** path to the venv Python (`AIDLC_MCP_PYTHON` =
  `…/code/kiro-mcp/.venv/bin/python`).
- **`ModuleNotFoundError: aidlc_mcp`** → the MCP package isn't installed in that
  venv; run `.venv/bin/python -m pip install -e .` from `code/kiro-mcp` (Step 7).

---

## Quick copy-paste quickstart (target = your account, us-east-1)

```bash
git clone <your-repo-url> && cd <repo-root>
yarn install
export AIDLC_DEPLOY_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
yarn workspace cdk cdk bootstrap aws://${AIDLC_DEPLOY_ACCOUNT}/us-east-1
yarn deploy-infrastructure
aws cloudformation describe-stacks --stack-name AIDLCPlatformWorkshop \
  --query "Stacks[0].Outputs" --output table --no-cli-pager
# install + configure the Kiro MCP frontend:
cd code/kiro-mcp && python3 -m venv .venv && .venv/bin/python -m pip install -e . && cd ../..
source scripts/kiro-testing-env.sh    # exports AIDLC_* from the stack outputs
# → open the repo in Kiro (or install powers/aidlc-platform/), pick a persona, "show me my projects"
```
