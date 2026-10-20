# Artifacts and Files

Everything the platform generates is a file in Amazon S3, read through the MCP server's `get_artifact` / `list_artifacts` tools from inside Kiro. This page explains what gets generated, where it lives, where the authoritative project record lives, and one naming detail worth knowing.

---

## Two kinds of stored data

Per project, the platform keeps two distinct things in two distinct stores:

1. **The project record** — the authoritative source for handoff state, owning role, the stage cursor, and history. It lives in **Amazon DynamoDB**, not in S3.
2. **The artifacts** — all the generated documents plus the ingested source. These live in S3.

| Where | Store | Owner | Contents |
| --- | --- | --- | --- |
| `AidlcProjects` item `PROJECT#{uuid}` / `META` | DynamoDB | Project API | Authoritative project record: current handoff state, owning role, stage cursor, and full history. |
| `{project_slug}/aidlc-docs/…` | S3 | Persona (via Kiro) | Every generated artifact. |
| `{project_slug}/source/…` | S3 | CodeBuild ingest | The ingested source code from the linked repository. |

> The S3 prefix `_platform/projects/` is **reserved but unused** — it stays reserved as a path-traversal guard so a client-supplied `project_uuid` can never collide with a `{slug}/` artifact tree. It is **not** the record store; the record is the DynamoDB item above.

---

## Artifacts by stage

Artifacts accumulate as the project moves through the stages. Each stage's `produces` contract (from the workflow registry) defines exactly which paths it writes; `submit_artifact` is constrained to those paths. Here's what each stage writes, in order.

### Business Analyst segment

| Key | Stage | Contents |
| --- | --- | --- |
| `{project_slug}/aidlc-docs/inception/requirements/uploaded/{file}` | BA upload | The original requirements document you uploaded (optional). |
| `{project_slug}/aidlc-docs/inception/requirements/uploaded/extracted/{file}.txt` | Document extraction | Plain-text extraction from an uploaded `.pdf` / `.docx`. |
| `{project_slug}/aidlc-docs/inception/requirements/requirements.md` | Requirements Analysis | The functional and non-functional requirements. |
| `{project_slug}/aidlc-docs/inception/requirements/requirement-verification-questions.md` | Requirements Analysis | The clarifying-question log. |
| `{project_slug}/aidlc-docs/inception/user-stories/stories.md` | User Stories | User stories with acceptance criteria. |
| `{project_slug}/aidlc-docs/inception/user-stories/personas.md` | User Stories | User personas. |

### Solution Architect segment

| Key | Stage | Contents |
| --- | --- | --- |
| `{project_slug}/aidlc-docs/inception/plans/execution-plan.md` | Workflow Planning | Which design stages will run, and why. |
| `{project_slug}/aidlc-docs/inception/plans/execution-plan-diagram.md` | Workflow Planning | A flowchart of the planned workflow. |
| `{project_slug}/aidlc-docs/inception/nfr-requirements/nfr-requirements.md` | NFR Requirements | Non-functional requirements covering performance, scalability, security, and operational concerns. |
| `{project_slug}/aidlc-docs/inception/nfr-design/nfr-design.md` | NFR Design | Non-functional design addressing NFR requirements with architectural patterns and solutions. |
| `{project_slug}/aidlc-docs/inception/application-design/*.md` | Application Design | Components, methods, services, dependencies, and the consolidated design (plus an AWS architecture diagram). |
| `{project_slug}/aidlc-docs/inception/application-design/unit-of-work.md` | Units Generation | The units of work, with boundaries and responsibilities. |
| `{project_slug}/aidlc-docs/inception/application-design/unit-of-work-dependency.md` | Units Generation | Dependencies between units. |
| `{project_slug}/aidlc-docs/inception/application-design/unit-of-work-story-map.md` | Units Generation | Mapping of user stories to units. |

### Software Developer segment

| Key | Stage | Contents |
| --- | --- | --- |
| `{project_slug}/aidlc-docs/construction/construction-brief.md` | Construction Handoff | The consolidated, ready-to-paste construction brief. |
| `{project_slug}/aidlc-docs/construction/tasks/*` | Construction Handoff | One task file per unit of work. |
| `{project_slug}/source/*` | Repository Ingestion | The ingested source code from the linked GitHub/GitLab repository. |

### QA Engineer segment

| Key | Stage | Contents |
| --- | --- | --- |
| `{project_slug}/aidlc-docs/qa/test-plan.md` | Test Planning | Test planning document with traceability from requirements to test cases. |
| `{project_slug}/aidlc-docs/qa/test-results.md` | Test Execution | Test execution results (written by the CodeBuild test runner when "Run tests" is used). |
| `{project_slug}/aidlc-docs/qa/qa-report.md` | QA Report | Final QA assessment and report on the tested code. |

### DevOps Engineer segment

| Key | Stage | Contents |
| --- | --- | --- |
| `{project_slug}/aidlc-docs/devops/deployment-plan.md` | Deployment Planning | Deployment plan covering infrastructure, environments, and deployment strategies. |
| `{project_slug}/aidlc-docs/devops/release-runbook.md` | Release | Release runbook and procedures for production deployment. |

> The exact file set varies by project. Optional stages (user stories, application design, units generation) only run when the project's scope and complexity call for them, so smaller projects produce fewer artifacts. Each stage's authoritative `produces` list is defined in the workflow registry's `stage_contracts`.

---

## Working with artifacts in Kiro

- **List.** `list_artifacts` shows everything under the project (labeled as the `agentic-sdlc` folder).
- **Read.** `get_artifact` returns a file's content. Reading the upstream deliverables also satisfies the review-before-approve hook.
- **Write.** You never write to S3 directly — you generate a deliverable in Kiro and call `submit_artifact(path, content)`, which uploads it via an owner-gated presigned PUT constrained to the active stage's `produces` paths.

---

## Uploads

When a BA uploads a requirements document, the MCP server requests a short-lived presigned upload URL from the Project API and uploads the file straight to S3, under `{project_slug}/aidlc-docs/inception/requirements/uploaded/`. The URL is valid for only about 300 seconds and is owner-gated and path-constrained. PDFs and Word documents are then extracted to plain text automatically.

---

## Next

- [Business Analyst Walkthrough](walkthrough-business-analyst.md) — produces the requirements and stories artifacts.
- [Software Developer Walkthrough](walkthrough-software-developer.md) — consumes the construction brief.
- [Handoffs and Change Requests](handoffs-and-change-requests.md) — where the DynamoDB project record and its history come from.
