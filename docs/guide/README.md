# Agentic SDLC Transformation — User Documentation

Welcome. This is the user-facing guide set for the Agentic SDLC Transformation platform — a multi-persona, agent-driven collaboration platform for the full software development lifecycle (inception, design, construction, QA, deployment, and operations), built on AWS.

These docs are written to be **shared with users**: product owners, business analysts, solution architects, developers, and operations. They focus on understanding and *using* the platform from **Kiro IDE** — the only frontend. If you're deploying or extending it, jump to the [engineering docs](#for-builders-and-operators).

---

## Start here

| If you want to… | Read |
| --- | --- |
| Understand what the platform is and who it's for | [Overview](overview.md) |
| See how it's built on AWS | [Architecture](architecture.md) |
| Select a persona agent and find your way around | [Getting Started](getting-started.md) |

---

## Walkthroughs by role

Pick the guide for your persona:

- [Business Analyst](walkthrough-business-analyst.md) — create a project, produce requirements and user stories, hand off to the architect.
- [Solution Architect](walkthrough-solution-architect.md) — review the requirements, produce the design and units of work, hand off to the developer.
- [Software Developer](walkthrough-software-developer.md) — assemble the construction brief, implement the code in Kiro, and link the repository.
- [QA Engineer](walkthrough-qa-engineer.md) — test the ingested code with test planning, execution, and reporting.
- [DevOps Engineer](walkthrough-devops-engineer.md) — deploy the application with deployment planning and release management.
- [Maintenance Dashboard](walkthrough-maintenance-dashboard.md) — the read-only cross-project operations view.

---

## Cross-cutting topics

- [Handoffs and Change Requests](handoffs-and-change-requests.md) — the full state machine, forward moves, bounces, and how the rules are enforced and audited.
- [Artifacts and Files](artifacts-and-files.md) — what the platform generates, where it's stored, and the `agentic-sdlc` vs `aidlc-docs` naming.
- [FAQ and Glossary](faq-and-glossary.md) — quick answers and a reference for the terms used throughout.

---

## The workflow in one picture

```text
Business Analyst        Solution Architect              Software Developer              QA Engineer                 DevOps Engineer
──────────────── ──────────────────────────── ──────────────────────────── ──────────────────────────── ─────────────────────────
create ─► requirements ─► review ─► design ─► construction ─► link repo & ─► test plan ─► test execution ─► deployment ─► release ─► deployed
         + stories        (approve/          + NFR       brief     submit for QA     + results   + reporting    planning   + runbook
                           request changes)                            ⬇                                                       
                                                                  ingestion                                                    
                                                               (Git round-trip)                                               
```

Every arrow is a governed step: a persona generates the deliverable in Kiro, a human reviews, and the backend records the transition. Work only moves to the next persona through an explicit handoff.

---

## For builders and operators

This guide set intentionally stays at the user level. For deployment, configuration, and internals, see:

- [Project README](../../README.md) — the top-level project overview and quick start.
- [Deployment Steps](../deployment-steps.md) — step-by-step deploy, stack outputs, and teardown.
- [MCP server setup](../../code/kiro-mcp/docs/setup.md) — wiring the `aidlc-platform` MCP server into Kiro (env vars, persona credentials).
- [kiro-native-platform spec](../../.kiro/specs/kiro-native-platform/) — the kiro-native-platform spec (the backend as system of record, Kiro as the generation engine).
- [User Guide](../user-guide.md) — the short deploy → configure → first-run companion.
- [Demo Notes](../demo-notes.md) — presenter notes for a customer demo.
- [Deployed Environment](../deployed-environment.md) — the current demo environment and the six persona logins.
