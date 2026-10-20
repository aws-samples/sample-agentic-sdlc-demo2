# Technical Design: Kiro-Generated Stage Artifacts

**Status:** Implemented — **Kiro generates all deliverables; AgentCore removed entirely**
**Author:** (draft)
**Related:** `docs/architecture-agentcore-vs-kiro.md`, `.kiro/steering/20-workflow-registry.md`, `.kiro/specs/kiro-native-platform/`

> **Implemented reconciliation:** This design has shipped, and it went **further
> than "orchestration only."** AgentCore has been **removed entirely** — no
> Runtime, Gateway, or Memory. Orchestration (the registry-driven handoff state
> machine, segment sequencing, the stage cursor, and RBAC) now lives in the
> **serverless Project API** backed by **Amazon DynamoDB** (`AidlcProjects`), with
> artifacts + ingested source in S3. **Kiro generates every deliverable** using
> its own agent, model, and per-stage Skills; `run_stage` / server-side
> generation is gone. See the [`kiro-native-platform` spec](../../.kiro/specs/kiro-native-platform/)
> for the implemented requirements/design/tasks. The sections below describe the
> original Option B (AgentCore-as-orchestrator) framing; substitute
> "Project API + DynamoDB" wherever they say "AgentCore orchestrates."

---

## 1. Summary

Today the AgentCore Runtime both **orchestrates** the multi-persona SDLC workflow
*and* **generates** every stage artifact (via `run_stage`, which drives a
server-side Strands stage agent that calls the model and writes artifacts to S3).

This design **keeps AgentCore as the orchestrator and workflow/RBAC authority**
but **moves artifact generation into Kiro**, so each persona produces their
deliverable using Kiro's own agent, model, and Skills. The goal is to make Kiro
central to every persona's work (requirements, design, test plans, deployment
runbooks — not just the developer's code), increasing Kiro adoption while
preserving the shared, governed, multi-persona backend.

The mechanism: decouple "decide the next stage + validate + advance"
(stays on the backend) from "produce the artifact content" (moves to Kiro), and
make the choice **pluggable per stage** via a `generator: kiro | agentcore`
flag in the workflow registry, so migration is incremental and headless stages
are retained.

## 2. Goals / Non-goals

