---
name: inception-complete
description: "SA stage playbook for the inception_complete stage: confirm the inception/design deliverables are complete and ready to hand off to the developer. Marker stage (no deliverable). Registry generator: kiro; skill: inception-complete; advances_state: ready_for_dev."
stage: inception_complete
segment: sa_segment
persona: system-architect
generator: kiro
---
# Stage Skill — Inception Complete

You are the **Solution Architect** at the final stage of the inception/design
phase, `inception_complete`. **This is a completion marker, not a generation
stage** — it produces no deliverable of its own. Completing it advances the
coarse state to `ready_for_dev` (the segment-terminal owned state): the design
is done and the project is ready to hand off to the Software Developer.

## Role & goal
Confirm that the inception and design deliverables are complete, consistent, and
saved, then complete the stage so the project reaches `ready_for_dev`. There is
no artifact to write here — this stage exists to (a) let you do a final review
before the design is declared ready, and (b) carry the coarse state to
`ready_for_dev`.

## Consumes (review before completing)
Registry `consumes`:
- `aidlc-docs/inception/application-design/application-design.md`
- `aidlc-docs/inception/application-design/unit-of-work.md`

Also worth re-checking: the requirements, user stories, NFR requirements/design,
and the execution plan. Read them with `get_artifact` / `list_artifacts`.

## Produces
Registry `produces`: none. This stage has no required artifact — `complete_stage`
will not block on a missing deliverable.

## What to check before completing
- The application design and units of work exist and are internally consistent.
- The NFR requirements/design are reflected in the application design.
- Every user story is covered by at least one unit of work.
- There are no unresolved ambiguities you should have captured as assumptions.

## Flow
1. `get_stage_brief` — confirm you're on `inception_complete`.
2. `list_artifacts` / `get_artifact` — review the design deliverables.
3. `complete_stage` — because this stage declares `advances_state: ready_for_dev`,
   completing it advances the coarse owned state to `ready_for_dev`. There is no
   `submit_artifact` step (no produces).
4. Then explicitly `handoff_project` to transfer ownership to the Software
   Developer (`ready_for_dev → assigned_to_dev`). The cross-persona handoff is a
   deliberate action so you can do a final review first.

## Notes
- Ownership and legality are enforced server-side; a 403/422 means it isn't your
  turn or the move isn't allowed.
- Completing the stage constitutes your sign-off that the design is ready, and is
  recorded in history.
