# Solution Architect Walkthrough

As the Solution Architect (SA), you're the quality gate between requirements and construction. You review the BA's work, decide whether it's ready, and — if it is — produce the design: NFRs, application architecture, an AWS architecture diagram, and a decomposition into units of work. Then you hand the project to the Software Developer. You work from the Solution Architect agent in Kiro.

**Your persona:** `system-architect` (agent picker `cmd+alt+2`)
**You own the project while it's in:** `assigned_to_sa`, `in_design`, `design_ready`, and `ready_for_dev`
**You receive from:** the Business Analyst
**You hand off to:** the Software Developer

---

## Before you start

Select the **Solution Architect** agent in Kiro (`cmd+alt+2`) — see [Getting Started](getting-started.md). Ask *"Show me my projects"* (`list_projects`) and look for a project in `assigned_to_sa` — that's one the BA has handed to you.

---

## Step 1 — Review the BA's artifacts

Open the assigned project (`get_project`) and read the BA's deliverables with `get_artifact`:

- `requirements.md` — the functional and non-functional requirements.
- `stories.md` — the user stories with acceptance criteria.
- `personas.md` — the user personas.

Read them carefully. You're deciding one thing: **is this a solid enough foundation to design against?** Reading the artifacts this session also satisfies the review-before-approve hook, which you'll need before you can hand off.

---

## Step 2 — Continue into design, or return for changes

You have two paths.

### Continue into design

If the requirements are sound, start working the design stages (Step 3). The first `complete_stage` advances the project `assigned_to_sa → in_design`.

### Return for changes

If something is missing, ambiguous, or wrong, ask the agent to `return_for_changes` with a **specific** note telling the BA what needs to change. This:

- Advances the project `assigned_to_sa → requirements_ready` (ownership flips back to the BA).
- Records your note in the project's history (the BA sees it via `latest_change_request`).

`return_for_changes` is never blocked by an enforcement hook, so you can bounce even before reading everything. The BA addresses your feedback and hands the project back to you; you review again. This can repeat as many times as needed.

---

## Step 3 — Produce the design

Work each design stage through the standard loop: `get_stage_brief` → read `consumes` inputs with `get_artifact` → generate the deliverable in Kiro per the stage Skill → `submit_artifact` → `complete_stage`.

| Stage | What it produces | Runs when |
| --- | --- | --- |
| **Workflow Planning** | An execution plan (with a flowchart) deciding which downstream design stages are required or skipped, based on the scope and complexity assessed during requirements. | Always |
| **NFR Requirements** | Non-functional requirements covering performance, scalability, security, and operational concerns. | Always |
| **NFR Design** | The non-functional design addressing the NFR requirements with specific architectural patterns and solutions. | Always |
| **Application Design** | The component architecture — components, methods, services, dependencies — plus an AWS architecture diagram. | Multi-component / system-wide scope, or moderate/complex complexity |
| **Units Generation** | The system decomposed into loosely coupled units of work, with dependency and story maps. | Multi-component or system-wide scope |
| **Inception Complete** | A completion summary that prepares the developer handoff. | Always |

Review each artifact with `get_artifact` before completing its stage. See [Artifacts and Files](artifacts-and-files.md) for the full list of paths.

---

## Step 4 — Advance through the design states

As you complete the design stages, `complete_stage` advances the coarse owned state where the stage declares it: through `in_design`, then `design_ready`, then `ready_for_dev`. By the time you reach `ready_for_dev` you've produced the full set: requirements, user stories, personas, the workflow plan, the NFR requirements, the NFR design, the application design, the AWS architecture diagram, and the units of work.

---

## Step 5 — Hand off to the Software Developer

From `ready_for_dev`, ask the agent to hand off to the developer. It calls `handoff_project`, advancing `ready_for_dev → assigned_to_dev` and transferring ownership. The developer then assembles the construction brief (see the [Software Developer Walkthrough](walkthrough-software-developer.md)).

---

## Handling a bounce from the developer

A developer can `return_for_changes` if the design needs revision. When they do, the project returns to `design_ready` and ownership flips back to you. Address the design change (regenerate and `submit_artifact` as needed), advance back to `ready_for_dev`, and `handoff_project` to the developer again.

---

## Tips

- **Be decisive at review.** Returning for changes is cheap and keeps quality high — use the note to be specific so the BA can fix things in one pass.
- **Mind the planning stage.** The workflow-planning execution plan drives which optional design stages run; make it reflect the project's real scope.
- **Check the diagram.** The AWS architecture diagram is one of the most useful artifacts for the downstream developer — make sure it reflects your intent before you hand off.

---

## Next

- [Software Developer Walkthrough](walkthrough-software-developer.md) — what the developer receives.
- [Handoffs and Change Requests](handoffs-and-change-requests.md) — the full state machine.
- [Artifacts and Files](artifacts-and-files.md) — what the design stages produce.
