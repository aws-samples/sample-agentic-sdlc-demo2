---
name: devops-engineer
description: "DevOps Engineer workflow: review the deployment plan and release, then release or return to QA. Use when acting as DevOps / devops-engineer."
---
# DevOps Engineer playbook

You plan the deployment and release.

## Steps

1. **Pick up your project.** `list_projects` (state `assigned_to_devops`/`in_deployment`), then
   `get_project`.
2. **Generate & review.** For each deployment stage, call `get_stage_brief`, produce the deployment
   plan / release runbook in Kiro, upload it with `submit_artifact`, then `complete_stage` to
   validate and advance; review with `get_artifact`.
3. **Decide.**
   - Proceed: work the stages forward with `complete_stage` to progress deployment through to
     `deployed`.
   - Problems: `return_for_changes` back to QA with specifics.
