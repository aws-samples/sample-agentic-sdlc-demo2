# Requirements — Kiro-Native Agentic SDLC Platform (Model 1)

## Introduction

This spec defines a **Kiro-native** realization of the Agentic SDLC platform in
which **Kiro (persona agents + skills) generates every deliverable**, and a
**thin, serverless shared backend** (Project API + storage + Cognito) provides
the one thing Kiro cannot: an authoritative, multi-user **system of record** for
projects, workflow state, RBAC, and artifacts. **Amazon Bedrock AgentCore is not
used** (no Runtime, Gateway, or Memory) in the active path.

This is "Model 1" from the architecture debate (`docs/architecture-agentcore-vs-kiro.md`,
`docs/design/kiro-generated-artifacts.md`):
- **Generation → Kiro** — each persona produces their stage artifacts using
  Kiro's agent, model, and Skills.
- **Orchestration & state → serverless backend** — the workflow registry state
  machine, handoff/RBAC, project selection, and artifact storage live in the
  Project API + storage, reachable by every user.
- **No AgentCore** — the Strands Runtime, Gateway, and Memory are removed from
  the active generation path.

The design must keep multi-persona collaboration on one shared project with
governed handoffs, and must make **project selection reliable** ("which project
am I working on") for every persona.

### Definitions
- **Persona** — one of six roles: Business Analyst (`requirements-analyst`),
  Solution Architect (`system-architect`), Software Developer
  (`software-developer`), QA Engineer (`qa-engineer`), DevOps Engineer
  (`devops-engineer`), Maintenance Specialist (`maintenance-specialist`,
  read-only).
- **Project** — a unit of work with a lifecycle state, an owning role, artifacts,
  and history; the authoritative record lives in the shared backend.
- **Segment / Stage** — a persona owns a workflow segment composed of ordered
  stages; each stage produces defined artifacts.
- **Stage contract** — the registry-declared `produces` / `consumes` /
  `acceptance` / `skill` for a stage.
- **Shared backend** — the serverless Project API + record store + artifact store
  + Cognito; the system of record. Not AgentCore.

### Resolved decisions (reviewer-approved)
1. **Project selection** — unchanged from the current implementation:
   broad-visibility listing with an ownership indicator; ownership is **role-level**.
2. **RBAC** — unchanged from the current implementation: **HTTP 403** for
   non-owning callers, **HTTP 422** for illegal transitions.
3. **Record store** — **Amazon DynamoDB** is the chosen record store (designed in
   from the start, behind the Project API).
4. **Acceptance validation** — **lenient in v1**: validators run and emit **clear
   warnings**, but do **not** block advancement (a stage with a missing *required*
   artifact is the one hard precondition). Strict (blocking) mode is configurable
   for later.
5. **Per-user assignment/claim (R12)** — **deferred**; v1 keeps role-level
   ownership with conflict protection (matches current selection behavior).

---

## Requirement 1 — Kiro generates all deliverables (no AgentCore)

**User story:** As a persona, I want to generate my stage's deliverables using
Kiro's own agent and Skills, so that Kiro is the tool I use for the actual work
and there is no dependency on a server-side generation engine.

**Acceptance criteria:**
1. WHEN a persona begins a stage, THEN the system SHALL provide the stage's brief
   (its `produces`, `consumes`, `acceptance`, and guiding `skill`) so Kiro can
   generate the artifact locally.
2. WHEN a persona generates a stage artifact, THEN generation SHALL be performed
   by Kiro (its agent + model + Skill), NOT by an AgentCore Runtime stage agent.
3. The active generation path SHALL NOT invoke AgentCore Runtime, Gateway, or
   Memory.
4. WHERE a stage's generation logic exists today as a server-side prompt, THE
   system SHALL express that logic as a versioned Kiro Skill distributed with the
   project.

## Requirement 2 — Authoritative shared project store

**User story:** As any persona, I want a single shared source of truth for
projects and their state, so that all personas and users see and act on the same
project consistently.

**Acceptance criteria:**
1. THE system SHALL persist each project's record (state, owning role, stage
   cursor, artifact references, history) in a shared backend store, not on any
   individual workstation.
2. WHEN any authorized persona reads a project, THEN they SHALL observe the same
   current state, owner, and stage as every other persona.
3. WHEN a persona changes a project's state or artifacts, THEN the change SHALL
   be durably recorded in the shared store before it is considered applied.
4. THE record store SHALL support concurrent access with lost-update protection
   (conditional writes), returning a conflict error on a stale write.

## Requirement 3 — Reliable project selection

**User story:** As a persona, I want to reliably choose which project I am working
on, so that I never act on the wrong project or lose track of my work.

