# Deployed Environment & Access

Example access reference for a **test/demo deployment** of Agentic SDLC
Transformation. This is a **template** showing how a deployed environment looks
once you deploy the stack into your own AWS account (`<your-account-id>`,
`us-east-1`) with pre-provisioned demo users. The account id `123456789012` below
is a placeholder — substitute your own account id.

> ⚠️ The credentials shown here are **illustrative demo values**. Do not reuse
> these passwords anywhere real; the stack provisions its own test users at deploy
> time.

## How you access it

The frontend is **Kiro IDE** — there is no browser URL. Each persona runs in Kiro
with their persona agent; the platform MCP server (`code/kiro-mcp/`) is configured
from the stack's `Kiro*` outputs and signs the persona into Cognito
(`USER_PASSWORD_AUTH`) with the logins below.

| | |
| --- | --- |
| AWS account / region | `<your-account-id>` (placeholder `123456789012`) / `us-east-1` |
| Stack | `AIDLCPlatformWorkshop` |
| Access | Kiro IDE + platform MCP server (configured from stack outputs) |

MCP server settings map from the stack outputs:
`KiroProjectApiUrl → AIDLC_PROJECT_API_URL`,
`KiroCognitoUserPoolId → AIDLC_COGNITO_USER_POOL_ID`,
`KiroCognitoClientId → AIDLC_COGNITO_CLIENT_ID`,
`KiroFilesBucket → AIDLC_FILES_BUCKET` (plus `AIDLC_AWS_REGION`, `AIDLC_AUTH_MODE`,
and per-persona credentials). There is no runtime ARN.

## Persona logins (all six)

| Persona | Cognito group | Email | Password |
| --- | --- | --- | --- |
| **Business Analyst** | `requirements-analyst` | `ba-tester@example.com` | `REPLACE_ME` |
| **Solution Architect** | `system-architect` | `sa-tester@example.com` | `REPLACE_ME` |
| **Software Developer** | `software-developer` | `testuser@example.com` | `REPLACE_ME` |
| **QA Engineer** | `qa-engineer` | `qa-tester@example.com` | `REPLACE_ME` |
| **DevOps Engineer** | `devops-engineer` | `devops-tester@example.com` | `REPLACE_ME` |
| **Maintenance Specialist** (read-only dashboard) | `maintenance-specialist` | `maint-tester@example.com` | `REPLACE_ME` |

These are emitted as stack outputs (`BATestUserEmail`/`Password`,
`SATestUser…`, `TestUser…` (developer), `QATestUser…`, `DevOpsTestUser…`,
`MaintTestUser…`).

## Service endpoints

The only network-exposed endpoints are the Project API (Cognito
access-token authorized) and the signature-verified Git webhook.

| Endpoint | URL |
| --- | --- |
| Project API | `https://exampleapiid02.execute-api.us-east-1.amazonaws.com/prod/` |
| **Git push webhook** (public, signature-verified) | `https://exampleapiid04.execute-api.us-east-1.amazonaws.com/prod/git-webhook` |

**Webhook secret:** stored in Secrets Manager at `aidlc/git-webhook/secret`.
Configure your GitHub/GitLab repo webhook to POST to the webhook URL with that
secret (GitHub → *Secret* for `X-Hub-Signature-256`; GitLab → *Secret token*).

## What's enabled in this deployment

The full **six-persona lifecycle** with the **Git round-trip**, all
Kiro-generated (`generator: kiro`):

```
BA → SA → Developer → QA Engineer → DevOps Engineer     (+ Maintenance read-only view)
```

- **BA** (inception): requirements analysis, user stories.
- **SA** (design): workflow planning, **NFR requirements**, **NFR design**,
  application design, units generation, inception complete.
- **Developer**: construction-handoff brief (read in Kiro); **link repo + submit**
  (GitHub/GitLab) → CodeBuild ingestion.
- **QA Engineer**: test planning, test execution, QA report; **run tests**
  (CodeBuild test-runner writes `aidlc-docs/qa/test-results.md`).
- **DevOps Engineer**: deployment planning, release.
- **Maintenance Specialist**: read-only cross-project **operations view**
  (repository ingest status/commit).

Handoff state machine:
```
draft → requirements_ready → assigned_to_sa → in_design → design_ready →
ready_for_dev → assigned_to_dev → implementation_submitted → assigned_to_qa →
in_qa → qa_passed → assigned_to_devops → in_deployment → deployed (terminal)
```
with request-changes bounces SA→BA, Dev→SA, QA→Dev, DevOps→QA.

## Prerequisites

The deployed stack does **not** require Amazon Bedrock model access — Kiro
performs generation with its own model. Each persona needs Kiro configured with
the platform MCP server (from the stack outputs above) and their persona login.

## Redeploy / teardown (maintainers)

```bash
set -a; source cdk/.env; set +a      # targets your account (refresh your AWS credentials if expired)
aws sts get-caller-identity --query Account --output text   # must print your account id
yarn deploy-infrastructure            # package assets + cdk deploy (UPDATE)
# teardown:
yarn delete-infrastructure
```
