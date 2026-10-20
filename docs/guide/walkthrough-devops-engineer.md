# DevOps Engineer Walkthrough

As the DevOps Engineer, you handle the deployment and release of the tested application. You receive a project that has passed QA, along with the full design artifacts and tested source code, and produce deployment plans and release runbooks to bring the application to production. You work from the DevOps Engineer agent in Kiro.

**Your persona:** `devops-engineer` (agent picker `cmd+alt+5`)
**You own the project while it's in:** `assigned_to_devops` and `in_deployment`
**You hand off to:** the `deployed` state (terminal)

---

## Before you start

Select the **DevOps Engineer** agent in Kiro (`cmd+alt+5`) — see [Getting Started](getting-started.md). Ask *"Show me my projects"* (`list_projects`) and look for a project in `assigned_to_devops` — one the QA Engineer has tested and passed.

---

## Step 1 — Review the tested code and artifacts

Open the assigned project (`get_project`). With `list_artifacts` / `get_artifact` you have access to:

- **The tested source code** — under `{project_slug}/source/`, the same code that passed QA.
- **QA artifacts** — test plan, test results, and QA report showing the code was validated.
- **Design artifacts** — requirements, NFR requirements, NFR design, application design, AWS architecture diagram, and units of work.

Understand the application's architecture, infrastructure requirements, and quality status. Reading these this session also satisfies the review-before-approve hook.

---

## Step 2 — Generate the DevOps stages

Work each stage through the standard loop: `get_stage_brief` → read inputs with `get_artifact` → generate the deliverable in Kiro per the stage Skill → `submit_artifact` → `complete_stage`.

| Stage | What it produces | Runs when |
| --- | --- | --- |
| **Deployment Planning** | A deployment plan covering infrastructure requirements, environment setup, CI/CD pipelines, monitoring, and deployment strategies. References the NFR design and AWS architecture diagram. | Always |
| **Release** | Release runbooks, deployment procedures, rollback plans, and operational documentation. May include infrastructure-as-code and deployment automation. | Always |

Key points:

- **Deployment Planning** leverages the SA's NFR design and AWS architecture diagram to create concrete infrastructure and deployment plans.
- **Release** focuses on the operational aspects — how to deploy, monitor, and maintain the application in production.
- Both stages consider the specific technology stack and architectural decisions made during design.

---

## Step 3 — Advance through the deployment states

As you complete the DevOps stages, `complete_stage` advances the coarse owned state where the stage declares it: from `assigned_to_devops` through `in_deployment` to `deployed`. Review each generated artifact (deployment plan, release runbook) with `get_artifact` before completing.

---

## Step 4 — Complete deployment or bounce back

From `in_deployment`, you have two options:

### Complete to the terminal state
Complete the final stage to advance the project to the `deployed` state, which is terminal. The project has moved through all six personas from initial idea to production deployment.

### Request changes from QA
If deployment reveals issues that require re-testing or QA attention, `return_for_changes` to the QA Engineer with a note. This returns the project to `assigned_to_qa` and flips ownership back to QA.

Common reasons for bouncing back to QA include:
- Infrastructure compatibility issues discovered during deployment planning
- Security concerns that require additional testing
- Performance requirements that need validation
- Missing test coverage for deployment-critical functionality

---

## Step 5 — After deployment

Once the project reaches `deployed`, it appears in the Maintenance Specialist's read-only cross-project view with a "Deployed" status. While the formal workflow is complete, the Maintenance Specialist can track operational status, repository information, and overall project health across all deployed applications. See the [Maintenance Dashboard Walkthrough](walkthrough-maintenance-dashboard.md).

---

## Tips

- **Leverage the AWS architecture diagram.** The SA's architecture design should guide your infrastructure and deployment planning.
- **Reference NFR requirements.** Performance, scalability, and security requirements from the SA inform your deployment strategy.
- **Check QA test results.** Understanding what was tested helps you plan monitoring and operational validation.
- **Plan for rollback.** Always include rollback procedures in your release runbook.
- **Consider the technology stack.** Tailor your deployment approach to the specific technologies chosen during design.
- **Use infrastructure-as-code.** Generate CloudFormation, CDK, or Terraform templates when appropriate.

---

## Next

- [Maintenance Dashboard](walkthrough-maintenance-dashboard.md) — the read-only operations view of deployed applications.
- [QA Engineer Walkthrough](walkthrough-qa-engineer.md) — what happens if you bounce it back.
- [Handoffs and Change Requests](handoffs-and-change-requests.md) — the DevOps→QA bounce mechanics.
- [Artifacts and Files](artifacts-and-files.md) — what the DevOps stages produce and where they're stored.