**Goals**
- Generate document/analysis stage artifacts in Kiro (Kiro's model + Skills).
- Preserve AgentCore's orchestration: intra-segment stage sequencing, handoff
  gates, `skip_completed`, and the human-in-the-loop approval model.
- Preserve the authoritative handoff state machine + RBAC (Project API 403/422).
- Keep artifacts in the same shared S3 locations so downstream personas are
  unaffected.
- Add server-side **acceptance validation** so client-generated artifacts still
  pass quality gates.
- Support incremental migration (per-stage flag) and retain `run_stage` for
  stages that must stay server-side/headless.

**Non-goals**
- Changing the workflow state machine, personas, or RBAC model.
- Moving control-plane automation (Git ingest/test callbacks, webhooks, SLA
  evaluators) — these are non-generative and stay server-side.
- Replacing AgentCore (Gateway, Memory, Identity remain).
- Changing where the developer writes code (already Kiro/local).

## 3. Background: how `run_stage` works today

Active stages (from `.kiro/steering/00-project-overview.md`):
- **BA:** `workspace_detection`, `reverse_engineering`, `requirements_analysis`, `user_stories`
- **SA:** `workflow_planning`, `nfr_requirements`, `nfr_design`, `application_design`, `units_generation`, `inception_complete`
- **Dev:** `construction_handoff`
- **QA:** `test_planning`, `test_execution` (CodeBuild), `qa_report`
- **DevOps:** `deployment_planning`, `release`

Current flow:
```
Kiro persona → MCP run_stage → WebSocket (Cognito access token)
   → AgentCore Runtime (main_ws.py) → Strands GraphBuilder (orchestrator.py)
      → stage agent invokes the model, writes artifacts to S3 via Gateway
        file-operations tools (aidlc-docs/…)
      → emits interrupts (approval gate / question / handoff)
   → streamed back over WebSocket; answered via answer_prompt
```
Key components:
- **Orchestrator** — `code/agentcore-runtime/icode-agent/orchestrator.py`
  (`build_sdlc_graph`), partitioned by `segments()`, with
  `handoff_gate__{seg}` boundaries and `skip_completed` for upstream segments.
- **Session/state** — `main_ws.py` uses `S3SessionManager` to persist graph
  execution state (completed nodes, next nodes) to S3; resumable.
- **Registry** — `workflow_registry.json` (+ `.py`) is the single source of
  truth for personas, segments, states, transitions (three synced copies).
- **State machine / RBAC** — `cdk/custom-resources/project_api.py`
  (`validate_transition`, owner-gate 403, illegal-transition 422).
- **MCP bridge** — `code/kiro-mcp/aidlc_mcp/agentcore_ws.py`
  (`AgentCoreSession`, `Turn.status ∈ {awaiting_approval, awaiting_input,
  complete, handoff, error, closed}`) and tools in `tools.py`
  (`run_stage`, `answer_prompt`, `stage_status`, `close_stage`).

## 4. Proposed design

### 4.1 Per-stage generator flag (registry)
Add an optional `generator` to each stage in the registry (defaults to
`agentcore` for backward compatibility):
```json
"requirements_analysis": {
  "segment": "ba_segment",
  "generator": "kiro",
  "produces": [
    { "path": "aidlc-docs/inception/requirements/requirements.md",
      "required": true,
      "acceptance": ["min_words:300", "has_sections:Functional,NonFunctional,Assumptions"] }
  ],
  "consumes": [
    "aidlc-docs/inception/requirements/uploaded/**",
    "source/**"
  ],
  "skill": "requirements-analysis",
  "approval_required": true
}
```
- `generator: kiro` → the artifact is produced in Kiro; the backend orchestrates + validates.
- `generator: agentcore` → today's `run_stage` behavior (unchanged, headless-capable).
- `produces` / `consumes` / `acceptance` / `skill` define the **stage contract**
  used by the brief, the validator, and the Skill.

Must be applied to all three synced registry copies (runtime image, Project API
Lambda asset, Kiro TS mirror) per `20-workflow-registry.md`.

### 4.2 Two orchestration options (decision for review)

Because the user requirement is to *keep the orchestrator*, we present two
architectures and a recommended hybrid.

**Option A — Orchestrator-in-the-loop (Strands graph stays in charge).**
For a `kiro` stage, the graph node does not call the model; it emits a new
interrupt `generation_required` carrying the stage brief, then the session is
**persisted and stopped** (state already lives in S3 via `S3SessionManager`).
Kiro generates locally and submits the artifact; the submission **resumes** the
session, which validates the artifact and continues the graph to the next node
(next stage or `handoff_gate__{seg}`).
- *Pros:* preserves the full orchestrator (intra-segment sequencing, gates,
  `skip_completed`) with one code path; reuses the interrupt/streaming model.
- *Cons:* more moving parts (resume-on-submit); still tied to the Runtime for
  sequencing.

**Option B — State-machine-driven (bypass Runtime for `kiro` stages).**
Track a **stage cursor** on the project record; the Project API + registry drive
intra-segment sequencing directly. Three MCP tools (§4.3) do brief → submit →
complete; `complete_stage` validates and advances the cursor, and on the last
stage of a segment triggers the handoff transition. The Runtime is invoked only
for `agentcore` stages.
- *Pros:* no held/ös Runtime session during human generation (big win at 4,000-user
  scale — see `docs/SCALING.md`); simplest client contract; least server cost.
- *Cons:* intra-segment sequencing logic moves from the Strands graph into the
  Project API/registry (the graph remains authoritative only for `agentcore`
  stages and cross-segment structure).

**DECIDED: Option B.** All active document/analysis stages are `generator: kiro`
and are driven by the state-machine path (§4.3). AgentCore is used **purely for
orchestration** — the registry-driven handoff state machine, segment sequencing,
and RBAC — and no longer invokes the model for these stages. The Strands graph /
`run_stage` path is retained **only** as an optional escape hatch for any stage
that must run unattended/headless (there are none among the current active
generative stages; `test_execution` remains the CodeBuild runner and is
unaffected). The handoff gates and segment structure remain registry-defined and
identical regardless of path.

*(Option A — orchestrator-in-the-loop with a `generation_required` interrupt and
resume-on-submit — was considered and set aside because holding/resuming a
Runtime session during human generation adds cost and session pressure at scale,
per `docs/SCALING.md`. It remains documented as the fallback if a future stage
must be orchestrated server-side.)*

### 4.3 New MCP tools (Kiro-facing)

Added to `code/kiro-mcp/aidlc_mcp/tools.py` + registered in `server.py`.

**`get_stage_brief(project_uuid) -> StageBrief`**
Returns the active stage's work order (computed from the registry + the record's
current segment/stage cursor):
```json
{
  "stage": "requirements_analysis",
  "segment": "ba_segment",
  "owning_role": "requirements-analyst",
  "display_name": "Requirements Analysis",
  "generator": "kiro",
  "consumes": [ { "path": "...", "description": "..." } ],
  "produces": [ { "path": "...", "required": true, "acceptance": [...] } ],
  "skill": "requirements-analysis",
  "approval_required": true
}
```
- 403 if caller is not the owning role (delegates to Project API RBAC).
- Error if the active stage's `generator` is `agentcore` (use `run_stage`).

