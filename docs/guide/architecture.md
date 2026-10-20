# Architecture

This page explains how the platform is put together — the major components, how work flows through them, and how the workflow is governed. It is written for a mixed audience: architects and engineers who want the shape of the system, and non-engineers who want to understand what happens when they act in Kiro.

The platform runs the **Kiro-native** architecture: **Kiro generates every deliverable**, and a **thin serverless backend** is the authoritative **system of record**. There is no Amazon Bedrock AgentCore (no Runtime, Gateway, or Memory), no Strands orchestrator, and no web UI. For the source-level spec, see [`.kiro/specs/kiro-native-platform/`](../../.kiro/specs/kiro-native-platform/).

---

## The big picture

```text
   Kiro IDE (the only frontend)          ┌──────────────────────────────────────────────┐
   ┌──────────────────────────────┐      │                   AWS                          │
   │ Persona agent (.kiro/agents)  │      │                                                │
   │  + stage Skill (.kiro/skills) │      │   ┌───────────────┐     ┌──────────────────┐   │
   │  + enforcement hooks          │      │   │  Project API  │◄───►│  DynamoDB         │   │
   │                               │ HTTP │   │ (API GW +     │     │  (AidlcProjects — │   │
   │  aidlc-platform MCP server ───┼──────┼──►│  Lambda)      │     │  system of record)│   │
   │  (code/kiro-mcp, stdio)       │      │   └───────┬───────┘     └──────────────────┘   │
   └──────────────────────────────┘      │           │  presigned S3 URLs                 │
            │ ambient AWS creds           │           ▼                                    │
            │ (S3 artifact reads)         │   ┌──────────────────┐   ┌──────────────────┐  │
            └─────────────────────────────┼──►│  S3 (artifacts +  │   │  CodeBuild        │  │
                                          │   │  ingested source) │◄──│  (ingest + tests) │  │
   Cognito (USER_PASSWORD_AUTH) ──────────┼──►└──────────────────┘   └──────────────────┘  │
   per-persona identity                   └──────────────────────────────────────────────┘
```

There is a single path to the backend:

- **The Project API (HTTP).** The MCP server calls a Cognito-authorized REST API for everything about a project's *record* and *stage flow*: listing/creating/reading projects, deriving the active stage brief, minting short-lived presigned S3 URLs for artifact reads and writes, running acceptance, and advancing the stage cursor and handoff state. This is the authoritative source of truth for who owns a project and what state it's in.

Artifacts and ingested source live in Amazon S3. The authoritative project record — handoff state, owning role, and history — lives in **Amazon DynamoDB**.

---

## Components

### Frontend — Kiro IDE (the only frontend)

There is no web application, CloudFront distribution, or hosted login page. Every persona works entirely inside Kiro IDE, made up of:

- **A custom agent per persona** in `.kiro/agents/` (`business-analyst.md`, `system-architect`/`solution-architect.md`, and so on) — one per Cognito persona. You switch personas with the agent picker (`cmd+alt+1..6`). Selecting a persona agent *is* signing in as that persona: the agent carries that Cognito identity via per-persona `USER_PASSWORD_AUTH` credentials.
- **Per-persona stage Skills** in `.kiro/skills/<persona>/<stage>.md` — versioned playbooks that guide how each stage's deliverable is generated.
- **Enforcement hooks** in `.kiro/hooks/` — client-side gates (review-before-approve, tests-before-submit) that are additive to the server-side RBAC.
- **The `aidlc-platform` MCP server** in `code/kiro-mcp/` — a local (stdio) server that wraps the Project API and S3 and exposes the tools the agents call.

Packaged together, these ship as the `aidlc-platform` Kiro **Power**.

### Project API (API Gateway + Lambda) — the system of record

A Cognito-authorized REST API backed by DynamoDB and S3. It is the **authoritative RBAC, state-machine, and generation-flow layer**:

- Lists / creates / reads / deletes projects (visibility is broad by design — every persona can *see* all projects; only *actions* are owner-gated).
- Derives the **active stage brief** from the workflow registry plus the project's `current_stage` cursor (returning the stage's `produces` / `consumes` / `acceptance` and guiding `skill`).
- Mints short-lived (≈300s), owner-gated presigned S3 URLs. The `stage-artifact-url` route additionally constrains each upload URL to a path the active stage `produces`, so a client can never write outside its stage contract.
- Runs acceptance on `complete_stage` (**lenient in v1**: warnings only; a missing *required* artifact is the one hard block), advances the stage cursor, and — where the stage declares `advances_state` — advances the coarse owned handoff state.
- Applies handoff transitions (`handoff_project`) and reviewer bounces (`return_for_changes`).

Every transition is checked two ways: the caller must own the project's current state (**HTTP 403** otherwise), and the requested transition must be legal in the state machine (**HTTP 422** otherwise). Writes are conditional, so two people acting at once can't corrupt a record (**HTTP 409** on a stale write).

### Generation — Kiro (agent + model + Skill)

There is no server-side generation engine. Each persona produces its stage artifacts with **Kiro's own agent and model**, guided by the stage's **Skill**. The flow per stage is: `get_stage_brief` → read `consumes` inputs with `get_artifact` → generate the deliverable in Kiro per the Skill → `submit_artifact(path, content)` → `complete_stage`.

