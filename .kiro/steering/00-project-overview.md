---
inclusion: always
---

# Project Overview

Agentic SDLC Transformation is a multi-persona, agent-driven collaboration
platform for the full software lifecycle (inception → design → construction →
QA → deployment), built on AWS. Six personas work the same project under
explicit human control:

- **Business Analyst (BA)** — `requirements-analyst` — uploads requirements and
  produces user stories.
- **Solution Architect (SA)** — `system-architect` — reviews the BA artifacts
  and produces NFRs and the design/architecture deliverables.
- **Software Developer (Dev)** — `software-developer` — reads the construction
  brief in Kiro, implements the code in Kiro, pushes to GitHub/GitLab, then links
  the repo + submits.
- **QA Engineer** — `qa-engineer` — tests the ingested code (test planning /
  execution / report; can run a CodeBuild test-runner).
- **DevOps Engineer** — `devops-engineer` — deployment planning + release.

A read-only **Maintenance Specialist** (`maintenance-specialist`) persona owns
no segment; it views a cross-project operations dashboard.

Projects move forward through an explicit handoff state machine
(`draft → requirements_ready → assigned_to_sa → in_design → design_ready →
ready_for_dev → assigned_to_dev → implementation_submitted → assigned_to_qa →
in_qa → qa_passed → assigned_to_devops → in_deployment → deployed`) with
`request_changes` bounces SA→BA (`assigned_to_sa → requirements_ready`),
Dev→SA (`assigned_to_dev → design_ready`), QA→Dev (`in_qa → assigned_to_dev`)
and DevOps→QA (`in_deployment → assigned_to_qa`).

The **Git round-trip** bridges the external coding phase back into the platform:
linking a repo + submitting triggers a CodeBuild ingestion (clone → `s3 sync`
into `{slug}/source/`) that advances the project to QA; an optional
signature-verified push webhook can re-trigger it.

## Architecture model — "Model 1" (Kiro generates)

The platform runs the **Kiro-native "Model 1"** architecture
(`.kiro/specs/kiro-native-platform/`): **Kiro generates every deliverable**, and a
**thin serverless backend** is the authoritative **system of record**.
**Amazon Bedrock AgentCore is not used** — there is no Runtime, Gateway, or
Memory in the active path, and there is no server-side generation engine or
Strands orchestrator. Each persona produces their stage artifacts with Kiro's own
agent + model + Skill; the backend owns state, RBAC, acceptance validation, and
the shared record.

## Tech at a glance

- **Infra**: AWS CDK v2 (TypeScript) in `cdk/`. The stack is now
  **Cognito + Project API (Lambda + API Gateway) + DynamoDB + S3 + CodeBuild** —
  no AgentCore constructs.
- **Backend (system of record)**: the **Project API** (Python Lambda,
  `cdk/custom-resources/project_api.py`) fronted by API Gateway. Project records,
  workflow state, the stage cursor, and history live in an **Amazon DynamoDB**
  table (`AidlcProjects`, single-table design with GSI1 role-queue / GSI2 broad
  selection / GSI3 state dashboards). Artifacts and ingested source stay in **S3**.
- **Generation**: **Kiro** (persona agents + stage **Skills** in `.kiro/skills/`
  + Kiro's model). No AgentCore Runtime; `code/agentcore-runtime/` has been
  deleted.
- **Frontend**: **Kiro IDE is the only frontend** — a local (stdio) **MCP server**
  (`code/kiro-mcp/`) wrapping the Project API + S3, one **custom agent per
  persona** (`.kiro/agents/`), per-persona **stage Skills** (`.kiro/skills/`),
  **enforcement hooks** (`.kiro/hooks/`), packaged as a **Kiro Power**
  (`powers/aidlc-platform/`). There is no React/web UI. See
  `.kiro/specs/kiro-native-platform/`.
- **Auth**: Amazon Cognito (`USER_PASSWORD_AUTH` per-persona for the Kiro MCP
  server). The machine (client-credentials) client and the `agentcore`
  resource-server scope were removed with AgentCore; the six persona groups and
  the post-confirmation role-assign trigger are retained.

## The Kiro generation flow (brief → generate → submit → complete)

Every active stage is `generator: kiro`. A persona works a stage through the MCP
server + Project API:

1. **`get_stage_brief`** — the backend derives the active stage's contract from
   the registry + the project's `current_stage` cursor (its `produces`,
   `consumes`, `acceptance`, and guiding `skill`).
2. **generate in Kiro** — the persona reads the `consumes` inputs and produces the
   artifact using the referenced Skill + Kiro's model.
3. **`submit_artifact`** — obtains an owner-gated, `produces`-constrained
   short-TTL presigned PUT and uploads the artifact to S3.
4. **`complete_stage`** — the backend runs acceptance validation (**lenient in
   v1**: warnings only; a missing *required* artifact is the one hard block),
   advances the stage cursor, and — where the stage declares `advances_state` —
   advances the coarse owned lifecycle state via `validate_transition`, appending
   a history entry atomically.

Cross-persona ownership transfer stays an **explicit** persona action
(`handoff_project`); the reviewer bounce is `return_for_changes`.

## What is actually active

The **running** workflow spans all six personas; every active stage is
Kiro-generated (`generator: kiro`) with a registry `stage_contract`:

- **BA** (`ba_segment`): `requirements_analysis`, `user_stories`.
- **SA** (`sa_segment`): `workflow_planning`, `nfr_requirements`, `nfr_design`,
  `application_design`, `units_generation`, `inception_complete`.
- **Dev** (`dev_segment`): `construction_handoff` (packages the construction
  brief, read in Kiro). Submit is a Kiro/Project-API action (link repo →
  `implementation_submitted`).
- **QA** (`qa_segment`): `test_planning`, `test_execution`, `qa_report`.
- **DevOps** (`devops_segment`): `deployment_planning`, `release`.

QA `test_execution` produces an assessment; real test runs are performed by a
**CodeBuild test-runner** triggered from the QA `run_tests` tool in Kiro.
In-platform application code generation does not run — the developer implements in
Kiro/externally from the construction brief and pushes to Git; the platform
ingests that repo (the Git round-trip, all serverless, AgentCore-free) for
QA/DevOps to operate on.

## Frontend: Kiro IDE (the only frontend)

Personas drive the platform entirely from **Kiro IDE**
(`.kiro/specs/kiro-native-platform/`). The Kiro
MCP server (`code/kiro-mcp/`) exposes the control plane (list/get/create projects,
handoffs, review) **and** the Kiro-native generation flow
(`get_stage_brief` / `submit_artifact` / `complete_stage`). The former
agent-conversation bridge (`run_stage` / `answer_prompt` / the AgentCore
WebSocket) has been **removed**. Each persona runs their own Kiro window with
their persona agent (per-persona Cognito `USER_PASSWORD_AUTH` via a pluggable
credential provider); RBAC stays server-side and unchanged (Project API 403/422).
Enforcement hooks gate review-before-approve and tests-before-submit but never
block a request-changes bounce. There is no React/web UI (no CloudFront/S3 UI).

## Working norms

- Read relevant existing code before writing new code; match the conventions of
  the stack you are touching (Python / CDK-TS).
- Prefer registry/config edits over hardcoding (see `20-workflow-registry.md`).
- Never rename the stable functional identifiers (see `10-stable-identifiers.md`).
