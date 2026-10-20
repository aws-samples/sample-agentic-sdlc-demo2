# Agentic SDLC Transformation — Technical Brief

*Kiro-IDE-fronted, multi-persona, agent-driven software delivery on AWS.*
*Prepared for customer presentation. No secret values are included; secrets are referenced by name/ARN only.*

---

## 1. Executive summary

The Agentic SDLC platform lets a small set of AI personas carry a software
project through its entire lifecycle — inception → design → construction → QA →
deployment — under explicit human control. Each persona is an AI agent operating
inside **Kiro IDE**, which is the sole frontend. This build runs the Kiro-native
architecture: **Kiro generates every deliverable**, and a thin
serverless backend is the authoritative **system of record**. All personas
collaborate on the **same project**, which advances through an explicit **handoff
state machine** with server-enforced role-based access control (RBAC).

There is no Amazon Bedrock AgentCore in the active path — no Runtime, Gateway, or
Memory, and no server-side generation engine or Strands orchestrator. Each
persona produces their stage artifacts with Kiro's own agent + model + Skill; the
backend owns state, RBAC, acceptance validation, and the shared record. Kiro
talks to that backend through a purpose-built **MCP server** (Model Context
Protocol) that exposes the control plane plus the Kiro-native generation flow
(`get_stage_brief` / `submit_artifact` / `complete_stage`).

Key properties:
- **Six personas, one project, explicit handoffs** — no persona can act out of turn.
- **Registry-driven workflow** — states, roles, and legal transitions live in one source of truth.
- **Defense-in-depth auth** — Amazon Cognito everywhere; the Project API is the authoritative RBAC boundary.
- **Real Git round-trip** — developer code is ingested from GitHub/GitLab and tested by a managed test-runner.
- **Infrastructure as code** — one AWS CDK stack, pinned to a single account.

---

## 2. Architecture at a glance

```
                         ┌───────────────────────────────────────────----──┐
   Kiro IDE (frontend)   │                    AWS                          │
   ┌───────────────┐     │                                                 │
   │ Persona agent │     │   ┌───────────-────┐     ┌──────────-────────┐  │
   │  (BA/SA/Dev/  │     │   │ Amazon Cognito │     │  Project API      │  │
   │   QA/DevOps/  │     │   │  user pool +   │────▶│ (Lambda + API GW, │  │
   │   Maint)      │     │   │  6 role groups │     │  Cognito-authz)   │  │
   └──────┬────────┘     │   └───────┬────────┘     └────────┬─────-────┘  │
          │ MCP (stdio)  │           │                       │             │
   ┌──────▼────────┐     │           │ JWT (groups)          │ read/write  │
   │ Platform MCP  │─────┼───────────┴───────-───────────────┤             │
   │ server        │     │                                   ▼             │
   │ (control plane│     │   ┌──────────────────┐   ┌──────────-─────┐     │
   │  + brief/     │─────┼──▶│ DynamoDB         │   │ S3 artifacts + │     │
   │  submit/      │     │   │ AidlcProjects    │   │ ingested source│     │
   │  complete)    │     │   │ (system of record│   └────────────-───┘     │
   └───────────────┘     │   └──────────────────┘         ▲                │
                         │   ┌──────────────────────┐     │                │
                         │   │ CodeBuild: repo      │     │                │
                         │   │ ingest + QA test-run │◀── Git roundtrip     │
                         │   └──────────────────────┘                      │
                         │   (signature-verified Git push webhook)         │
                         └─────────────────────────────────────────────────┘
```

