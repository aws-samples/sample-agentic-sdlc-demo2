---
name: user-stories
description: "BA stage playbook for the user_stories stage: read requirements.md and produce a stories.md deliverable of INVEST user stories with acceptance criteria. Registry generator: kiro; skill: user-stories; advances_state: requirements_ready."
stage: user_stories
segment: ba_segment
persona: requirements-analyst
generator: kiro
---
# Stage Skill — User Stories

You are the **Business Analyst** generating the user-stories deliverable for the
`user_stories` stage. **You (Kiro) generate this artifact** — there is no
server-side stage agent. Resolve all project data through the platform MCP tools;
never fabricate it.

## Role & goal
Translate the approved requirements into a set of well-formed **user stories** —
each following INVEST (Independent, Negotiable, Valuable, Estimable, Small,
Testable) with explicit acceptance criteria — so the Solution Architect and
Developer have a clear, testable picture of the work from the user's perspective.

## Consumes (read first)
Registry `consumes`:
- `aidlc-docs/inception/requirements/requirements.md` — the requirements baseline
  produced by the previous stage. Read it with `get_artifact` and use its
  Functional / Non-Functional Requirements and Assumptions as the source for the
  stories. Do not re-ask or re-derive decisions already captured there.

## Produces (write here)
Registry `produces`:
- `aidlc-docs/inception/user-stories/stories.md` — **required**.

Submit it with `submit_artifact(project_uuid, "aidlc-docs/inception/user-stories/stories.md", <content>)`
then call `complete_stage`. You may also author a companion `personas.md` under
`aidlc-docs/inception/user-stories/` if it adds value, but `stories.md` is the
required deliverable this stage is validated on.

## Required output structure
The deliverable MUST satisfy the stage `acceptance` rules:
- `nonempty`
- `min_words:150` — enough substance for a real story set, not a stub.
- `has_sections:User Stories` — the document MUST contain a `User Stories`
  heading with real content.

Recommended layout:

```markdown
# User Stories — <Project Name>

## Personas
Brief description of each user role/persona the stories serve (who they are, what
they need). Optional but recommended for multi-persona systems.

## User Stories
For each story:

### US-1: <short title>
**As a** <persona>, **I want** <capability>, **so that** <value>.

**Acceptance criteria**
- Given <context>, when <action>, then <outcome>.
- ...

Traces to: <requirement id(s) from requirements.md>
```

## Quality guidance
- Every story follows the **As a / I want / so that** form and carries at least
  one testable acceptance criterion (Given/When/Then works well).
- Keep stories **small and independent** — split epics into deliverable slices.
- **Trace** each story back to the requirement(s) it satisfies so coverage is
  auditable; make sure the functional requirements are collectively covered.
- Focus on user-observable value; leave implementation/design detail to the SA.
- Number stories (US-1, US-2, …) so downstream stages can reference them.

## Flow
1. `get_stage_brief` — confirm you're on `user_stories`.
2. `get_artifact aidlc-docs/inception/requirements/requirements.md` — read the
   baseline.
3. Generate `stories.md` per the structure above.
4. `submit_artifact` to the produces path.
5. `complete_stage` — validates acceptance (lenient warnings; missing required
   blocks). Because this stage declares `advances_state: requirements_ready`,
   completing it also advances the coarse owned state to `requirements_ready`
   (still BA-owned, "ready to hand off"). You then explicitly `handoff_project`
   to transfer ownership to the Solution Architect.

## Notes
- Ownership and legality are enforced server-side; a 403/422 means it isn't your
  turn or the move isn't allowed. Do not try to force it.
- Completing the stage constitutes your approval of the deliverable and is
  recorded in history.
