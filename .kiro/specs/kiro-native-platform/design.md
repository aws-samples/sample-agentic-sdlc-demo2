# Design — Kiro-Native Agentic SDLC Platform (Model 1)

## 1. Overview & principles

Model 1 makes **Kiro the generator of every deliverable** and a **thin serverless
backend** the **system of record**. **AgentCore is removed entirely** (no Runtime,
Gateway, or Memory). Orchestration — the workflow state machine, stage
sequencing, RBAC, and project selection — lives in the **workflow registry + the
Project API (DynamoDB-backed)**, which is reachable by all users.

Principles:
- **Kiro generates; the backend governs.** Kiro (persona agents + Skills + its
  model) produces artifacts; the backend owns state, RBAC, validation, and the
  shared record.
- **Registry is the single source of truth** for personas/segments/stages/
  transitions and the new per-stage **generator/contract**.
- **Server-authoritative RBAC** (403 non-owner / 422 illegal) is unchanged.
- **DynamoDB** is the record store (indexed, conditional writes); **S3** holds
  artifacts and ingested source.
- **Lenient acceptance validation** in v1 (warn, don't block; missing required
  artifact is the one hard precondition).
- **Selection semantics unchanged** from today (broad-visibility list + role-level
  ownership indicator).

## 2. Architecture

```
   Kiro IDE (per user)                       Serverless backend (AWS)  — NO AgentCore
   ┌───────────────────────┐                 ┌──────────────────────────────────────┐
   │ persona agent (BA/SA/  │                 │  Amazon Cognito (Kiro client, groups) │
   │  Dev/QA/DevOps/Maint)  │   HTTPS (JWT)   │            │ verified role claim       │
   │  + stage Skills        │◀───────────────▶│  Project API (Lambda + API Gateway)   │
   │  + Kiro model          │                 │   ├ project CRUD / list / select      │
   │                        │                 │   ├ stage-brief / stage-artifact-url  │
   │ MCP server (stdio):    │                 │   ├ complete-stage (validate+advance) │
   │  project/select/brief/ │                 │   ├ transition / return-for-changes   │
   │  submit/complete/...   │                 │   └ link-repo / run-tests             │
   └───────────────────────┘                 │            │                           │
                                              │   DynamoDB (records + history + cursor)│
                                              │   S3 (artifacts + ingested source)     │
                                              │   CodeBuild (repo ingest + QA tests)   │
                                              │   Workflow registry (SSOT, mirrored)   │
                                              └──────────────────────────────────────┘
```

Two AI touchpoints collapse to one: **only Kiro runs a model.** The backend runs
no model — it validates and advances.

## 3. Component design

### 3.1 Kiro frontend
- **Persona agents** (`.kiro/agents/*.md`) — one per role; each authenticates as
  its persona and drives the stage flow. `run_stage`/WebSocket usage is removed.
- **Stage Skills** (`.kiro/skills/<persona>/<stage>.md`) — the migrated stage
  prompts/playbooks (formerly server-side stage agents). Each encodes: role/goal,
  the `consumes` inputs to read, the required output structure (aligned to
  `acceptance`), quality guidance, and the `produces` write path(s). Versioned
  in-repo → consistent across the org.
- **MCP server** (`code/kiro-mcp/`) — the only backend client (see 3.2).

### 3.2 MCP server tools (revised)
Removed: `run_stage`, `answer_prompt`, `stage_status`, `close_stage`, and
`aidlc_mcp/agentcore_ws.py`; `AIDLC_RUNTIME_ARN` config drops.

Retained: `list_projects`, `get_project` (now includes `current_stage`),
`create_project`, `upload_requirements`, `list_artifacts`, `get_artifact`,
`link_repo`, `submit_implementation`, `run_tests`, `delete_project`.

Transition tools (roles clarified — see §3.11):
- **`complete_stage`** — advances the stage cursor within a segment (and, where a
  stage declares it, advances the coarse owned state). **Primary progression tool.**
- **`handoff_project`** — **retained and required**: the explicit cross-persona
  handoff that transfers ownership (e.g. `requirements_ready → assigned_to_sa`).
