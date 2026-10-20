# Handoffs and Change Requests

Every project moves through a fixed set of states, and work passes between personas through governed handoffs. This page explains the state machine, what each state means, how forward moves and "bounces" work, and how the platform keeps it all honest.

---

## The state machine

```text
draft ─► requirements_ready ─► assigned_to_sa ─► in_design ─► design_ready ─► ready_for_dev ─► assigned_to_dev ─► implementation_submitted ─► assigned_to_qa ─► in_qa ─► qa_passed ─► assigned_to_devops ─► in_deployment ─► deployed
 (BA)         (BA)                 (SA)            (SA)          (SA)            (SA)              (Dev)               (Dev)                    (QA)        (QA)      (QA)           (DevOps)           (DevOps)      (DevOps)

                                     │                                                              │                                                              │                            │
                                     └─ request changes ─► requirements_ready                       └─ request changes ─► design_ready                          └─ request changes ─► assigned_to_dev   └─ request changes ─► assigned_to_qa
                                        (SA sends back to BA)                                         (Dev sends back to SA)                                     (QA sends back to Dev)            (DevOps sends back to QA)
```

Each state has exactly one **owning persona**. Only that persona can advance the project out of that state.

| State | Owner | What it means |
| --- | --- | --- |
| `draft` | Business Analyst | Project just created. The BA can upload requirements and start the inception stages. |
| `requirements_ready` | Business Analyst | Requirements and user stories are generated. The BA assigns the project to the SA. |
| `assigned_to_sa` | Solution Architect | The SA reviews the BA's artifacts and either continues into design or returns them for changes. |
| `in_design` | Solution Architect | The SA is producing NFR requirements, NFR design, application design and units of work. |
| `design_ready` | Solution Architect | Design work is complete; the SA completes the remaining stages toward development. |
| `ready_for_dev` | Solution Architect | Design is final. The SA hands off to the Software Developer. |
| `assigned_to_dev` | Software Developer | The construction brief is assembled. The developer implements in Kiro, links their repository, and submits for QA. |
| `implementation_submitted` | Software Developer | Repository is linked; CodeBuild ingestion clones and syncs the code, then advances to QA. |
| `assigned_to_qa` | QA Engineer | QA reviews test plan, can run tests via CodeBuild, and produces QA reports. |
| `in_qa` | QA Engineer | QA testing and assessment in progress. |
| `qa_passed` | QA Engineer | QA testing complete and passed. Ready for DevOps handoff. |
| `assigned_to_devops` | DevOps Engineer | DevOps produces deployment plans and release runbooks. |
| `in_deployment` | DevOps Engineer | Deployment planning and release management in progress. |
| `deployed` | DevOps Engineer | Terminal state. Application is deployed to production. |

---

## Forward moves

Forward moves are the normal path from idea to deployed application. Most are driven by the owning persona completing a stage (`complete_stage`), which advances the stage cursor and — where the stage declares `advances_state` — the coarse owned state. Crossing into the next persona's segment is an explicit `handoff_project`.

- BA → `handoff_project` to the SA (`requirements_ready → assigned_to_sa`)
- SA → `complete_stage` through design (`assigned_to_sa → in_design → design_ready → ready_for_dev`, as the design stages complete)
- SA → `handoff_project` to the Developer (`ready_for_dev → assigned_to_dev`)
- Dev → `link_repo` + `submit_implementation` (`assigned_to_dev → implementation_submitted`); the tests-before-submit hook must pass first
- System → CodeBuild ingest auto-advances (`implementation_submitted → assigned_to_qa`)
- QA → `complete_stage` through QA (`assigned_to_qa → in_qa → qa_passed`, as the QA stages complete)
- QA → `handoff_project` to DevOps (`qa_passed → assigned_to_devops`)
- DevOps → `complete_stage` through deployment (`assigned_to_devops → in_deployment → deployed`)

The construction handoff stage is the one exception: it produces the construction brief but does **not** advance the lifecycle state. The Dev→QA boundary is the Git round-trip (`submit_implementation` → CodeBuild ingest), not a `complete_stage`.

## Bounces (request changes)

Four bounces are supported. They send a project *backward* to the previous persona so they can fix something:

- **SA → BA:** `assigned_to_sa → requirements_ready`. The SA requests changes to the requirements. Ownership returns to the BA.
- **Dev → SA:** `assigned_to_dev → design_ready`. The developer requests changes to the design. Ownership returns to the SA.
- **QA → Dev:** `in_qa → assigned_to_dev`. The QA engineer requests changes to the implementation. Ownership returns to the developer.
- **DevOps → QA:** `in_deployment → assigned_to_qa`. The DevOps engineer requests changes from QA. Ownership returns to the QA engineer.

When you request changes, you can attach a **note** explaining what needs to change. The note is recorded in the project's history and surfaced to the persona receiving the bounce via `get_project` (`latest_change_request`).

There's no cap on bounces — a project can go back and forth as many times as quality requires.

---

## How a handoff actually happens

A handoff is an explicit ownership transfer, not just a status change.

1. As you work, your persona agent generates each stage in your segment (`get_stage_brief` → generate → `submit_artifact` → `complete_stage`).
2. When your segment's stages are complete, you call `handoff_project` to transfer ownership to the next persona.
3. The Project API validates the transition, flips the owning role, and appends a history entry atomically.
4. The next persona picks up the project in their own Kiro window (selecting their persona agent) and starts their segment's stages.

Because each persona works from their own persona agent — with its own stage Skill and Cognito identity — each gets a clean, focused starting point rather than one shared thread.

---

## How the rules are enforced

The platform doesn't rely on the client to keep things legal — it enforces the rules on the server, in the Project API.

- **Ownership (who can act).** The Project API checks the caller's verified identity (their Cognito group) against the project's owning role. A non-owner attempting a transition is rejected with **HTTP 403** — you can only act on a project when it's your persona's turn.
- **Legality (what's allowed).** Every requested transition is validated against the state machine. An illegal transition (say, jumping straight from `draft` to `ready_for_dev`) is rejected with **HTTP 422**, and the project record is left unchanged.
- **Concurrency.** Project records are written conditionally, so two people acting on the same project at the same time can't clobber each other's change — a stale write is rejected with **HTTP 409**.

The legal transitions and each stage's contract live in a single **workflow registry**, read by the Project API (canonical) and mirrored to the Kiro MCP server. Because the rules aren't duplicated across independent layers, the state machine stays coherent.

Two **client-side enforcement hooks** add to (never replace) the server rules: **review-before-approve** blocks `complete_stage`/`handoff_project` until you've read the artifacts this session, and **tests-before-submit** blocks the developer's `submit_implementation` until tests pass. Neither hook blocks `return_for_changes`.

---

## The audit trail

Every transition is appended to the project's **history**, recorded in the authoritative project record in **DynamoDB** (`AidlcProjects`, item `PROJECT#{uuid}` / `META`). Each entry captures who acted, when, and — for bounces — the note that was attached. `get_project` surfaces the record, giving you a complete, reviewable trail of how a project moved from idea to deployed, including every round trip.

---

## Seeing what's waiting on you

Ask your persona agent to `list_projects`. Each project shows its current state and owning role, so you can spot the ones assigned to your persona. `get_project` on any project shows the full detail, including `latest_change_request` when a bounce is waiting for you.

---

## Next

- [Business Analyst Walkthrough](walkthrough-business-analyst.md)
- [Solution Architect Walkthrough](walkthrough-solution-architect.md)
- [Software Developer Walkthrough](walkthrough-software-developer.md)
- [Architecture](architecture.md#governance-one-registry-enforced-server-side) — how the registry drives all this.