**Planes:**
- **Control plane** — Project API (handoff state, RBAC, stage cursor) over DynamoDB.
- **Generation plane** — Kiro (persona agents + stage Skills + Kiro's model); no server-side engine.
- **Client plane** — Kiro persona agents + the Platform MCP server.
- **Integration plane** — Git ingestion + QA test-runner (CodeBuild), signature-verified push webhook.

---

## 3. Authentication & authorization (end to end)

### 3.1 Identity — Amazon Cognito
- One Cognito **user pool** with email sign-in and a custom attribute `custom:role`.
- **Six role groups**, matching the stable persona identifiers:
  `requirements-analyst`, `system-architect`, `software-developer`,
  `qa-engineer`, `devops-engineer`, `maintenance-specialist`.
- A **pre-sign-up** Lambda auto-confirms accounts (demo/workshop convenience).
- A **post-confirmation** Lambda reads `custom:role` and adds the user to the
  matching Cognito group (`AdminAddUserToGroup`), so a user's persona is fixed at
  sign-up. An unrecognized role is assigned no group (sign-up never blocked).

### 3.2 App client & token flow
- **Kiro client** (public, no secret): `USER_PASSWORD_AUTH`. The Platform MCP
  server signs a persona in non-interactively and receives ID + access tokens.
  Each persona agent carries its own credentials, so switching agents switches
  identity. (The former machine/client-credentials client and the `agentcore`
  resource-server scope were removed with AgentCore.)

### 3.3 Authorization — defense in depth
Two layers, with the Project API as the authoritative boundary:

1. **Project API RBAC (authoritative).** API Gateway's Cognito authorizer injects
   verified JWT claims (including `cognito:groups`). Every state-changing route
   is **owner-gated**: the caller's groups must contain the record's current
   `owning_role`, checked *before* any mutation. Non-owner → **HTTP 403**.
2. **Transition legality.** The shared workflow registry's `validate_transition`
   is the single authority on whether a state move is legal. Illegal → **HTTP 422**;
   the record is left unchanged.

Additional guardrails: concurrent writes use conditional writes (**HTTP 409** on a
stale conditional write); unresolvable requests return **HTTP 400**. There is no
runtime component to enforce RBAC — the backend is the sole enforcement
point.

---

## 4. The workflow: state machine & handoffs

A single **workflow registry** (JSON + helpers, kept consistent between the
Project API Lambda copy and the Kiro frontend copy) is the sole source of truth
for personas, segments, states, and legal transitions.

**Forward path:**
```
draft → requirements_ready → assigned_to_sa → in_design → design_ready →
ready_for_dev → assigned_to_dev → implementation_submitted → assigned_to_qa →
in_qa → qa_passed → assigned_to_devops → in_deployment → deployed
```

**Request-changes bounces (one step back, with comments):**
- SA → BA (`assigned_to_sa → requirements_ready`)
- Dev → SA (`assigned_to_dev → design_ready`)
- QA → Dev (`in_qa → assigned_to_dev`)
- DevOps → QA (`in_deployment → assigned_to_qa`)

Cross-persona ownership transfer is an explicit persona action
(`handoff_project`); the reviewer bounce is `return_for_changes`. Within an
owner, `complete_stage` advances the stage cursor and — where the stage declares
`advances_state` — the coarse owned lifecycle state via `validate_transition`,
appending a history entry atomically. **Adding a persona is a registry edit, not
a code change.**

---

## 5. The Kiro frontend & the Platform MCP server

- **Six persona agents** (`.kiro/agents/*.md`) — one per role. Each carries that
  persona's credentials and a curated toolset. You switch persona from Kiro's
  agent picker; that is effectively "logging in" as that role.
- **Platform MCP server** (Python, stdio) — wraps the Project API + S3 and exposes:
  - **Read/visibility:** `list_projects`, `get_project`, `list_artifacts`, `get_artifact`
  - **Lifecycle/transition:** `create_project`, `delete_project`, `handoff_project`, `return_for_changes`
  - **Kiro-native generation flow:** `get_stage_brief`, `submit_artifact`, `complete_stage`
  - **Developer:** `link_repo`, `submit_implementation`; **QA:** `run_tests`; **BA:** `upload_requirements`
- **Generation flow** — `get_stage_brief` returns the active stage's contract
  (its `produces`, `consumes`, `acceptance`, and guiding `skill`) derived from the
  registry + the project's `current_stage` cursor. The persona generates the
  artifact in Kiro using the referenced Skill + Kiro's model, uploads it with
  `submit_artifact` (an owner-gated, `produces`-constrained short-TTL presigned
  PUT), then calls `complete_stage`. There is no agent-conversation WebSocket
  bridge — Kiro generates directly.
- **Enforcement hooks** (local, in Kiro): *review-before-approve* (must read
  artifacts before advancing) and *tests-before-submit* (developer tests must
  pass before submit). They never block a `return_for_changes` bounce. Server RBAC
  remains authoritative regardless.

---

## 6. The generation model: Kiro generates

- **Kiro produces every deliverable.** Each active stage is `generator: kiro`
  in the registry, with a `stage_contract` describing its `skill`, `consumes`,
  `produces` (each with `acceptance` rules), and optional `advances_state`.
- **Stage Skills** (`.kiro/skills/<persona>/<stage>`) are the per-stage playbooks
  that guide generation. The persona agent reads the `consumes` inputs and writes
  the `produces` artifact using its Skill + Kiro's model.
- **Acceptance validation** runs server-side in `complete_stage` and is **lenient
  in v1**: rule failures are warnings; the one hard block is a missing *required*
  artifact.
- **Tool access** is the MCP server's own tools (Project API + S3), plus whatever
  additional MCP servers a persona agent is configured with. There is no AgentCore
  Gateway; per-project Atlassian credentials are resolved by an Atlassian proxy
  that calls the Atlassian REST APIs directly.

**Active stages today:**
- **BA:** requirements analysis, user stories
- **SA:** workflow planning, NFR requirements, NFR design, application design, units generation, inception complete
- **Dev:** construction handoff (packages the construction brief, read in Kiro)
- **QA:** test planning, test execution (assessment), QA report
- **DevOps:** deployment planning, release

In-platform application-code generation does not run — the developer implements
externally (in Kiro) from the construction brief and the platform ingests the
resulting repository.

---

## 7. Persona-by-persona walkthrough

| Persona | Cognito group | Owns states | What they do | Key tools |
| --- | --- | --- | --- | --- |
| **Business Analyst** | `requirements-analyst` | `draft → requirements_ready` | Create project, upload requirements, generate requirements + user stories, hand off to SA | `create_project`, `upload_requirements`, `get_stage_brief`, `submit_artifact`, `complete_stage`, `handoff_project` |
| **Solution Architect** | `system-architect` | `assigned_to_sa → in_design → design_ready` | Review BA artifacts (coverage/risks), generate NFRs + design, approve or return | `get_artifact`, `get_stage_brief`, `submit_artifact`, `complete_stage`, `return_for_changes`, `handoff_project` |
| **Software Developer** | `software-developer` | `assigned_to_dev → implementation_submitted` | Read the construction brief, implement in Kiro, push to Git, link repo, submit | `get_artifact`, `link_repo`, `submit_implementation`, `return_for_changes` |
| **QA Engineer** | `qa-engineer` | `assigned_to_qa → in_qa → qa_passed` | Run the test-runner, review results, generate test plan/report, pass or return | `run_tests`, `get_project`, `get_stage_brief`, `submit_artifact`, `complete_stage`, `return_for_changes` |
| **DevOps Engineer** | `devops-engineer` | `assigned_to_devops → in_deployment → deployed` | Generate deployment plan/release runbook, release or return | `get_stage_brief`, `submit_artifact`, `complete_stage`, `get_artifact`, `return_for_changes` |
| **Maintenance Specialist** | `maintenance-specialist` | (none — read-only) | Cross-project operations view; inspect status/artifacts only | `list_projects`, `get_project`, `get_artifact` |

**Flow narrative:** BA creates the project and generates requirements/stories →
hands to SA. SA reviews and, if satisfied, approves and generates the design →
hands to Dev. Dev implements from the brief, pushes, links the repo, and submits
→ the platform ingests the code and auto-advances to QA. QA runs tests, reviews,
and passes → hands to DevOps. DevOps plans and releases → `deployed`. At any
review point a persona can return the work one step back with comments.

---

## 8. The Git round-trip (bridging external coding back in)

1. **Link** — Developer calls `link_repo` with an `https://github.com|gitlab.com/owner/repo`
   URL (validated against an allowlist — an SSRF guard). An optional private-repo
   token is stored in Secrets Manager (`aidlc/projects/{uuid}/git`), never on the
   record, never logged.
2. **Submit** — `submit_implementation` transitions to `implementation_submitted`
   (blocked unless a repo is linked). This triggers a **CodeBuild ingest** job:
   clone → guardrail check (size/file caps) → `s3 sync` into `{slug}/source/` →
   direct-invoke the Project API's ingest callback, which **auto-advances to QA**.
3. **QA test-runner** — QA's `run_tests` starts a **CodeBuild** job that pulls
   `{slug}/source/`, auto-detects npm/pytest, writes `aidlc-docs/qa/test-results.md`,
   and calls back with pass/fail (QA decides forward vs bounce).
4. **Push webhook (optional)** — a dedicated, public endpoint verifies a GitHub
   HMAC / GitLab token signature before any access and re-triggers ingest on push.

---

## 9. Data & storage model

- **Records live in DynamoDB.** Project records, workflow state, the stage cursor,
  and history live in the **`AidlcProjects`** table (single-table design keyed
  `PROJECT#{uuid}` / `META`, with GSIs for role-queue / broad selection / state
  dashboards). Artifacts and ingested source stay in **S3**: artifacts under
  `{project_slug}/aidlc-docs/...`; ingested source under `{project_slug}/source/`.
- **Reserved object prefix:** `_platform/projects/` is retained but unused — kept
  reserved so a client-supplied `project_uuid` can never collide with a `{slug}/`
  artifact tree (a path-traversal guard).
- **Two identifiers, kept distinct:** `project_uuid` (stable, credential/record
  key; regex-constrained to block path traversal) and `project_slug` (the S3
  artifact prefix, derived from the name).
- **Presigned uploads** are short-lived (~5 min), owner-gated, and constrained to
  a path the active stage `produces`; the client's standing S3 access stays
  read-only.
- **Display vs storage:** artifacts are stored under the stable `aidlc-docs/`
  prefix and only *displayed* relabeled as `agentic-sdlc` — the S3 key never
  changes.

---

## 10. Security posture

- **Authenticated by default** — the only network-exposed endpoint is the Project
  API (Cognito access-token authorized), plus the signature-verified Git webhook,
  which verifies an HMAC/token signature before doing anything.
- **RBAC is server-authoritative** — persona identity comes from verified Cognito
  JWT claims; the client cannot forge it. 403 (not your turn) / 422 (illegal move)
  / 409 (stale conditional write).
- **Secrets by reference only** — per-project Atlassian creds, per-project Git
  tokens, and the Cognito app-client secret all live in Secrets Manager and are
  never logged, echoed, or stored on records.
- **Input hardening** — repo-URL allowlist (SSRF), path-traversal guards on
  `project_uuid` and upload paths, short presigned TTLs constrained to the stage
  `produces` allowlist.
- **Account pinning** — the CDK stack refuses to deploy to any account other than
  the pinned one unless explicitly overridden (guards against cross-account mishaps).
- **Demo caveat (state clearly in the deck):** the workshop provisions six
  fixed test users with known passwords and emits them as stack outputs for
  convenience. For any non-demo posture these would be removed, and real users
  would self-serve sign-up with per-user credentials.

---

## 11. Deployment & infrastructure

- **One AWS CDK v2 (TypeScript) stack** — `AIDLCPlatformWorkshop` — composing:
  Cognito auth, the Project API (Lambda + API Gateway), the DynamoDB record table,
  the S3 project bucket, and the Git ingest + QA test-runner CodeBuild projects +
  webhook. There are no AgentCore constructs.
- **Kiro configuration is published as stack outputs** (`Kiro*`): Project API URL,
  Cognito user-pool ID, Kiro client ID, files bucket — mapped into the MCP
  server's `AIDLC_*` settings (`KiroProjectApiUrl → AIDLC_PROJECT_API_URL`,
  `KiroCognitoUserPoolId → AIDLC_COGNITO_USER_POOL_ID`,
  `KiroCognitoClientId → AIDLC_COGNITO_CLIENT_ID`,
  `KiroFilesBucket → AIDLC_FILES_BUCKET`, plus `AIDLC_AWS_REGION`,
  `AIDLC_AUTH_MODE`, and per-persona credentials). There is no runtime ARN.