**`submit_artifact(project_uuid, path, content, content_type="text/markdown") -> {ok, key, etag}`**
- Path must be one of the active stage's declared `produces` paths (rejected
  otherwise — prevents writing outside the stage contract).
- Writes to `s3://{files_bucket}/{slug}/{path}` via the MCP server's existing
  S3 access, authenticated as the persona.
- Idempotent per path; supports multiple `produces` via repeated calls.

**`complete_stage(project_uuid, note?) -> CompleteResult`**
- Calls a new Project API endpoint (`POST /projects/{id}/complete-stage`) that:
  1. Owner-gates (403).
  2. Runs **acceptance validation** on the declared `produces` (see §4.5); on
     failure returns `{status:"rejected", validation:[...]}` and does **not**
     advance.
  3. Advances the **stage cursor** to the next stage in `segment.stages`; if the
     segment's stages are exhausted, applies the **handoff/forward transition**
     through `validate_transition` (registry authority).
  4. Returns the new state (next stage, or handoff to next segment).
- `approval_required` stages: the human's act of calling `complete_stage` *is*
  the approval (they reviewed in Kiro), recorded in history.

`run_stage` / `answer_prompt` / `stage_status` / `close_stage` remain for
`agentcore` stages.

### 4.4 Skills: migrating stage prompts into Kiro

Each `kiro` stage's server-side system prompt becomes a **versioned Skill**:
- Location: `.kiro/skills/<persona>/<stage>.md` (e.g.
  `.kiro/skills/business-analyst/requirements-analysis.md`), referenced by the
  persona agent and named in the registry stage's `skill` field.
- Content: the stage's role/goal, the exact inputs to read (`consumes`), the
  required output structure/sections (matching `acceptance`), quality guidance,
  and the write path(s). Effectively the stage agent's prompt, reformatted as a
  Kiro playbook.
- Distribution: shipped in the repo / Power so every user runs the same
  governed prompt (consistency across the org).
- The persona agent workflow becomes: `get_stage_brief` → (Kiro reads
  `consumes` via `get_artifact`) → generate per the skill → `submit_artifact` →
  `complete_stage`.

### 4.5 Acceptance validation (server-side quality gate)

Because content is now produced client-side, `complete-stage` validates before
advancing. Validators are declarative (`acceptance` list) and pure/server-side:
- Structural: `required` artifact exists and non-empty; `has_sections:...`;
  `min_words:N`; valid markdown/JSON where applicable.
- Coverage (where feasible): e.g. every requirement id in `requirements.md` is
  referenced by at least one story in `stories.md` (for `user_stories`).
- Extensible: a validator registry keyed by name; unknown validators fail closed
  in strict mode or warn in lenient mode (configurable per environment).