- **`return_for_changes`** — retained: the reviewer bounce (one step back).
- **`advance_project`** — **retired** (its progress/approve role is folded into
  `complete_stage` + `handoff_project`).

New:
- **`get_stage_brief(project_uuid)`** → the active stage's contract (from the
  backend: registry + cursor).
- **`submit_artifact(project_uuid, path, content)`** → obtains a presigned PUT
  constrained to the active stage's `produces` path (owner-gated) and uploads.
- **`complete_stage(project_uuid, note?)`** → validates acceptance (lenient),
  advances the cursor or applies the segment handoff, records history.

### 3.3 Project API (DynamoDB-backed)
`cdk/custom-resources/project_api.py` evolves:
- **Storage swap:** replace S3-JSON record read/write/list
  (`_read_record`/`_write_record`/`_list_records`) with DynamoDB (see 3.5). The
  ETag-conditional pattern becomes a DynamoDB **conditional write** on a
  `version` attribute. This is the contained migration seam; registry, RBAC, and
  transition logic are unchanged.
- **New routes** (all Cognito-authorized, owner-gated where stateful):
  - `GET /projects/{id}/stage-brief` — derive the current stage contract from the
    registry + `current_stage`; slug-substitute `produces`/`consumes` paths.
  - `POST /projects/{id}/stage-artifact-url` — presigned PUT for a path that MUST
    be in the active stage's `produces` (403 non-owner; 400 off-contract path).
  - `POST /projects/{id}/complete-stage` — owner-gate → run acceptance (lenient
    warnings) → advance the cursor (and, if the stage declares `advances_state`,
    advance the coarse owned state via `validate_transition`, 422 if illegal) →
    append history + validation results. The **cross-persona handoff is a
    separate, explicit `handoff_project` action** (§3.11), not performed here.
    Returns `{status, warnings[], current_stage, handoff_state}`.
- **Existing routes** (create/list/get/transition/reassign/upload-url/link-repo/
  run-tests/callbacks) retained; transition/reassign keep 403/422 semantics.

### 3.4 Workflow registry
Add per-stage fields (defaults keep back-compat): `generator` (`kiro` for all
active stages in Model 1), `produces[]` (`{path, required, acceptance[]}`),
`consumes[]`, `skill`, and optional **`advances_state`** (the coarse
`handoff_state` this stage moves to on completion, within the same owner — see
§3.11). Extend `validate()` to check these are well-formed, that every `skill`
reference and every stage in `segment.stages` is consistent, and that any
`advances_state` is a legal forward target for the owning role. Keep the three
synced copies + the drift assertion.

### 3.5 DynamoDB data model
Single table `AidlcProjects` (on-demand capacity):

| Item | PK | SK | Notes |
| --- | --- | --- | --- |
| Project record | `PROJECT#{uuid}` | `META` | state, owning_role, current_stage, slug, name, repo/qa fields, latest_change_request, `version` |
| History entry | `PROJECT#{uuid}` | `HIST#{ts}#{seq}` | from, to, actor, note, kind (transition/stage-complete), validation summary |
| Artifact ref | `PROJECT#{uuid}` | `ART#{path}` | submitted_by, submitted_at, etag (audit; artifacts themselves in S3) |

GSIs (routine queries, no full scan):
- **GSI1 (role queue):** PK `ROLE#{owning_role}`, SK `updated_at` — "assigned to my role."
- **GSI2 (all projects / selection):** PK `ENTITY#PROJECT`, SK `updated_at` — broad-visibility listing (matches current selection), paginated.
- **GSI3 (state/dashboards):** PK `STATE#{handoff_state}`, SK `updated_at` — Maintenance/SLA views.
- (assignee/program GSIs deferred per R12 / out-of-scope.)

Concurrency: state-changing ops use `TransactWriteItems` to update `META`
(ConditionExpression on `version` and `owning_role`) + put the `HIST` item
atomically; a stale write fails the condition → HTTP 409.

### 3.6 Artifact store (S3)
Unchanged layout: records move to DynamoDB, but artifacts stay in the project
bucket under `{slug}/aidlc-docs/...`, ingested source under `{slug}/source/`.
Writes happen only via presigned PUTs minted by the owner-gated
`stage-artifact-url` / `upload-url` routes.

