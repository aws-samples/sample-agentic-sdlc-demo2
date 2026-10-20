# Overview

Agentic SDLC Transformation is a multi-persona, agent-driven collaboration platform for the full software development lifecycle — inception, design, construction, QA, deployment, and operations. It runs on AWS and lets specialized AI personas do the heavy lifting while humans stay in control of every decision that matters.

Instead of one chatbot trying to do everything, the platform models the way real delivery teams work: distinct roles own distinct phases, and work moves forward through explicit, governed handoffs.

You work the whole platform from **Kiro IDE** — it is the only frontend. Each persona is a Kiro **agent** that calls a shared MCP server; there is no web portal, browser UI, or hosted login page. "Signing in as a persona" means selecting that persona's agent in Kiro.

> New here? Read this page, then jump to [Getting Started](getting-started.md) to select a persona and run your first project.

---

## What it does

You bring a rough idea ("an event RSVP and check-in app," "an internal helpdesk," and so on). The platform walks it through a governed pipeline and produces a consistent, reviewable set of artifacts:

- **Requirements** — functional and non-functional, derived from your intent and any documents you upload.
- **User stories and personas** — contracts for what gets built, following INVEST criteria.
- **A workflow plan** — which design stages will run, and why, based on the project's scope and complexity.
- **Application design** — components, services, dependencies, and an AWS architecture diagram.
- **Units of work** — the system decomposed into loosely coupled, buildable pieces.
- **A construction brief** — a consolidated, ready-to-paste package a developer takes into an AI-assisted IDE to write the actual code.
- **Git round-trip** — the developer links their repository, and the platform ingests the code for QA and DevOps.
- **Test plan and results** — test planning, execution assessment, and QA reports on the ingested code.
- **Deployment plan and runbook** — deployment planning and release management for production deployment.

At each meaningful step, the persona's Kiro agent generates the deliverable — guided by a versioned stage **Skill** and the stage's contract — and you review it before advancing. Nothing moves to the next persona until a human hands it off.

---

## Who it's for

- **Product owners and business analysts** who want to turn intent into structured, reviewable requirements quickly.
- **Solution architects** who want a fast, standardized path from requirements to an AWS-targeted design.
- **Software developers** who want a clean, complete brief to hand to an AI coding assistant.
- **Engineering leaders and operations** who want cross-project visibility into where work stands.

If you are evaluating how agentic AI can accelerate cloud-native delivery without giving up human oversight, this platform is a working reference.

---

## The six personas

Each persona is a Kiro **agent** you select from the agent picker (`cmd+alt+1..6`), backed by its own Cognito identity. The backend enforces which actions a persona can take from its verified group claim — server-side, not in the client.

| Persona | Owns | What they do |
| --- | --- | --- |
| **Business Analyst (BA)** | Inception / requirements | Creates the project, optionally uploads a requirements document, and generates requirements, user stories, and personas in Kiro. Then hands the project off to the Solution Architect. |
| **Solution Architect (SA)** | Design | Reviews the BA's artifacts and either continues or returns them for changes. Produces the workflow plan, NFR requirements, NFR design, application design, AWS architecture diagram, and units of work. Then hands off to the Software Developer. |
| **Software Developer (Dev)** | Construction handoff | Generates and reads a consolidated construction brief plus per-unit task files, implements the code in Kiro, then links the repository and submits for QA. Can bounce the project back to the SA if the design needs changes. |
| **QA Engineer** | Testing | Receives the ingested code and produces test plans, a test-execution assessment, and QA reports. Can run a CodeBuild test runner on the actual code. Hands off to DevOps or bounces back to the developer. |
| **DevOps Engineer** | Deployment | Receives the tested code and produces deployment plans and release runbooks. Advances the project through to the deployed state. Can bounce back to QA if issues are found. |
| **Maintenance Specialist** | (read-only) | Owns no phase. Gets a read-only cross-project view — every project's current stage and assigned persona, repository status, and rollups — through the MCP read tools. It cannot generate, hand off, or change state. |

The platform ships with these six personas configured. Adding a persona is a configuration change, not a code change — see [Architecture](architecture.md#extensibility).

---

## The lifecycle at a glance

A project moves forward through a fixed sequence of states. Each state has exactly one owning persona, and only that persona can advance it.

```text
draft ─► requirements_ready ─► assigned_to_sa ─► in_design ─► design_ready ─► ready_for_dev ─► assigned_to_dev ─► implementation_submitted ─► assigned_to_qa ─► in_qa ─► qa_passed ─► assigned_to_devops ─► in_deployment ─► deployed
 (BA)         (BA)                 (SA)            (SA)          (SA)            (SA)              (Dev)               (Dev)                    (QA)        (QA)      (QA)           (DevOps)           (DevOps)      (DevOps)

                                     │                                                              │                                                              │                            │
                                     └─ request changes ─► requirements_ready                       └─ request changes ─► design_ready                          └─ request changes ─► assigned_to_dev   └─ request changes ─► assigned_to_qa
                                        (SA sends back to BA)                                         (Dev sends back to SA)                                     (QA sends back to Dev)            (DevOps sends back to QA)
```

- **Forward** moves are the normal path from idea to deployed application.
- **Request-changes** moves are the supported "bounces": SA→BA, Dev→SA, QA→Dev, and DevOps→QA.

The full meaning of each state, and how transitions are enforced and audited, is covered in [Handoffs and Change Requests](handoffs-and-change-requests.md).

---

## Two principles behind the design

**AI-powered execution with human oversight.** Each persona's Kiro agent generates the deliverable, asks clarifying questions, and defers the decisions that require business context to you. You review each artifact and decide when to complete a stage or hand off. This mirrors the AWS [AI-Driven Development Lifecycle (AI-DLC)](https://aws.amazon.com/blogs/devops/ai-driven-development-life-cycle/) methodology the platform is based on.

**Governed collaboration, not a free-for-all.** Roles, permissions, and legal state transitions are defined in one place (a workflow registry) and enforced server-side by the Project API. A persona can only act on projects assigned to it, and only in ways the state machine allows. This keeps a multi-persona workflow predictable and auditable.

---

## What it does not do

Setting expectations honestly:

- **It does not generate application code as a platform stage.** The construction stage produces a construction brief; the developer implements the code in Kiro (or their environment of choice), pushes to GitHub/GitLab, and links the repository back to the platform, which ingests it for the QA and DevOps stages.
- **The Dev→QA boundary is the Git round-trip.** The construction handoff stage assembles the brief but does not advance the lifecycle state on its own — linking the repo and submitting triggers the CodeBuild ingest that advances the project to QA.
- **Real test execution uses a CodeBuild runner.** The QA Engineer can trigger actual test runs on the ingested code, but the test-execution stage primarily produces a test assessment.

---

## Where to go next

- [Getting Started](getting-started.md) — select a persona agent and orient yourself in Kiro.
- [Business Analyst Walkthrough](walkthrough-business-analyst.md) — create a project and produce requirements and stories.
- [Solution Architect Walkthrough](walkthrough-solution-architect.md) — review, design, and hand off.
- [Software Developer Walkthrough](walkthrough-software-developer.md) — turn the brief into code.
- [Maintenance Dashboard](walkthrough-maintenance-dashboard.md) — the read-only operations view.
- [Architecture](architecture.md) — how the pieces fit together on AWS.
