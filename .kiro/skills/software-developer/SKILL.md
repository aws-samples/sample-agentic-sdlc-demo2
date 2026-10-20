---
name: software-developer
description: "Software Developer workflow: turn the construction brief into a local Kiro spec, implement and test in Kiro, push to Git, link the repo, and submit for QA. Use when acting as the developer / software-developer."
---
# Software Developer playbook

You implement the design in this Kiro workspace and submit it for QA.

## Steps

1. **Pick up your project.** `list_projects` (state `assigned_to_dev`), then `get_project`.
2. **Read the brief.** `list_artifacts` and `get_artifact` under `aidlc-docs/construction/`
   (`construction-brief.md` and the per-unit task files under `construction/tasks/`).
3. **Materialize a local spec.** Turn the per-unit task files into a Kiro spec at
   `.kiro/specs/<project>/tasks.md` in this workspace, then implement unit-by-unit using Kiro's
   task execution.
4. **Build & test locally.** Implement the code and run the test suite locally until it passes.
5. **Commit & push.** Use Kiro's Git to commit and push to GitHub/GitLab.
6. **Link & submit.** `link_repo` (provide a `git_token` only for a private repo — it is stored
   securely and never shown), then `submit_implementation`.
   - The tests-before-submit gate runs your tests and blocks the submit if they fail.
7. **Design changes?** If the design is wrong/insufficient, `return_for_changes` back to the
   Solution Architect with specifics.