### 3.7 Auth (Cognito)
Keep the Kiro app client (`USER_PASSWORD_AUTH`) and the six groups. **Remove** the
machine (client-credentials) client and the resource-server `gateway` scope —
they existed only for agent↔Gateway. RBAC is unchanged (verified group claim →
owner-gate).

### 3.8 Git round-trip (retained, no AgentCore)
`link-repo` + submit → CodeBuild ingest → callback advances to QA; QA `run-tests`
→ CodeBuild test-runner → callback records results; signature-verified webhook
re-triggers ingest. All serverless; unchanged and already AgentCore-free.

### 3.9 AgentCore removal
Delete from `cdk/lib/aidlc-platform-agents.ts`: the **Gateway**, **Runtime**, and
**Memory** constructs; the runtime env/authorizer wiring; the M2M client. Retire
the agent container build (`AIDLCPlatformBuild` for `icode-agent`) and **delete the
Strands orchestrator under `code/agentcore-runtime/` outright**. Remove the
`Kiro*RuntimeArn` output and the Gateway/Memory SSM params. Net: the stack becomes
Cognito + Project API + DynamoDB + S3 + CodeBuild.

### 3.10 Artifact submission (presigned PUT) — detail

`submit_artifact(project_uuid, path, content)` flow:
1. MCP → `POST /projects/{id}/stage-artifact-url` with the target `path`.
2. Project API:
   - Authenticates (Cognito JWT → verified role) and reads the record (DynamoDB GetItem).
   - **Owner-gate** → HTTP 403 if the caller's role ≠ the project's `owning_role`.
   - Validates `path` is one of the **active stage's `produces`** paths (from the
     registry + `current_stage`) → HTTP 400 if off-contract.
   - Builds the S3 key `{slug}/{path}` and mints a short-TTL (~300s) **SigV4**
     presigned PUT for that exact key; returns `{upload_url, key, expires_in}`.
3. MCP performs the HTTP PUT of `content` directly to S3 (bypasses API/Lambda
   payload limits).
4. **Write authority** is the Project API Lambda role, whose `s3:PutObject` is
   scoped to `{*}/aidlc-docs/*` (the artifact tree); the per-request `produces`
   check narrows each URL to the exact declared key — so a client can never write
   outside its stage contract, and never holds broad S3 write creds.
5. Artifact metadata (`submitted_by`/`submitted_at`/`etag`) is recorded at
   **`complete_stage`** (which HeadObjects the declared `produces`), keeping
   `submit_artifact` stateless and idempotent — re-submitting overwrites the same
   key; S3 versioning retains history.

**Why presigned:** RBAC stays server-side (owner-gated URL minting), large
artifacts stream directly to S3, and the `produces` allowlist bounds writes. It
reuses the proven `upload-url` pattern (SigV4 forced so a client-added
`Content-Type` cannot break the signature).

**Alternative (accepted for small text):** `complete_stage` could accept the
markdown inline and the API writes it — one fewer round-trip, but bounded by
API/Lambda payload limits and routes content through the control plane. Presigned
is the default; inline is acceptable for tiny artifacts if preferred.

### 3.11 Handoff_state ↔ stage cursor mapping

There are **two granularities**, and the design keeps both:
- **`handoff_state`** — the coarse, RBAC-bearing lifecycle state (the full list in
  R4.3). It determines ownership and appears in queues.
- **`current_stage`** — the fine-grained cursor identifying which deliverable
  within the current segment the persona is producing (a segment can have more
  stages than states, e.g. BA has 4 stages but the BA-owned states are
  `draft`/`requirements_ready`).

Mapping mechanism (registry-driven):
- A stage MAY declare an optional **`advances_state`**. WHEN that stage completes,
  `complete_stage` also advances `handoff_state` **within the same owner** via
  `validate_transition` (e.g. SA stages walking `assigned_to_sa → in_design →
  design_ready`). Stages without `advances_state` only move the cursor.
- Completing the **segment-terminal stage** leaves the project in the segment's
  terminal *owned* state (e.g. `requirements_ready` for BA, `design_ready` for
  SA) — "ready to hand off," still owned by the same persona.