**Acceptance criteria:**
1. WHEN a persona requests their projects, THEN the system SHALL return the list
   of projects from the shared store with each project's name, id, current state,
   owning role, and an ownership indicator (mine / awaiting-me / other).
2. THE system SHALL identify each project by a stable, unique identifier that is
   safe to use in storage keys (constrained character set).
3. WHEN a persona selects a project by id, THEN all subsequent actions in that
   session SHALL apply to that project until they select a different one.
4. WHEN a project has been handed off to the persona, THEN it SHALL appear in
   that persona's queue as "awaiting-me" / "mine".
5. THE system SHALL support listing/filtering projects such that selection remains
   reliable as the number of projects grows (no unbounded full scan required for
   routine selection).

## Requirement 4 — Registry-driven handoff state machine

**User story:** As the organization, I want the workflow (personas, states,
transitions) defined in one place, so that the process is consistent and
extensible without code changes.

**Acceptance criteria:**
1. THE workflow (personas, segments, stages, states, forward transitions,
   request-changes transitions) SHALL be defined in a single workflow registry
   that is the source of truth.
2. WHEN a project advances, THEN the target state SHALL be resolved through the
   registry (forward or request-changes), never by ad-hoc literals.
3. THE system SHALL support the forward lifecycle
   `draft → requirements_ready → assigned_to_sa → in_design → design_ready →
   ready_for_dev → assigned_to_dev → implementation_submitted → assigned_to_qa →
   in_qa → qa_passed → assigned_to_devops → in_deployment → deployed`.
4. THE system SHALL support request-changes bounces: SA→BA, Dev→SA, QA→Dev,
   DevOps→QA, as declared in the registry.
5. WHERE registry copies exist in more than one place, THE system SHALL keep them
   consistent and verify they do not drift.

## Requirement 5 — Server-enforced RBAC and transition legality

**User story:** As the organization, I want role-based access enforced by the
backend, so that a persona can only act when it is genuinely their turn and
cannot bypass governance from the client.

**Acceptance criteria:**
1. WHEN a persona attempts a state-changing action on a project, THEN the backend
   SHALL verify the caller's role (from a verified identity claim) matches the
   project's current owning role, and SHALL reject non-owners with HTTP 403.
2. WHEN a requested transition is not legal per the registry, THEN the backend
   SHALL reject it with HTTP 422 and leave the record unchanged.
3. THE RBAC decision SHALL be made server-side using an unforgeable identity
   claim (not client-supplied role).
4. THE read path (listing/viewing projects and artifacts) SHALL be available to
   authorized personas without granting write/transition rights.

## Requirement 6 — Stage contract and stage cursor

**User story:** As a persona, I want the backend to know exactly which stage I am
on and what it must produce, so that generation and validation are well-defined
and the workflow advances correctly.

**Acceptance criteria:**
1. THE registry SHALL declare, per stage: `produces` (artifact paths + required
   flag + acceptance rules), `consumes` (input artifact paths), a `skill`
   reference, and a `generator` value (`kiro` for Model 1 stages).
2. THE backend SHALL track a per-project stage cursor identifying the active
   stage within the current segment.
3. WHEN a stage completes, THEN the backend SHALL advance the cursor to the next
   stage in the segment; WHEN the last stage of a segment completes, THEN the
   backend SHALL apply the segment's handoff transition through the registry.
4. WHEN a persona requests the current stage brief, THEN the backend SHALL derive
   it from the registry and the project's cursor.

## Requirement 7 — Kiro generation flow (brief → generate → submit → complete)

**User story:** As a persona, I want a clear flow to fetch my task, generate it in
Kiro, submit it, and advance the workflow, so that producing deliverables is
consistent and governed.

**Acceptance criteria:**
1. THE system SHALL provide a way to fetch the current stage brief for a selected
   project (Requirement 6.4).
2. THE system SHALL provide a way to submit a generated artifact for the active
   stage; submission SHALL be constrained to the stage's declared `produces`
   paths and SHALL reject any other path.
3. THE system SHALL provide a way to mark the stage complete, which SHALL run
   acceptance validation and, on success, advance the cursor / apply the handoff
   (Requirement 6.3).
4. WHEN a *required* artifact is missing/empty, THEN the system SHALL NOT advance
   (hard precondition). WHEN other acceptance rules fail in v1 (lenient mode),
   THEN the system SHALL advance but SHALL return clear warnings for the persona.
5. WHERE a stage requires human approval, THE persona's act of completing the
   stage SHALL constitute that approval and SHALL be recorded.

## Requirement 8 — Acceptance validation as a quality gate

