# Software Developer Walkthrough

As the Software Developer, you're the bridge from design to tested code. You assemble a finished design into a **construction brief**, implement the code in Kiro, then link your repository back to the platform so QA and DevOps can operate on the real code. You work from the Software Developer agent in Kiro.

**Your persona:** `software-developer` (agent picker `cmd+alt+3`)
**You own the project while it's in:** `assigned_to_dev` and `implementation_submitted`
**You receive from:** the Solution Architect
**You hand off to:** the QA Engineer (via the Git round-trip)

> Important: the platform does **not** generate application code as a stage. Its job is to produce a construction-ready brief and then ingest your repository for the downstream QA and DevOps stages. You implement the code yourself in Kiro. This is by design.

---

## Before you start

Select the **Software Developer** agent in Kiro (`cmd+alt+3`) — see [Getting Started](getting-started.md). Ask *"Show me my projects"* (`list_projects`) and look for a project in `assigned_to_dev` — one the SA has handed to you.

---

## Step 1 — Assemble the construction brief

Work the `construction_handoff` stage through the standard loop: the agent calls `get_stage_brief`, reads all the inception and design artifacts with `get_artifact`, generates the **construction kickoff package** in Kiro, uploads it with `submit_artifact`, then calls `complete_stage`. This produces:

- **`construction-brief.md`** — a single consolidated document that folds together the requirements, user stories, design, and units of work, with a ready-to-paste coding-agent prompt at the top.
- **Per-unit task files** — one task file per unit of work, so you can drive construction unit by unit.

These land under the project's artifact folder (displayed as **`agentic-sdlc`**). See [Artifacts and Files](artifacts-and-files.md) for exact locations.

> Note: `construction_handoff` only advances the **stage cursor** — it does **not** advance the lifecycle state. The Dev→QA boundary is the Git round-trip in Step 3, not a `complete_stage`.

---

## Step 2 — Implement the code in Kiro

1. Read **`construction-brief.md`** with `get_artifact`. It's designed to be a self-contained prompt: everything a coding agent needs to start building, without re-reading the whole project.
2. Implement the code in Kiro — a common pattern is to materialize a local Kiro spec at `.kiro/specs/<project>/tasks.md` and drive the build unit by unit using Kiro task execution, using the per-unit task files.
3. Build and run the test suite locally until green.

Work unit by unit using the per-unit task files if the project was decomposed into multiple units. Each task file scopes one buildable piece.

---

## Step 3 — Link your repository and submit for QA

After implementing the code and pushing it to GitHub or GitLab:

1. Ask the agent to link your repository, giving the repository URL (e.g., `https://github.com/username/project-repo`) and, optionally, a branch (defaults to `main`). For a private repository, provide a personal access token — it's stored securely in Secrets Manager, never logged or displayed. This calls `link_repo`.
2. Ask the agent to submit for QA. This calls `submit_implementation`.

The **tests-before-submit** enforcement hook runs your tests first and blocks `submit_implementation` if they fail (it never blocks `return_for_changes`). On success:

- The platform records your repository metadata and triggers a **CodeBuild ingestion** job.
- CodeBuild clones your repository and `s3 sync`s the source into `{project_slug}/source/`.
- Once ingestion completes, the project advances to `assigned_to_qa` and the QA Engineer takes over.

---

## Step 4 — Bounce back, or finish

You have two ways to close out your ownership:

- **Request design changes.** If the design has a gap or a problem you hit during construction, `return_for_changes` to the SA. This returns the project to `design_ready` and flips ownership to the Solution Architect, who revises the design and hands it back to you.
- **Complete the Git round-trip.** When you link your repository and ingestion completes, the project advances to the QA Engineer. Your direct ownership ends here, but your code continues through the QA and DevOps stages.

---

## What the brief contains

The construction brief is assembled from the upstream artifacts, so it reflects the whole project:

- The **requirements** (functional and non-functional).
- The **user stories** and **personas**.
- The **application design** — components, services, dependencies — and the **AWS architecture diagram**.
- The **units of work** and their dependency and story maps.
- A **prompt preamble** written so you can paste it straight into a coding agent.

Because it's generated from the same artifacts the SA approved, the brief stays consistent with the design. If you find it's missing something, that's usually a signal to bounce the project back to the SA rather than to patch around it.

---

## Tips

- **Start from the brief, not the raw artifacts.** The brief already consolidates everything; you rarely need to open the individual design files unless you want detail on a specific unit.
- **Use per-unit task files for parallel or incremental work.** They're scoped so you (or Kiro task execution) can build one unit at a time.
- **Bounce early if the design is wrong.** It's cheaper to fix a design gap with the SA than to work around it in code.
- **Ensure your repository is complete before linking.** The QA Engineer works with whatever is in your repository at the time of ingestion.
- **Use clear commit messages.** The ingested commit SHA is recorded and visible to QA and DevOps.

---

## Next

- [QA Engineer Walkthrough](walkthrough-qa-engineer.md) — what happens after you submit for QA.
- [Handoffs and Change Requests](handoffs-and-change-requests.md) — how the bounce back to the SA works.
- [Artifacts and Files](artifacts-and-files.md) — where the brief and task files live.
- [Solution Architect Walkthrough](walkthrough-solution-architect.md) — what happens if you bounce it back.
