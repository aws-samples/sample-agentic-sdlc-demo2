---
name: construction-handoff
description: "Dev stage playbook for the construction_handoff stage: consolidate the inception/design artifacts into a single construction-brief.md a coding agent can build from. Does NOT advance state — submission is the Git round-trip (link_repo + submit_implementation). Registry generator: kiro; skill: construction-handoff."
stage: construction_handoff
segment: dev_segment
persona: software-developer
generator: kiro
---
# Stage Skill — Construction Handoff

You are the **Software Developer** generating the construction brief for the
`construction_handoff` stage. **You (Kiro) generate this artifact** — there is no
server-side stage agent. Resolve all project data through the platform MCP
tools; never fabricate it.

## Role & goal
Consolidate the inception/design deliverables into one **construction brief** — a
ready-to-use specification a coding agent (or you, in an external IDE) implements
the project from. The brief packages the requirements, user stories, application
design, and units of work behind a short preamble aimed at a coding agent.

**IMPORTANT — this stage does NOT advance the coarse state.** It produces the
brief only. The Dev → QA boundary is the **Git round-trip**: after you implement
the code externally, you `link_repo` (a validated Git URL) and
`submit_implementation`, which triggers the serverless ingestion (clone → sync
into `{slug}/source/`) that advances the project to `assigned_to_qa`. Completing
this stage will NOT bypass that repo-linked submit gate.

## Consumes (read first)
Registry `consumes`:
- `aidlc-docs/inception/requirements/requirements.md`
- `aidlc-docs/inception/user-stories/stories.md`
- `aidlc-docs/inception/application-design/application-design.md`
- `aidlc-docs/inception/application-design/unit-of-work.md`

Read them with `get_artifact`. Fold in any companion design/components files that
add useful context.

## Produces (write here)
Registry `produces`:
- `aidlc-docs/construction/construction-brief.md` — **required**.

Submit it with `submit_artifact(project_uuid, "aidlc-docs/construction/construction-brief.md", <content>)`,
then call `complete_stage`.

## Required output structure
The deliverable MUST satisfy the stage `acceptance` rules:
- `nonempty`
- `min_words:150`
- `has_sections:Application Design,Units of Work` — these headings MUST be
  present (verbatim), each with real content.

Recommended layout:

```markdown
# Construction Brief — <Project Name>

> For an AI coding agent (Kiro / Claude Code / Cursor). Implement the project
> described below. Inception and design are complete; the sections below are the
> authoritative specification. Target AWS for all cloud services. Build unit by
> unit.

## Requirements & Stories
A condensed statement of the functional requirements and the user stories.

## Application Design
The components, methods, services, and dependencies (from application-design.md).

## Units of Work
The ordered units to implement, each with its boundaries, responsibilities, and
the stories it delivers.
```

## Quality guidance
- The brief must be **self-contained** — a coding agent should not need to hunt
  for context outside it.
- Preserve traceability from units → stories → requirements.
- Keep the AWS-only constraint explicit in the preamble.

## Flow
1. `get_stage_brief` — confirm you're on `construction_handoff`.
2. `get_artifact` — read the requirements, stories, application design, units.
3. Generate `construction-brief.md` per the structure above.
4. `submit_artifact` to the produces path.
5. `complete_stage` — validates acceptance (lenient warnings; missing required
   blocks). This stage has no `advances_state`, so the coarse state stays
   `assigned_to_dev` and the cursor does not move past the single Dev stage.
6. Implement the code (externally / in Kiro), then `link_repo` and
   `submit_implementation` — the Git round-trip advances the project to QA.

## Notes
- Ownership and legality are enforced server-side; a 403/422 means it isn't your
  turn or the move isn't allowed.
- `submit_implementation` is gated on a linked repo — link it first.
