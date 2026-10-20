# QA Engineer Walkthrough

As the QA Engineer, you ensure the quality of the implemented code. You receive a project with ingested source code from the developer's repository, along with the full design artifacts, and produce a test plan, a test-execution assessment, and a QA report. You can also run the actual tests with a CodeBuild test runner. You work from the QA Engineer agent in Kiro.

**Your persona:** `qa-engineer` (agent picker `cmd+alt+4`)
**You own the project while it's in:** `assigned_to_qa`, `in_qa`, and `qa_passed`
**You receive from:** the Software Developer (via the Git round-trip)
**You hand off to:** the DevOps Engineer

---

## Before you start

Select the **QA Engineer** agent in Kiro (`cmd+alt+4`) — see [Getting Started](getting-started.md). Ask *"Show me my projects"* (`list_projects`) and look for a project in `assigned_to_qa` — one the developer has linked and submitted through the Git round-trip.

---

## Step 1 — Review the ingested code and design artifacts

Open the assigned project (`get_project`). With `list_artifacts` / `get_artifact` you have access to:

- **The ingested source code** — under `{project_slug}/source/`, synced from the developer's GitHub/GitLab repository.
- **All design artifacts** — requirements, user stories, personas, NFR requirements, NFR design, application design, and units of work.
- **The construction brief** — the consolidated package that guided the implementation.

Familiarize yourself with both the intended design and the actual implementation. Reading these this session also satisfies the review-before-approve hook.

---

## Step 2 — Generate the QA stages

Work each QA stage through the standard loop: `get_stage_brief` → read inputs with `get_artifact` → generate the deliverable in Kiro per the stage Skill → `submit_artifact` → `complete_stage`.

| Stage | What it produces | Runs when |
| --- | --- | --- |
| **Test Planning** | A test plan with traceability from requirements and user stories to test cases, informed by the application design. | Always |
| **Test Execution** | A test-execution assessment of the ingested source code. Can incorporate real results from the CodeBuild test runner. | Always |
| **QA Report** | A final QA report summarizing results, coverage analysis, and quality assessment, with a recommendation to advance or return for changes. | Always |

Key points:

- **Test Planning** can run before you dig into the code — it's grounded in the requirements and design, so it drives comprehensive coverage.
- **Test Execution** can incorporate a real **CodeBuild test run** (Step 3).
- **QA Report** synthesizes everything into a final quality assessment.

---

## Step 3 — Run the tests (optional)

If the ingested repository contains tests, you can execute them:

1. Ask the agent to run the tests. It calls `run_tests`, which triggers a CodeBuild test-runner job on the ingested repository.
2. Results arrive asynchronously — check `get_project` for `qa_test_status` and `qa_test_summary`. The runner writes results to `aidlc-docs/qa/test-results.md`.
3. Fold the real results into your Test Execution assessment.

This gives you both automated test results and the agent's broader quality analysis.

---

## Step 4 — Advance through the QA states

As you complete the QA stages, `complete_stage` advances the coarse owned state where the stage declares it: from `assigned_to_qa` through `in_qa` to `qa_passed`. Review each generated artifact (test plan, test results if applicable, QA report) with `get_artifact` before completing.

---

## Step 5 — Hand off to DevOps or bounce back

From `qa_passed`, you have two options:

### Advance to DevOps
Ask the agent to hand off to DevOps. It calls `handoff_project`, advancing `qa_passed → assigned_to_devops`, and the DevOps Engineer takes over for deployment planning.

### Request changes from the developer
If testing reveals issues that require code changes, `return_for_changes` to the developer with a note. This returns the project to `assigned_to_dev` and flips ownership back to the Software Developer, who will:

1. Make the necessary code changes in Kiro.
2. Push updates to their repository.
3. Re-link and resubmit (which triggers fresh ingestion).
4. Return the project to you for re-testing.

---

## Handling a bounce from DevOps

If the DevOps Engineer hits issues during deployment planning, they can `return_for_changes` to you. The project returns to `assigned_to_qa` and ownership flips back to you. Review the DevOps note, address any QA-related concerns, and hand off again when ready.

---

## Tips

- **Start with test planning before digging into code.** This ensures you're testing against the requirements, not just what was implemented.
- **Use the real test runner when available.** Automated test results provide concrete evidence of code quality.
- **Cross-reference design and implementation.** Look for gaps between what was designed and what was built.
- **Be specific when bouncing back to the developer.** Clear feedback helps them fix issues in one iteration.
- **Check the repository commit and ingestion status.** Make sure you're testing the latest version of the code.

---

## Next

- [DevOps Engineer Walkthrough](walkthrough-devops-engineer.md) — what happens after you hand off.
- [Software Developer Walkthrough](walkthrough-software-developer.md) — what happens if you bounce it back.
- [Handoffs and Change Requests](handoffs-and-change-requests.md) — the QA→Dev and DevOps→QA bounce mechanics.
- [Artifacts and Files](artifacts-and-files.md) — what the QA stages produce and where they're stored.
