# Maintenance Dashboard Walkthrough

The Maintenance Specialist is a read-only oversight role. It owns no phase of the workflow and performs no transitions. Instead, it gets a cross-project view — a single window on every project, where it stands, and who's holding it — built entirely from the MCP read tools inside Kiro. There is no web dashboard page.

**Your persona:** `maintenance-specialist` (agent picker `cmd+alt+6`)
**You own:** nothing (read-only)
**What you see:** a read-only cross-project operations view

---

## Before you start

Select the **Maintenance Specialist** agent in Kiro (`cmd+alt+6`) — see [Getting Started](getting-started.md). This persona's toolset is deliberately limited to read-only tools: `list_projects`, `get_project`, `list_artifacts`, and `get_artifact`. It has no generation, handoff, or transition tools at all, so there are no create/assign/complete actions to find.

---

## Building the cross-project view

There's no `/dashboard` page — you assemble the view by asking the agent, which calls the read tools:

1. **The rollup.** `list_projects` returns every project (visibility is broad by design) with its current state and owning persona. Ask the agent to summarize it — for example, group projects by state or by owning persona to see where work sits and who's the bottleneck.
2. **Project detail.** `get_project` on any project shows its full record: current stage, owning role, repository/ingest status and latest commit (for projects past the Git round-trip), and history.
3. **Artifacts.** `list_artifacts` / `get_artifact` let you inspect any project's generated documents.

Because `list_projects` is the same broad listing every persona uses, you get full visibility across all six roles without touching any project.

---

## What you can and can't do

- **Can:** view every project's status and detail, inspect artifacts, and summarize the aggregate picture; use this to spot stalled work, uneven workloads, or projects waiting too long in one state.
- **Can't:** create, hand off, complete a stage, return for changes, upload, or delete. This persona's tools are read-only, and even if a write were attempted the Project API would reject it (a non-owner gets an HTTP 403). The view is purely observational.

This separation is intentional — operations oversight shouldn't be able to accidentally move someone else's work.

---

## Why visibility is broad but actions are gated

Across the whole platform, every persona can *see* every project (the project listing is not owner-filtered), but only the owning persona can *act* on a project, enforced server-side by the Project API. The Maintenance Specialist takes that model to its natural end: full visibility, zero mutation. It reads the same broad listing the other personas use and simply presents it as an operations view.

---

## Next

- [Handoffs and Change Requests](handoffs-and-change-requests.md) — what each state you see means.
- [Overview](overview.md) — how the personas fit together.