- The **cross-persona handoff** (ownership transfer, e.g.
  `requirements_ready → assigned_to_sa`) is an **explicit** persona action via
  **`handoff_project`** — so the persona can do a final review before assigning
  to the next role. This is why `handoff_project` is retained (§3.2).
- `return_for_changes` performs the registry `request_changes` bounce and resets
  the cursor to the appropriate stage of the target segment.

This preserves the entire R4.3 state machine unchanged while adding the cursor for
intra-segment sequencing.

## 4. Access-pattern → index map

| Need (requirement) | Query |
| --- | --- |
| List all projects for selection (R3) | GSI2 `ENTITY#PROJECT` by `updated_at` (paginated) |
| "Assigned to my role" queue (R3.4, R12) | GSI1 `ROLE#{role}` |
| Read one project + cursor (R3.3, R6.4) | GetItem `PROJECT#{uuid}` / `META` |
| Advance/handoff with concurrency (R2.4, R5, R6.3) | TransactWrite conditional on `version`+`owning_role` |
| Cross-project status (R14) | GSI3 `STATE#{state}` |
| Audit/history (R14.1) | Query `PROJECT#{uuid}` `HIST#` |

## 5. API & tool contracts (essentials)

**`GET /projects/{id}/stage-brief` → StageBrief**
```json
{ "stage":"requirements_analysis","segment":"ba_segment","owning_role":"requirements-analyst",
  "generator":"kiro","skill":"requirements-analysis",
  "consumes":[{"path":"aidlc-docs/inception/requirements/uploaded/**"}],
  "produces":[{"path":"aidlc-docs/inception/requirements/requirements.md","required":true,
               "acceptance":["has_sections:Functional,NonFunctional","min_words:300"]}],
  "approval_required":true }
```
403 if not owner; 409/404 as applicable.

**`POST /projects/{id}/stage-artifact-url` {path} → {upload_url, key, expires_in}**
- `path` must be in the active stage `produces` (else 400); owner-gated (403);
  short TTL presigned PUT.

**`POST /projects/{id}/complete-stage` {note?} → CompleteResult**
```json
{ "status":"advanced", "warnings":[{"path":"...","rule":"min_words:300","message":"..."}],
  "current_stage":"user_stories" }         // or "handoff":{"to_state":"assigned_to_sa","owning_role":"system-architect"}
```
- Owner-gate (403) → validate (lenient: warnings only, except missing required →
  `status:"blocked"`, no advance) → advance cursor or apply handoff (422 if
  illegal) → append history (+ warnings) atomically.

## 6. Sequence flows

**6.1 Selection (R3):** persona → `list_projects` (GSI2 broad list + GSI1 role
queue for "mine") → `get_project(id)` (state + `current_stage`).

**6.2 BA stage generation (R7):**
```
get_stage_brief → get_artifact(consumes) → Kiro generates via Skill
→ submit_artifact(produces path)  (presigned PUT, owner-gated)
→ complete_stage → validate(lenient) → advance cursor to next BA stage
```

**6.3 Segment handoff (R6.3):** completing intra-segment stages advances the owned
coarse state to the segment-terminal state (e.g. `design_ready`), still owned by
the persona. The persona then explicitly `handoff_project` → `validate_transition`
transfers ownership (e.g. `design_ready → ready_for_dev` / to the next segment's
entry state) via a `TransactWrite` → project appears in the next persona's role
queue (GSI1).

**6.4 Request-changes (R4.4):** reviewer `return_for_changes` → registry
`request_changes` edge → state bounces one step back; cursor reset to the target
segment's relevant stage; receiving persona regenerates + `complete_stage`.

**6.5 Git round-trip (R11):** Dev `link_repo` → `submit_implementation` → ingest
CodeBuild → callback advances to QA; QA `run_tests` → test CodeBuild → callback
records results.

## 7. Acceptance validation (lenient v1)
- Validators keyed by name (`has_sections:...`, `min_words:N`, `nonempty`,
  `valid_markdown`, extensible). Pure, server-side.
- **v1 (lenient):** rule failures → `warnings[]` returned + recorded in history;
  advancement proceeds. **Missing/empty `required` artifact → hard block** (no
  advance). Unknown validator → warn.