- Failures return actionable messages; the stage stays open for the persona to
  fix and resubmit.

This preserves the governance the server-side agents implicitly provided.

## 5. Data model changes

**Registry (per stage):** `generator`, `produces[]`, `consumes[]`, `acceptance[]`,
`skill` (§4.1). `validate()` closure extended to check these are well-formed.

**Project record (`_platform/projects/{uuid}.json`):**
- `current_stage` — the active stage within the current segment (cursor).
- `stage_status` — `{stage: "in_progress"|"submitted"|"accepted"}`.
- `artifact_manifest` — optional list of produced artifacts with `submitted_by`,
  `submitted_at`, `etag` (audit).
- `assignment_history` continues to record stage completions (actor = persona).

**Project API:** new `POST /projects/{id}/complete-stage` route
(Cognito-authorized, owner-gated), plus a read that exposes `current_stage` in
`get_project`.

## 6. End-to-end sequences

### 6.1 A Kiro-generated stage (Option B)
```
BA persona (Kiro): "generate the requirements"
  → get_stage_brief(project)              # brief for requirements_analysis
  → get_artifact(uploaded reqs, source)   # read consumes
  → (Kiro model + requirements-analysis Skill generate requirements.md)
  → submit_artifact(project, ".../requirements.md", content)
  → complete_stage(project)
        → Project API: owner-gate → validate acceptance → advance cursor
          → next stage user_stories (still BA) OR, if last, handoff gate
  → agent tells the user the result / next step
```

### 6.2 Segment boundary / handoff
When `complete_stage` is called on the **last** stage of a segment
(`segment.stages[-1]`), the Project API applies the forward transition
(`validate_transition(current, forward(current))`), moving ownership to the next
segment's role (e.g. `assigned_to_sa`). The receiving persona then sees it via
`list_projects`. Identical to today's handoff semantics.

### 6.3 Request-changes (bounce)
Unchanged: a reviewer calls `return_for_changes`; the record bounces one step
back per the registry `request_changes` edge; the receiving persona regenerates
the relevant stage(s) in Kiro and re-completes.

## 7. Security considerations
- **RBAC unchanged and authoritative:** `get_stage_brief`, `submit_artifact`,
  and `complete-stage` all owner-gate via the Project API (403). The MCP server
  writes as the authenticated persona.
- **Contract-bounded writes:** `submit_artifact` only accepts declared `produces`
  paths, preventing arbitrary writes into the artifact tree.
- **Quality gate:** server-side acceptance validation prevents junk artifacts
  from advancing the workflow.
- **Audit:** `artifact_manifest` records who/what/when for every submission.
- **Untrusted content:** downstream stages treat prior artifacts as data, not
  instructions.
- **Secrets/config:** unchanged (Secrets Manager, stack outputs).

## 8. Observability
- Emit metrics on `complete-stage`: acceptance pass/fail, per-stage dwell (feeds
  `docs/OBSERVABILITY`-style SLAs), rework counts.
- Log validation failures with reasons for coaching/skill improvement.
- Track `generator` per stage so dashboards show Kiro-generated vs server-generated.

## 9. Trade-offs / what's at stake
- **Headless generation is lost for `kiro` stages** — they require a human's Kiro
  session. Keep any stage that must run unattended as `generator: agentcore`.
  (Control-plane automation is non-generative and unaffected.)
- **Generation consistency now depends on Kiro's model + the Skill** — mitigated
  by shipping Skills as versioned, governed artifacts and by server-side
  acceptance validation.
- **Intra-segment sequencing moves to the state machine (Option B)** — the
  Strands graph is no longer the sole sequencer; the registry cursor is. Net
  simpler and cheaper, but a real change to reason about.
- **Runtime cost/session usage drops** for moved stages (aligns with the goal and
  with the 4,000-user scaling posture); Bedrock usage shifts to Kiro.

## 10. Failure modes & edge cases
- **Partial submission:** `complete_stage` fails if a `required` produce is
  missing; stage stays open.
