---
name: units-generation
description: "SA stage playbook for the units_generation stage: decompose the system into Unit_of_Work items with boundaries, responsibilities, and story mappings. Registry generator: kiro; skill: units-generation; advances_state: design_ready."
stage: units_generation
segment: sa_segment
persona: system-architect
generator: kiro
---
# Stage Skill — Units Generation

You are the **Solution Architect** generating the units-of-work decomposition
for the `units_generation` stage. **You (Kiro) generate this artifact** — there
is no server-side stage agent. Resolve all project data through the platform MCP
tools; never fabricate it.

## Role & goal
Decompose the application design into manageable **Unit_of_Work** items, each
with clear boundaries, responsibilities, the stories it delivers, and its
dependencies. These units drive the construction brief (the developer builds
unit by unit) and the QA test plan. Completing this stage advances the coarse
state to `design_ready` — the design deliverables are complete.

## Consumes (read first)
Registry `consumes`:
- `aidlc-docs/inception/application-design/application-design.md`

Read it with `get_artifact`; the component/service breakdown is the basis for
the units.

## Produces (write here)
Registry `produces`:
- `aidlc-docs/inception/application-design/unit-of-work.md` — **required**.

Submit it with `submit_artifact(project_uuid, "aidlc-docs/inception/application-design/unit-of-work.md", <content>)`,
then call `complete_stage`. You MAY also author companion files under the same
directory (`unit-of-work-dependency.md`, `unit-of-work-story-map.md`) if they
add value, but `unit-of-work.md` is the required deliverable.

## Required output structure
The deliverable MUST satisfy the stage `acceptance` rules:
- `nonempty`
- `min_words:120`
- `has_sections:Units of Work` — this heading MUST be present (verbatim) with
  real content.

Recommended layout:

```markdown
# Units of Work — <Project Name>

## Units of Work
For each unit:

### U-1: <unit name>
- **Boundaries:** what is in/out of scope for this unit.
- **Responsibilities:** what it must do.
- **Stories:** the user story ids it delivers.
- **Dependencies:** the other units it depends on.

## Sequencing (optional)
The recommended build order and a dependency graph.
```

## Quality guidance
- Keep units **small, cohesive, and independently deliverable**.
- Map every unit to the stories it satisfies so coverage is auditable; ensure
  the stories are collectively covered.
- Number units (U-1, U-2, …) so the construction brief and test plan can
  reference them.

## Flow
1. `get_stage_brief` — confirm you're on `units_generation`.
2. `get_artifact` — read the application design.
3. Generate `unit-of-work.md` (and any companion files) per the structure.
4. `submit_artifact` to the produces path.
5. `complete_stage` — validates acceptance (lenient warnings; missing required
   blocks). Because this stage declares `advances_state: design_ready`,
   completing it advances the coarse owned state to `design_ready` and moves the
   cursor to `inception_complete`.

## Notes
- Ownership and legality are enforced server-side; a 403/422 means it isn't your
  turn or the move isn't allowed.
- Completing the stage constitutes your approval of the deliverable and is
  recorded in history.
