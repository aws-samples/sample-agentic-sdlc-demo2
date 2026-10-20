# FAQ and Glossary

Quick answers to common questions, plus a glossary of the terms you'll run into across the platform.

---

## Frequently asked questions

### Does the platform write my application code?

No. The active workflow covers inception, design, and the developer handoff. It produces a **construction brief** that guides you as you implement the code in Kiro (or your environment of choice). Once implemented, you link your repository back to the platform, which ingests it for the QA and DevOps stages. This split is intentional — the platform's job is to get you to a construction-ready state, then bridge your implemented code back into the QA and deployment phases.

### Why can I see projects I can't act on?

Visibility is broad on purpose: every persona can *see* every project, which makes cross-team awareness and the read-only maintenance view possible. But you can only *act* on projects currently assigned to your persona — the server (Project API) rejects out-of-turn actions with an HTTP 403.

### Can I play more than one persona?

Yes. Switch between persona agents with the agent picker (`cmd+alt+1..6`) — each agent carries its own Cognito identity, so switching agent is switching persona. This is the easiest way to run a full BA → SA → Dev → QA → DevOps flow yourself.

### How many times can a project bounce back and forth?

As many times as needed. There's no cap on request-changes bounces (SA → BA, Dev → SA, QA → Dev, and DevOps → QA). Every bounce, including the note, is recorded in the project's history.

### How does the Git round-trip work?

After implementing code in your external IDE, you link your GitHub/GitLab repository to the project. The platform triggers a CodeBuild job that clones your repository and syncs the source code into S3. Once ingestion completes, the project automatically advances to the QA Engineer, who can then review both your code and the design artifacts to perform testing and quality assurance.

### What happens if my repository is private?

You can provide a personal access token when linking your repository. The token is securely stored in AWS Secrets Manager and used by the ingestion process to access your private repository. The token is never logged or displayed.

### What file types can I upload as requirements?

`.md`, `.txt`, `.pdf`, and `.docx`. PDFs and Word documents are automatically extracted to plain text, which becomes the agent's authoritative requirements baseline.

### The agent is asking a lot of questions. How do I speed that up?

Give it more context up front. If you're the BA, upload a detailed requirements document or paste a thorough description. When the agent already has the information, it only asks about gaps and ambiguities instead of the full question set.

### Why do some stages not run?

Several stages are conditional. Workflow planning assesses the project's scope and complexity and decides which design stages are needed. Smaller, simpler projects skip stages like user stories, application design, or units generation. As the SA, you can override these decisions at the workflow-planning gate.

### I don't see any persona I can act as / a persona's actions are rejected.

Your persona is your Cognito identity: the role must be one of the recognized values, and the post-confirmation step (which maps `custom:role` to a group) runs only after email verification. If you were confirmed some other way, an administrator may need to add you to the matching Cognito group. Selecting a persona agent that isn't the current owner will get an HTTP 403 — switch to the owning persona. See [Getting Started](getting-started.md#selecting-a-persona).

### Where's the deployment guide?

Deploying and tearing down the platform is covered in [Deployment Steps](../deployment-steps.md); wiring the MCP server into Kiro is in [code/kiro-mcp/docs/setup.md](../../code/kiro-mcp/docs/setup.md). This guide set focuses on *using* the platform, not standing it up.

### Is my work saved if a session is interrupted?

Yes. Every artifact you submit is stored durably in S3, and the authoritative handoff state, stage cursor, and history live in the DynamoDB project record — independent of any Kiro session. There is no server-side session state to lose: pick up where you left off by selecting your persona agent and calling `get_project` / `get_stage_brief`.

---

## Glossary

**Acceptance** — the checks the backend runs on `complete_stage`. In v1 acceptance is **lenient**: warnings only, with a missing *required* artifact as the one hard block.

**Artifact** — any file the platform generates (requirements, stories, design, diagrams, the construction brief). Stored in S3 under `{project_slug}/aidlc-docs/…`; read with `get_artifact`.

**Bounce** — a `return_for_changes` transition that sends a project backward to the previous persona (SA → BA, Dev → SA, QA → Dev, DevOps → QA). Never blocked by an enforcement hook.

**Construction brief** — the consolidated, ready-to-paste package (`construction-brief.md`) that guides implementation. Contains all requirements, design, and units of work in a single document.

**Enforcement hook** — a client-side Kiro hook that adds to the server RBAC: review-before-approve gates `complete_stage`/`handoff_project` until artifacts were read this session; tests-before-submit gates the developer's `submit_implementation`. Neither blocks `return_for_changes`.

**Git round-trip** — the developer links their repository, which triggers CodeBuild ingestion of the source code into S3 for the QA and DevOps stages, advancing the project to QA.

**Handoff** — transferring ownership of a project from one persona to the next at a segment boundary, via `handoff_project`.

**Ingestion** — the CodeBuild process that clones a linked repository and syncs the source into `{project_slug}/source/` for downstream QA and DevOps consumption.

**MCP (Model Context Protocol)** — the protocol the persona agents use to call the local `aidlc-platform` MCP server, which wraps the Project API and S3.

**NFR (Non-Functional Requirements)** — performance, scalability, security, and operational requirements produced by the Solution Architect.

**Persona** — the role you select as a Kiro agent: Business Analyst, Solution Architect, Software Developer, QA Engineer, DevOps Engineer, or Maintenance Specialist. Backed by a Cognito identity; determines what you can see and do.

**Project record** — the authoritative record of a project's handoff state, owning role, stage cursor, and history. Lives in **DynamoDB** (`AidlcProjects`, item `PROJECT#{uuid}` / `META`), owned by the Project API.

**Skill** — a versioned per-persona stage playbook (`.kiro/skills/<persona>/<stage>.md`) that guides how Kiro generates that stage's deliverable.

**Stage** — a single step within a segment (for example, Requirements Analysis or Application Design), generated by the persona's Kiro agent per its Skill and stage contract.

**Stage brief / stage contract** — what `get_stage_brief` returns for the active stage: its `produces`, `consumes`, `acceptance`, and guiding `skill`, derived from the registry plus the project's stage cursor.

**Segment** — the portion of the lifecycle owned by one persona (the BA segment, the SA segment, the Developer segment, and so on).

**State** — where a project sits in the handoff state machine (`draft`, `requirements_ready`, `assigned_to_sa`, and so on).

**Unit of work** — a loosely coupled, independently buildable piece the system is decomposed into during Units Generation.

**Workflow registry** — the single source of truth defining personas, segments, states, legal transitions, and stage contracts. Canonical copy in the Project API Lambda asset, mirrored to the Kiro MCP server.

---

## Next

- [Overview](overview.md)
- [Handoffs and Change Requests](handoffs-and-change-requests.md)
- [Architecture](architecture.md)
