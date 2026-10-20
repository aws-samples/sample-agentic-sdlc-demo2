# Business Analyst Walkthrough

As the Business Analyst (BA), you start the project. You turn a rough idea — optionally backed by a requirements document — into structured requirements, user stories, and personas, then hand the project to the Solution Architect. You do all of this from Kiro by talking to the Business Analyst agent.

**Your persona:** `requirements-analyst` (agent picker `cmd+alt+1`)
**You own the project while it's in:** `draft` and `requirements_ready`
**You hand off to:** the Solution Architect

---

## Before you start

Select the **Business Analyst** agent in Kiro (`cmd+alt+1`) — see [Getting Started](getting-started.md). Confirm the connection by asking *"Show me my projects"* (a clean `list_projects`).

Have your idea ready. A good starting idea is small enough to finish inception quickly but rich enough to exercise the workflow — for example an event RSVP and check-in app, an internal helpdesk/ticketing tool, or a document-upload-with-AI-summary service.

---

## Step 1 — Create the project

Ask the agent to create a project, giving it a **name** (for example, `Online Bookstore`) and a short **description**. Under the hood this calls `create_project`, which starts the project in the `draft` state, owned by you.

---

## Step 2 — (Optional) Upload a requirements document

If you already have requirements written down, upload them so the agent uses them as its baseline instead of asking you everything from scratch.

Ask the agent to upload your requirements file (`.md`, `.txt`, `.pdf`, or `.docx`). It calls `upload_requirements`, which requests a short-lived, owner-gated presigned URL and uploads the file directly to S3. PDFs and Word documents are converted to plain text automatically; the extracted text becomes the agent's authoritative requirements baseline.

You can skip this step entirely and describe your idea in the conversation instead — the agent will ask a fuller set of clarifying questions.

---

## Step 3 — Generate the inception stages

The BA segment has two stages. You work each through the same loop: the agent calls `get_stage_brief` for the active stage, reads any inputs with `get_artifact`, generates the deliverable in Kiro per the stage Skill, uploads it with `submit_artifact`, then calls `complete_stage`.

| Stage | What it produces | Runs when |
| --- | --- | --- |
| **Requirements Analysis** | The requirements document. Asks clarifying questions and assesses scope and complexity, which drive later routing. | Always |
| **User Stories** | User stories (INVEST criteria, acceptance criteria) and personas. | When the project is user-facing / multi-persona / complex |

A few things to expect while it runs:

- **If you uploaded a document,** the requirements stage reads it first and only asks about gaps and ambiguities. **If you didn't,** it asks the full clarifying-question set.
- **Answer precisely.** The quality of the requirements tracks the quality of your answers.
- **Review before completing.** After generating a stage's artifact, read it back with `get_artifact` before calling `complete_stage`. Reading it also satisfies the review-before-approve hook.

Acceptance on `complete_stage` is lenient (warnings only; a missing *required* artifact is the one hard block), so it won't nitpick — but reviewing your own output here saves a bounce later. As artifacts are generated they're stored in S3 (displayed under the **`agentic-sdlc`** folder). See [Artifacts and Files](artifacts-and-files.md) for exactly what's produced and where.

---

## Step 4 — Hand off to the Solution Architect

Once requirements and user stories are generated and completed, hand the project to the SA. Ask the agent to hand off; it calls `handoff_project`, which:

- Advances the project `requirements_ready → assigned_to_sa`.
- Transfers ownership to the Solution Architect.

The review-before-approve hook requires that you've read the artifacts this session before it allows the handoff. After the handoff, a `list_projects` shows the project owned by the SA — your part is done unless the SA sends it back.

---

## Handling a bounce (changes requested)

If the SA reviews your artifacts and returns them, ownership flips back to you and the project returns to `requirements_ready`.

To address it:

1. Ask the agent to `get_project` — the SA's note appears as `latest_change_request`.
2. Make the requested adjustments to the requirements or stories (regenerate and `submit_artifact` as needed).
3. When you're satisfied, `handoff_project` back to the Solution Architect.

There's no limit to how many times a project can bounce; every transition — including the SA's note — is recorded in the project's history for audit. See [Handoffs and Change Requests](handoffs-and-change-requests.md).

---

## Tips

- **Front-load context.** Uploading a solid requirements document (or pasting a detailed description) dramatically shortens the question phase.
- **Answer precisely.** Vague answers lead the agent to ask follow-ups or make assumptions. If you don't know something, say so — the agent handles that gracefully.
- **Review before you hand off.** Read each artifact with `get_artifact` before handing off. It's much cheaper to adjust now than after an SA bounce.

---

## Next

- [Solution Architect Walkthrough](walkthrough-solution-architect.md) — what happens after you hand off.
- [Handoffs and Change Requests](handoffs-and-change-requests.md) — the full state machine and bounce mechanics.
- [Artifacts and Files](artifacts-and-files.md) — what the BA stages produce and where.
