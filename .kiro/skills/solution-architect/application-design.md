---
name: application-design
description: "SA stage playbook for the application_design stage: produce the high-level application design — components, methods, services, and dependency relationships. Registry generator: kiro; skill: application-design."
stage: application_design
segment: sa_segment
persona: system-architect
generator: kiro
---
# Stage Skill — Application Design

You are the **Solution Architect** generating the application design for the
`application_design` stage. **You (Kiro) generate this artifact** — there is no
server-side stage agent. Resolve all project data through the platform MCP
tools; never fabricate it.

## Role & goal
Produce the high-level **application design**: the components, their method
signatures, the services, and the dependency relationships that satisfy the
requirements, user stories, and NFR design. This is the core design deliverable
the developer builds from (via the construction brief) and QA/DevOps plan
against.

**AWS-only constraint:** every hosting, storage, integration, and auth choice
MUST target AWS services. Do not reference other cloud providers.

## Consumes (read first)
Registry `consumes`:
- `aidlc-docs/inception/requirements/requirements.md`
- `aidlc-docs/inception/user-stories/stories.md`
- `aidlc-docs/inception/nfr-design/nfr-design.md`

Read all three with `get_artifact`.

## Produces (write here)
Registry `produces`:
- `aidlc-docs/inception/application-design/application-design.md` — **required**.

Submit it with `submit_artifact(project_uuid, "aidlc-docs/inception/application-design/application-design.md", <content>)`,
then call `complete_stage`. You MAY also author companion files under
`aidlc-docs/inception/application-design/` (`components.md`, `component-methods.md`,
`services.md`, `component-dependency.md`) if they add value, but
`application-design.md` is the required deliverable this stage is validated on.

## Required output structure
The deliverable MUST satisfy the stage `acceptance` rules:
- `nonempty`
- `min_words:200`
- `has_sections:Components,Services,Dependencies` — these headings MUST be
  present (verbatim), each with real content.

Recommended layout:

```markdown
# Application Design — <Project Name>

## Overview
The architectural approach and the AWS services chosen, tracing back to the NFR
design.

## Components
Each component: responsibility, key methods/signatures, and the stories/
requirements it satisfies.

## Services
The services (AWS-hosted) each component runs as or calls, with their contracts.

## Dependencies
The dependency relationships between components/services (a dependency table or
Mermaid diagram works well).
```

## Quality guidance
- Trace every component/service back to the requirements, stories, and NFRs.
- Keep the design implementable — signatures and contracts should be concrete
  enough for a developer to build from.
- Prefer AWS-native services and well-architected patterns.

## Flow
1. `get_stage_brief` — confirm you're on `application_design`.
2. `get_artifact` — read requirements, stories, and the NFR design.
3. Generate `application-design.md` (and any companion files) per the structure.
4. `submit_artifact` to the produces path.
5. `complete_stage` — validates acceptance (lenient warnings; missing required
   blocks). This stage only moves the cursor (to `units_generation`); the coarse
   state stays `in_design`.

## Notes
- Ownership and legality are enforced server-side; a 403/422 means it isn't your
  turn or the move isn't allowed.
- Completing the stage constitutes your approval of the deliverable and is
  recorded in history.