**User story:** As the organization, I want submitted artifacts validated by the
backend before the workflow advances, so that client-side generation still meets
quality standards.

**Acceptance criteria:**
1. WHEN a stage is completed, THEN the backend SHALL validate that all `required`
   artifacts for the stage exist and are non-empty.
2. THE backend SHALL evaluate the stage's declared `acceptance` rules
   (e.g. required sections, minimum content, structural/format checks). In v1
   (lenient mode) rule failures SHALL be surfaced as **clear warnings** and SHALL
   NOT block advancement; the mode SHALL be configurable to strict (blocking).
3. THE set of validators SHALL be extensible; an unknown validator SHALL warn
   (lenient) and SHALL be treatable as fail-closed under strict mode.
4. Validation SHALL be performed server-side (not solely in the client), and
   validation results (pass/warn) SHALL be recorded for observability.

## Requirement 9 — Shared artifact storage

**User story:** As a persona, I want the deliverables I produce to be stored in a
shared location that downstream personas can read, so that handoffs carry the
work forward.

**Acceptance criteria:**
1. THE system SHALL store artifacts in a shared, per-project location reachable by
   all authorized personas.
2. WHEN a persona submits an artifact, THEN downstream personas SHALL be able to
   read it after the relevant handoff.
3. THE system SHALL record, per artifact, who submitted it and when (audit).
4. Artifact write access SHALL be limited to the owning persona for the active
   stage (consistent with RBAC).

## Requirement 10 — Authentication and per-persona identity

**User story:** As a user, I want to act as my assigned persona with verified
credentials, so that RBAC and audit are trustworthy.

**Acceptance criteria:**
1. THE system SHALL authenticate users via Cognito and derive the persona role
   from a verified group/claim.
2. WHEN a user selects a persona in Kiro, THEN actions SHALL be authorized as that
   persona's role by the backend.
3. Credentials and secrets SHALL be referenced securely (no secrets in client
   config or logs).

## Requirement 11 — Git round-trip (retained, no AgentCore)

**User story:** As a Software Developer, I want to link my pushed repository and
submit it so QA can test it, using serverless ingestion that does not depend on
AgentCore.

**Acceptance criteria:**
1. THE system SHALL allow the developer to link a Git repository (validated host
   allowlist) and submit the implementation, gated on a linked repo.
2. WHEN the implementation is submitted, THEN the system SHALL ingest the repo
   source into the project's shared storage and advance the project to QA.
3. THE QA Engineer SHALL be able to trigger a test run whose results are recorded
   on the project record.
4. Ingestion and testing SHALL use serverless build infrastructure and SHALL NOT
   depend on AgentCore.

## Requirement 12 — Multi-user work within a role

**User story:** As one of several people sharing a persona role, I want to see and
claim the projects assigned to me, so that multiple users can work the same phase
without collisions.

**Acceptance criteria:**
1. THE system SHALL allow multiple users to hold the same persona role.
2. THE system SHALL let a user view projects assigned to their role and identify
   which are theirs to act on.
3. Per-user claim/assignment is **deferred beyond v1**; v1 SHALL rely on
   role-level ownership with lost-update (conditional-write) protection so
   concurrent same-role actors cannot silently overwrite each other.
4. Concurrent state changes to the same project SHALL be serialized safely
   (Requirement 2.4).

## Requirement 13 — Scale and data store

**User story:** As the organization (up to thousands of users), I want the shared
store to scale, so that project selection, handoffs, and queues remain fast.

**Acceptance criteria:**
1. THE record store SHALL support indexed retrieval by owning role, by assignee,
   by state, and by program/grouping, without an unbounded scan for routine
   queries.
2. THE artifact store SHALL scale to many projects and large documents/source.
3. THE record store SHALL be **Amazon DynamoDB**, isolated behind the Project API
   so the workflow, RBAC, and generation logic are independent of the store
   implementation. Artifacts and ingested source remain in object storage (S3).

## Requirement 14 — Observability and audit

**User story:** As a maintenance/operations persona, I want visibility into
project status and history, so that I can track progress and spot stuck work.

**Acceptance criteria:**
1. THE system SHALL record an append-only history of transitions and stage
   completions (who, from, to, when, note).
2. THE read-only Maintenance persona SHALL be able to view cross-project status
   without the ability to modify state.
3. THE system SHALL expose per-project current stage and state for monitoring
   (enabling stage-dwell/SLA reporting).

## Out of scope (for this spec)
- In-platform automated code generation (the developer codes in Kiro/externally).
- Frontier-agent (AWS DevOps/Security Agent) integration (separate track).
- Program/portfolio roll-ups and sub-task/work-item hierarchies (future).
- Long-term org knowledge base / RAG (separate track).