### Storage

- **DynamoDB (`AidlcProjects`)** — the authoritative project record, keyed `PROJECT#{uuid}` / `META`, single-table design with GSIs for the role queue, broad selection, and state dashboards. Holds the handoff state, owning role, stage cursor, and history.
- **Amazon S3** — the generated artifacts under `{project_slug}/aidlc-docs/…` and the ingested source under `{project_slug}/source/…`. The reserved `_platform/projects/` prefix is **retained but unused** as a path-traversal guard; it is **not** the record store.

### CodeBuild

Two roles, both serverless and AgentCore-free:

- **Ingestion** — when the developer links a repo and submits, a CodeBuild job clones the repo and `s3 sync`s the source into `{project_slug}/source/`, then advances the project to QA. An optional signature-verified push webhook can re-trigger it.
- **Test runner** — the QA `run_tests` action triggers a CodeBuild project that runs the ingested repository's test suite; results surface on the project record (`qa_test_status` / `qa_test_summary`).

---

## How a project moves forward: the flow

1. You select a persona agent in Kiro (`cmd+alt+1..6`). The MCP server authenticates as that persona's Cognito identity.
2. For the active stage, the agent calls `get_stage_brief` — the backend returns the stage contract from the registry + the project's `current_stage` cursor.
3. The agent reads the `consumes` inputs with `get_artifact`, generates the deliverable per the Skill, and uploads it with `submit_artifact` (an owner-gated, `produces`-constrained presigned PUT to S3).
4. `complete_stage` runs lenient acceptance, advances the stage cursor, and — where declared — advances the coarse owned state, appending a history entry atomically.
5. At a **segment boundary**, the owning persona calls `handoff_project` to transfer ownership. The Project API records the transition; the next persona picks up the project in their own Kiro window.

---

## Governance: one registry, enforced server-side

The heart of the platform's predictability is a single **workflow registry** (`workflow_registry.json`) that defines personas, segments, states, legal transitions, and each stage's contract. With AgentCore removed, it exists in **two** copies that must stay consistent:

1. `cdk/custom-resources/workflow_registry.json` — the Project API Lambda asset (the canonical source).
2. `code/kiro-mcp/aidlc_mcp/workflow_registry.json` — the Kiro MCP server copy (so the frontend can resolve stages/skills without a round-trip).

- The **Project API** uses it to derive the active stage brief and decide whether a requested transition is legal.
- The **MCP server / persona agents** use it to resolve stages and the guiding Skill.

Because personas, states, transitions, and stage contracts are defined in one place, the workflow stays coherent and is safe to extend.

---

## Security model (summary)

- **Authentication** is Amazon Cognito, `USER_PASSWORD_AUTH` per persona for the MCP server. The former machine (client-credentials) client and the `agentcore` resource-server scope were removed with AgentCore.
- **Persona** is chosen at sign-up, stored as a `custom:role` attribute, and mapped to a Cognito group by a post-confirmation Lambda trigger.
- **Authorization** for project actions is enforced server-side by the Project API from the caller's verified Cognito group — non-owners get 403, illegal transitions get 422, stale writes get 409.
- **Only network-exposed endpoints** are the Project API (Cognito-authorized) and the signature-verified Git webhook. Any new network-exposed endpoint must be authenticated.
- **Secrets** (per-project Atlassian credentials at `aidlc/projects/{uuid}/atlassian`, the Cognito client secret) live in AWS Secrets Manager and are referenced by name, never logged.
- **Uploads** use short-lived presigned URLs constrained to the active stage's `produces` paths; the client-supplied `project_uuid` is regex-constrained to a safe character set.

---

## Infrastructure and deployment

Everything is defined as AWS CDK v2 (TypeScript) in the `cdk/` directory and deployed as a single stack named **`AIDLCPlatformWorkshop`**. The stack provisions Cognito, the Project API (Lambda + API Gateway), the DynamoDB record table, the S3 artifact bucket, and the CodeBuild ingest/test-runner projects — no AgentCore constructs.

Deployment, prerequisites, and account-guard behavior are covered in [Deployment Steps](../deployment-steps.md); MCP configuration is in [code/kiro-mcp/docs/setup.md](../../code/kiro-mcp/docs/setup.md).

---

## Extensibility

Two extension points are designed in:

- **Add a persona or workflow segment** by editing the workflow registry (persona, segment, states, any request-changes edge, and a `stage_contract` for each new stage), then syncing both copies. The Project API derives its stage brief and handoff enforcement from the registry, so no orchestrator edit is needed. For a new segment's stages to produce artifacts, author the referenced Kiro Skill under `.kiro/skills/` — in this architecture, Kiro generates; there is no orchestrator to enable.
- **Adjust how a stage is generated** by editing its Skill under `.kiro/skills/<persona>/<stage>.md`.

---

## Related docs

- [Handoffs and Change Requests](handoffs-and-change-requests.md) — the state machine and how it's enforced.
- [Artifacts and Files](artifacts-and-files.md) — the S3 layout and the DynamoDB record.
- [kiro-native-platform spec](../../.kiro/specs/kiro-native-platform/) — the kiro-native-platform spec.
- [MCP server setup](../../code/kiro-mcp/docs/setup.md) — wiring the MCP server into Kiro.