- **Region/account** — single pinned account, `us-east-1`.

---

## 12. Technology stack

| Layer | Technology |
| --- | --- |
| Frontend | Kiro IDE (persona agents, skills, hooks) + Platform MCP server (Python, MCP stdio) |
| Generation | Kiro (persona agents + stage Skills + Kiro's model) — no server-side engine |
| Control plane / system of record | API Gateway + Lambda (Python) Project API; DynamoDB (`AidlcProjects`) |
| Artifact storage | Amazon S3 (artifacts + ingested source) |
| Identity | Amazon Cognito (`USER_PASSWORD_AUTH` per persona for the MCP server) |
| Integration | CodeBuild (repo ingest + QA test-runner); signature-verified Git webhook |
| Infrastructure | AWS CDK v2 (TypeScript), single pinned account |

> Note: Amazon Bedrock model access is **not** a dependency of the deployed stack
> — Kiro performs generation with its own model.

---

## 13. Current scope (be precise in the demo)

- **Live end-to-end:** all six personas, the full handoff state machine, the
  Kiro-native generation flow (`get_stage_brief` / `submit_artifact` /
  `complete_stage`), the Git round-trip, and the QA test-runner.
- **Active generation stages:** BA and SA inception stages, the Dev construction
  handoff, QA test planning/execution/report, DevOps deployment planning/release.
- **Intentionally not run in-platform:** application-code generation — the
  developer codes externally (in Kiro) and the platform ingests the repo.
- **Frontend:** Kiro only; there is no web UI.