- Mode flag (`ACCEPTANCE_MODE=lenient|strict`) allows switching to blocking later
  with no code change.

## 8. Security
- RBAC unchanged: verified Cognito group → owner-gate 403; illegal transition 422;
  conditional write 409.
- Writes only via owner-gated, `produces`-constrained presigned PUTs (no broad S3
  write from clients).
- Secrets by reference (Secrets Manager); Kiro config from stack outputs; no
  secrets in client config/logs.
- Removing the Gateway/Runtime shrinks the attack surface (no M2M client, no
  runtime endpoint).

## 9. Observability
- Append-only `HIST#` items (transitions + stage completions + validation
  warnings) → audit.
- `current_stage`/`handoff_state` on `META` + GSI3 → cross-project status and
  stage-dwell/SLA reporting (feeds the observability track).
- Maintenance persona: read-only queries over GSI2/GSI3.

## 10. Migration plan
1. **Registry:** add `generator`/contract fields (3 synced copies) + `validate()`.
2. **DynamoDB:** add the table + GSIs (CDK); implement the store behind the
   Project API record functions; conditional-write concurrency.
3. **Project API:** add `stage-brief`, `stage-artifact-url`, `complete-stage`,
   `current_stage` cursor, acceptance validators (lenient).
4. **MCP:** add `get_stage_brief`/`submit_artifact`/`complete_stage`; remove
   `run_stage`/WS bridge + runtime config.
5. **Skills:** author stage Skills (start with BA `requirements_analysis`).
6. **Remove AgentCore:** delete Runtime/Gateway/Memory/M2M-client/agent-build from
   CDK; **delete `code/agentcore-runtime/` outright**.
7. **Pilot** BA `requirements_analysis` end to end, then roll forward stage by
   stage; keep the Git round-trip throughout.
8. **Greenfield:** no data migration — start with a fresh DynamoDB table (no
   existing project data to preserve).

## 11. Testing strategy
- **Unit:** registry `validate()` (new fields); each acceptance validator;
  `complete-stage` advance/handoff + lenient warnings; presigned path-guard.
- **Project API:** DynamoDB conditional writes / 409; owner-gate 403; illegal 422;
  cursor advance; GSIs (moto or a local DynamoDB).
- **MCP:** new tools against fakes (extend `code/kiro-mcp/tests/`); confirm the
  WS bridge/`run_stage` removal doesn't break registration.
- **E2E:** scripted BA→SA handoff with Kiro generation faked by fixtures; Git
  round-trip regression.

## 12. Risks / trade-offs
- **Headless generation is gone** — Kiro-generated stages need a human session.
  Acceptable per Model 1 (no `agentcore` stages remain); control-plane automation
  (ingest/tests/webhook) is unaffected.
- **Generation consistency now depends on Kiro's model + Skills** — mitigated by
  versioned Skills + recorded acceptance warnings.
- **DynamoDB adoption** touches the Project API record layer and CDK; scoped and
  testable. Greenfield — no data migration needed.
- **Lenient validation** can let weak artifacts through — accepted for v1;
  warnings recorded; strict mode available later.

## 13. Requirements traceability
| Requirement | Design coverage |
| --- | --- |
| R1 Kiro generates, no AgentCore | 3.1, 3.2, 3.9 |
| R2 Shared store | 3.3, 3.5 |
| R3 Selection (as-is) | 3.5 GSI2/GSI1, 6.1 |
| R4 Registry state machine | 3.4, 6.3/6.4 |
| R5 RBAC 403/422 (as-is) | 3.3, 8 |
| R6 Stage contract + cursor | 3.4, 3.3, 5 |
| R7 Generation flow | 3.2, 5, 6.2 |
| R8 Acceptance (lenient) | 7 |
| R9 Artifact storage | 3.6 |
| R10 Auth | 3.7 |
| R11 Git round-trip | 3.8, 6.5 |
| R12 Multi-user (claim deferred) | 3.5 (role queue, conditional writes) |
| R13 DynamoDB | 3.5, 4 |
| R14 Observability/audit | 3.5 HIST, 9 |