- **Validation failure:** returns errors; no advance; persona fixes + resubmits.
- **Concurrent submit (same project):** ETag-conditional writes on the record;
  loser gets 409 and refetches (existing pattern).
- **Wrong persona:** 403 at any of the three tools.
- **`agentcore` stage reached via Kiro tools:** `get_stage_brief` returns a clear
  "use run_stage" error.
- **Registry drift:** the byte-identical-copies assertion + `validate()` closure
  guard the new fields.

## 11. Migration plan (incremental)
1. **Registry:** add `generator`/contract fields (default `agentcore`); sync 3 copies; extend `validate()` + the CDK assertion test.
2. **Project API:** add `current_stage` cursor, `complete-stage` route, and the acceptance validators.
3. **MCP:** add `get_stage_brief` / `submit_artifact` / `complete_stage`; keep `run_stage`.
4. **Skills:** author the first stage's Skill (`requirements-analysis`).
5. **Pilot:** flip **BA `requirements_analysis`** to `generator: kiro`; validate quality + gate end-to-end against a test project.
6. **Roll forward** stage-by-stage (user_stories → SA design stages → QA → DevOps), leaving `test_execution` and control-plane paths server-side.
7. **Orchestrator:** for Option B, make `kiro` stages no-ops in the Strands graph (skip), or (Option A) implement the `generation_required` interrupt + resume-on-submit.

## 12. Testing strategy
- **Unit:** registry `validate()` with new fields; each acceptance validator;
  `submit_artifact` path-guarding; `complete-stage` advance/gate logic.
- **Project API:** owner-gate + validation + cursor-advance + handoff transition
  (extend `cdk/custom-resources/tests/`).
- **MCP:** new tools against in-memory fakes (extend `code/kiro-mcp/tests/`).
- **E2E:** a scripted BA→SA handoff where BA stages are Kiro-generated (fake the
  Kiro generation with fixture content) — mirrors the existing e2e harness.
- **Regression:** an `agentcore`-generator stage still runs via `run_stage`.

## 13. Rollout & flags
- The `generator` flag *is* the feature flag (per stage, per deployment).
- Ship with everything `agentcore` (no behavior change), then flip stages via a
  registry update + redeploy.
- Strict vs lenient acceptance validation configurable per environment.

## 14. Affected components
- `workflow_registry.json` (+ `.py`) — 3 synced copies; `validate()`.
- `cdk/custom-resources/project_api.py` — `complete-stage`, cursor, validators.
- `cdk/lib/aidlc-platform-agents.ts` — new route wiring; CDK assertion test.
- `code/agentcore-runtime/icode-agent/orchestrator.py`, `shared_state.py`,
  `main_ws.py` — skip/`generation_required` handling for `kiro` stages.
- `code/kiro-mcp/aidlc_mcp/tools.py`, `server.py` — new tools.
- `.kiro/skills/<persona>/<stage>.md`, `.kiro/agents/*.md` — stage Skills + tool wiring.
- `code/ui-frontend` — n/a (removed).

## 15. Alternatives considered
- **Keep all generation on AgentCore (status quo):** rejected — does not increase
  Kiro adoption; leaves coding as the only Kiro-native step.
- **Move orchestration to Kiro too:** rejected — loses shared project, RBAC,
  headless automation, scale (see `docs/architecture-agentcore-vs-kiro.md`).
- **Option A everywhere (orchestrator-in-the-loop for all stages):** viable;
  higher fidelity to the current graph but holds Runtime sessions during human
  generation (worse at scale). Offered as the alternative if review prefers it.

## 16. Open questions for review
1. ~~Option A vs B vs the recommended hybrid — which orchestration model?~~
   **RESOLVED: Option B** — AgentCore orchestration-only; Kiro generates all
   deliverables (see Decision at top and §4.2).
2. How strict should acceptance validation be at launch (block vs warn)?
3. Do we need per-stage **coverage** validators (e.g. requirement→story) in v1,
   or structural checks only?
4. Should `current_stage` also be surfaced to the Maintenance dashboard now?
5. Governance: who owns/updates the stage Skills, and how are changes reviewed?
