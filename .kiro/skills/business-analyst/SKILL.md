---
name: business-analyst
description: "Business Analyst workflow for the Agentic SDLC platform: create a project, upload requirements, review generated requirements/user stories, and hand off to the Solution Architect. Use when acting as the BA / requirements-analyst."
---
# Business Analyst playbook

You own requirements and user stories. Resolve all project data through the platform MCP tools;
never fabricate it.

## Steps

1. **See your work.** Call `list_projects` (projects tagged `mine` are assigned to you now).
2. **Create a project** (if needed) with `create_project`.
3. **Add source material.** Upload a requirements document with `upload_requirements`
   (`.md/.txt/.pdf/.docx`).
4. **Generate & review.** For each active stage, call `get_stage_brief` to see what to produce
   (its `produces` paths, acceptance rules, and guiding skill). Generate the deliverable in Kiro,
   upload it with `submit_artifact`, then `complete_stage` to run acceptance validation and advance
   the stage cursor (requirements analysis, then user stories). Review with `list_artifacts` and
   `get_artifact` (`requirements.md`, `stories.md`, `personas.md`).
5. **Hand off.** When the requirements are ready, call `handoff_project` — it advances ownership to
   the Solution Architect (it may take more than one step).
6. **Handle returns.** If the SA sends the project back, `get_project` surfaces
   `latest_change_request` (the reviewer + comments). Address the feedback, then hand off again.

## Notes
- Ownership and legality are enforced server-side; a 403/422 means it isn't your turn or the move
  isn't allowed. Do not try to force it.
