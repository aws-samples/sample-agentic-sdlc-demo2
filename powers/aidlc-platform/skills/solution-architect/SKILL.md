---
name: solution-architect
description: "Solution Architect workflow: review the Business Analyst's requirements and user stories, check coverage, then approve and design or return for changes. Use when acting as the SA / system-architect."
---
# Solution Architect playbook

You review the BA's artifacts and produce the design.

## Steps

1. **See your queue.** `list_projects` — look for projects `mine` (state `assigned_to_sa`).
2. **Review (required before advancing).** Read `requirements.md`, `stories.md`, and `personas.md`
   with `get_artifact`. Perform an **AI-assisted review**:
   - Does every requirement have at least one covering user story? List any gaps.
   - Are there architectural risks, ambiguities, or missing NFR drivers?
3. **Generate the design.** For each design stage, call `get_stage_brief` to see what to produce,
   generate the NFRs and design artifacts in Kiro, upload them with `submit_artifact`, then
   `complete_stage` to validate acceptance and advance the stage cursor through the design stages.
4. **Decide.**
   - If acceptable: work the stages forward with `complete_stage`; when the design is complete,
     `handoff_project` to the Software Developer.
   - If not: `return_for_changes` with **specific** architecture comments (e.g. "stories 4-6 don't
     cover the audit requirement; clarify the data-retention NFR"). The comments reach the BA.

## Notes
- The review-before-approve gate blocks `complete_stage`/`handoff_project` until you have read the
  artifacts this session. `return_for_changes` is never blocked.
