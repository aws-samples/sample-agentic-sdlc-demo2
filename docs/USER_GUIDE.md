# Agentic SDLC — Kiro Frontend User Guide

Six AI personas collaborate on one software project inside Kiro IDE. Each persona
is a Kiro **agent** backed by the same **`aidlc-platform` MCP server**, which talks
to the live backend (Project API + DynamoDB + S3) on account `123456789012`.
Kiro IDE is the only frontend — there is no web UI, portal, or hosted login page;
everything happens by talking to a persona agent that calls MCP tools. A project
moves forward through an explicit **handoff state machine**; every transition is
authorized server-side by the caller's Cognito group (RBAC), so a persona can only
act when it is genuinely their turn. Each persona generates its stage deliverables
with Kiro's own agent + model + a versioned stage Skill — there is no AgentCore
Runtime, Gateway, or Memory in the flow.

## The handoff state machine

```
draft → requirements_ready → assigned_to_sa → in_design → design_ready →
ready_for_dev → assigned_to_dev → implementation_submitted → assigned_to_qa →
in_qa → qa_passed → assigned_to_devops → in_deployment → deployed
```

The four "send it back" bounces:

- SA → BA   (`assigned_to_sa → requirements_ready`)
- Dev → SA  (`assigned_to_dev → design_ready`)
- QA → Dev  (`in_qa → assigned_to_dev`)
- DevOps → QA (`in_deployment → assigned_to_qa`)

---

## One-time setup

### 1. Install the MCP server

```bash
cd code/kiro-mcp
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Note two paths you will reference from Kiro:

- `AIDLC_MCP_PYTHON` = absolute path to `code/kiro-mcp/.venv/bin/python`
- `AIDLC_MCP_HOME`   = absolute path to `code/kiro-mcp`

### 2. Pull config from the live stack outputs

```bash
aws cloudformation describe-stacks --stack-name AIDLCPlatformWorkshop \
  --query "Stacks[0].Outputs" --output table --no-cli-pager
```

Map them to environment variables (export in your shell so Kiro's `${VAR}`
expansion resolves them):

export AWS_PROFILE="<your-aws-profile>"
export AWS_REGION="us-east-1"

# Use the absolute path to your checkout of this repo:
export AIDLC_MCP_HOME="<repo-root>/code/kiro-mcp"
export AIDLC_MCP_PYTHON="<repo-root>/code/kiro-mcp/.venv/bin/python"
export AIDLC_AUTH_MODE="password"

export AIDLC_PROJECT_API_URL="https://exampleapiid01.execute-api.us-east-1.amazonaws.com/prod/"
export AIDLC_COGNITO_USER_POOL_ID="us-east-1_EXAMPLE1"
export AIDLC_COGNITO_CLIENT_ID="exampleclientid0123456789"
export AIDLC_AWS_REGION="us-east-1"
export AIDLC_FILES_BUCKET="agentcore-files-123456789012-us-east-1"

# persona test-user credentials (throwaway test users; don't commit)
export AIDLC_BA_USERNAME="ba-tester@example.com";      export AIDLC_BA_PASSWORD="REPLACE_ME"
export AIDLC_SA_USERNAME="sa-tester@example.com";      export AIDLC_SA_PASSWORD="REPLACE_ME"
export AIDLC_DEV_USERNAME="testuser@example.com";      export AIDLC_DEV_PASSWORD="REPLACE_ME"
export AIDLC_QA_USERNAME="qa-tester@example.com";      export AIDLC_QA_PASSWORD="REPLACE_ME"
export AIDLC_DEVOPS_USERNAME="devops-tester@example.com"; export AIDLC_DEVOPS_PASSWORD="REPLACE_ME"
export AIDLC_MAINT_USERNAME="maint-tester@example.com"; export AIDLC_MAINT_PASSWORD="REPLACE_ME"


| Env var | Stack output | Example value |
| --- | --- | --- |
| `AIDLC_PROJECT_API_URL` | `KiroProjectApiUrl` | `https://exampleapiid01.execute-api.us-east-1.amazonaws.com/prod/` |
| `AIDLC_COGNITO_USER_POOL_ID` | `KiroCognitoUserPoolId` | `us-east-1_EXAMPLE1` |
| `AIDLC_COGNITO_CLIENT_ID` | `KiroCognitoClientId` | `exampleclientid0123456789` |
| `AIDLC_AWS_REGION` | deploy region | `us-east-1` |
| `AIDLC_FILES_BUCKET` | `KiroFilesBucket` | `agentcore-files-123456789012-us-east-1` |

Also set `AIDLC_AUTH_MODE=password`, and make sure ambient AWS credentials are
available (your AWS profile, `<your-aws-profile>`) for S3 artifact reads. There is no
`AIDLC_RUNTIME_ARN` — AgentCore was removed, so no runtime ARN is configured.
See [docs/deployment-steps.md](deployment-steps.md) for the full deploy and
[code/kiro-mcp/docs/setup.md](../code/kiro-mcp/docs/setup.md) for MCP config.

### 3. Provide persona credentials

The repo agents support in-window persona switching, so set one username/password
pair per persona. Use the six pre-provisioned Cognito test users the stack prints.

| Persona | Username env / Password env | Test user |
| --- | --- | --- |
| BA | `AIDLC_BA_USERNAME` / `AIDLC_BA_PASSWORD` | ba-tester@example.com / REPLACE_ME |
| SA | `AIDLC_SA_USERNAME` / `AIDLC_SA_PASSWORD` | sa-tester@example.com / REPLACE_ME |
| Dev | `AIDLC_DEV_USERNAME` / `AIDLC_DEV_PASSWORD` | testuser@example.com / REPLACE_ME |
| QA | `AIDLC_QA_USERNAME` / `AIDLC_QA_PASSWORD` | qa-tester@example.com / REPLACE_ME |
| DevOps | `AIDLC_DEVOPS_USERNAME` / `AIDLC_DEVOPS_PASSWORD` | devops-tester@example.com / REPLACE_ME |
| Maint | `AIDLC_MAINT_USERNAME` / `AIDLC_MAINT_PASSWORD` | maint-tester@example.com / REPLACE_ME |

Never commit credentials; export them in your shell/OS environment.

### 4. Load into Kiro

Either **open this repo in Kiro** (the six persona agents in `.kiro/agents/`, the
enforcement hooks in `.kiro/hooks/`, and the skills in `.kiro/skills/` are picked
up automatically), or **install the Power** from `powers/aidlc-platform/`.

### 5. Switch persona

Use the keyboard shortcut or the agent picker:

| Persona | Shortcut |
| --- | --- |
| Business Analyst | `cmd+alt+1` |
| Solution Architect | `cmd+alt+2` |
| Software Developer | `cmd+alt+3` |
| QA Engineer | `cmd+alt+4` |
| DevOps Engineer | `cmd+alt+5` |
| Maintenance Specialist | `cmd+alt+6` |

**Sanity check:** select any persona and ask *"Show me the projects I'm working
on."* A successful `list_projects` confirms auth, config, and connectivity.

---

## How you interact

You talk to each persona in natural language; the agent calls MCP tools under the
hood. The tools that matter across personas:

- **Visibility:** `list_projects`, `get_project` (shows state, owner, and
  `latest_change_request`)
- **Artifacts:** `list_artifacts`, `get_artifact` (reading these also satisfies the
  review gate)
- **Generation (per stage):** `get_stage_brief` returns the active stage contract
  (its `produces` / `consumes` / `acceptance` and the guiding `skill`); you read the
  `consumes` inputs with `get_artifact`, generate the deliverable in Kiro following
  the Skill, upload it with `submit_artifact(path, content)` (an owner-gated
  presigned PUT, constrained to the declared `produces` paths), then call
  `complete_stage` to run acceptance and advance the stage cursor
- **Movement:** `complete_stage` (advances the stage cursor and, where the stage
  declares it, the coarse owned state), `handoff_project` (transfer ownership to the
  next persona), `return_for_changes` (bounce back with comments)

Acceptance in v1 is **lenient**: `complete_stage` emits warnings only, and the one
hard block is a missing *required* artifact.

Two client-side enforcement hooks (additive to the server-side RBAC) keep everyone
honest:

- **Review-before-approve** — blocks `complete_stage`/`handoff_project` until you
  have actually read the artifacts this session (`return_for_changes` is never
  blocked).
- **Tests-before-submit** — runs the developer's tests and blocks
  `submit_implementation` if they fail (also never blocks `return_for_changes`).

---

## Persona 1 — Business Analyst (`cmd+alt+1`)

**Owns:** requirements + user stories. **States:** `draft → requirements_ready`,
then hands to SA.

1. *"Show my projects"* → `list_projects`.
2. *"Create a project called X"* → `create_project` (starts in `draft`).
3. *"Upload this requirements doc"* → `upload_requirements` (optional;
   `.md/.txt/.pdf/.docx`).
4. *"Generate the requirements analysis"* → the agent calls `get_stage_brief`,
   reads any uploaded doc with `get_artifact`, generates the deliverable per the
   Skill, uploads it with `submit_artifact`, then `complete_stage`. Repeat the same
   brief→generate→submit→complete loop for the `user_stories` stage.
5. Review the output: `list_artifacts`, then `get_artifact` on the requirements and
   user-stories artifacts.
6. *"Hand this off to the architect"* → `handoff_project` (transfers ownership to
   the SA).
7. **If the SA returns it:** `get_project` surfaces `latest_change_request` (who +
   comments). Address the feedback, regenerate, hand off again.

## Persona 2 — Solution Architect (`cmd+alt+2`)

**Owns:** review of BA artifacts + design/NFRs. **States:**
`assigned_to_sa → in_design → design_ready`, then hands to Dev.

1. *"What's in my queue?"* → `list_projects` (look for `mine` at `assigned_to_sa`).
2. **Review first (required):** `get_artifact` on `requirements.md`, `stories.md`,
   `personas.md`. Do an AI-assisted review — does every requirement have a covering
   story? Any architectural risks or missing NFR drivers?
3. **Decide:**
   - Acceptable → work the design stages in order (`workflow_planning`,
     `nfr_requirements`, `nfr_design`, `application_design`, `units_generation`,
     `inception_complete`), each through the brief→generate→submit→complete loop;
     review each artifact with `get_artifact`.
   - Not acceptable → `return_for_changes` with **specific** comments (they reach
     the BA).
4. When the design is complete → `handoff_project` to the Software Developer.

*(The review gate blocks `complete_stage`/`handoff_project` until you have read the
artifacts this session.)*

## Persona 3 — Software Developer (`cmd+alt+3`)

**Owns:** implementation. **States:**
`assigned_to_dev → implementation_submitted → assigned_to_qa`. This is the external
coding phase — you build in Kiro and push to Git; the platform ingests the repo.

1. *"Show my project"* → `list_projects` (state `assigned_to_dev`), then
   `get_project`.
2. Work the `construction_handoff` stage through the brief→generate→submit→complete
   loop to assemble the construction brief at
   `aidlc-docs/construction/construction-brief.md` (plus per-unit task files), then
   read it with `get_artifact`. Note: `construction_handoff` only moves the stage
   cursor — it does **not** auto-advance the lifecycle state; the Dev→QA boundary is
   the Git round-trip below.
3. Materialize a local Kiro spec at `.kiro/specs/<project>/tasks.md`, then implement
   unit-by-unit using Kiro task execution.
4. Build and run the test suite locally until green.
5. Commit and push to GitHub/GitLab with Kiro's Git.
6. `link_repo` (pass a `git_token` only for a private repo — it is stored securely,
   never shown), then `submit_implementation`. The tests-before-submit gate runs
   your tests and blocks submit if they fail; on success the CodeBuild ingest runs
   and the project advances to `assigned_to_qa`.
7. **If the design is wrong:** `return_for_changes` back to the SA with specifics.

## Persona 4 — QA Engineer (`cmd+alt+4`)

**Owns:** testing the ingested code. **States:**
`assigned_to_qa → in_qa → qa_passed`, then hands to DevOps.

1. *"Pick up my project"* → `list_projects` (`assigned_to_qa`/`in_qa`),
   `get_project`.
2. *"Run the tests"* → `run_tests` starts the CodeBuild test-runner. Results arrive
   asynchronously — check `get_project` for `qa_test_status` / `qa_test_summary`.
3. *"Generate the test plan/report"* → work the `test_planning`, `test_execution`,
   and `qa_report` stages through the brief→generate→submit→complete loop; review
   with `get_artifact`.
4. **Review (required):** read the test plan / results / `qa_report`. AI-assisted
   review — are tests adequate? uncovered risks? flaky/failing areas?
5. **Decide:**
   - Pass → `complete_stage` through `qa_passed`, then `handoff_project` to DevOps.
   - Fail → `return_for_changes` to the Developer with failing details.

## Persona 5 — DevOps Engineer (`cmd+alt+5`)

**Owns:** deployment planning + release. **States:**
`assigned_to_devops → in_deployment → deployed`.

1. *"Pick up my project"* → `list_projects` (`assigned_to_devops`/`in_deployment`),
   `get_project`.
2. *"Generate the deployment plan / release runbook"* → work the
   `deployment_planning` and `release` stages through the
   brief→generate→submit→complete loop; review with `get_artifact`.
3. **Decide:**
   - Proceed → `complete_stage` through to `deployed` (terminal — the project is
     done).
   - Problems → `return_for_changes` back to QA with specifics.

## Persona 6 — Maintenance Specialist (`cmd+alt+6`)

**Read-only.** Owns no segment. Use `list_projects` / `get_project` to see every
project's assigned persona, stage, and repo/QA status, and `get_artifact` to inspect
documents. It **cannot** generate, hand off, or change state — it reports status
only (its toolset excludes all write/transition tools).

---

## A full run, end to end

BA creates the project and uploads requirements → generates requirements + stories
via brief→submit→complete → **handoff to SA**. SA reviews, generates the design
stages → **handoff to Dev**. Dev assembles + reads the construction brief,
implements in Kiro, pushes, `link_repo` + `submit_implementation` → **CodeBuild
ingest advances to QA**. QA runs tests, generates the QA artifacts, passes →
**handoff to DevOps**. DevOps plans and releases → `deployed`. At any review point a
persona can `return_for_changes` to bounce it one step back with comments; the
receiving persona sees the note via `get_project`'s `latest_change_request`.

## Things to keep in mind

- **RBAC is server-authoritative.** A `403` means it isn't your turn; a `422` means
  the move is illegal. The agents will not (and should not) force it — switch to the
  persona that owns the current state.
- **Gates are per-session.** Read artifacts before `complete_stage`/`handoff_project`;
  tests must pass before `submit_implementation`. Neither gate blocks
  `return_for_changes`.
- **Credentials expire.** If a call returns 401, refresh your AWS credentials
  and retry.
- **No web UI.** Kiro IDE is the only frontend — no portal, hosted login page, or
  status-badge UI. All config comes from stack outputs; everything happens inside
  Kiro by talking to a persona agent.

---

## Testing in a separate Kiro window

A common workflow is to run the personas in one Kiro window while keeping another
open for fixing code. Two things to understand first:

- **Same repo, not a new folder.** The persona agents, hooks, skills, and MCP
  config all live in this repo's `.kiro/`. The test window must open **this same
  repo folder** — a fresh empty folder won't have any of it.
- **Env vars must exist before Kiro launches, and be approved.** Kiro expands
  `${VAR}` in an MCP `env` block only for variables you have **explicitly approved**
  under **Settings -> "Mcp Approved Env Vars"** (unapproved vars trigger a security
  warning and are not expanded). The variable also has to be present in the
  environment Kiro was launched from, so launch Kiro from the shell where you set
  them.

The `AIDLC_*` names to approve once under Settings -> "Mcp Approved Env Vars":

```
AIDLC_MCP_PYTHON  AIDLC_MCP_HOME  AIDLC_AUTH_MODE
AIDLC_PROJECT_API_URL  AIDLC_COGNITO_USER_POOL_ID  AIDLC_COGNITO_CLIENT_ID
AIDLC_AWS_REGION  AIDLC_FILES_BUCKET
AIDLC_BA_USERNAME  AIDLC_BA_PASSWORD  AIDLC_SA_USERNAME  AIDLC_SA_PASSWORD
AIDLC_DEV_USERNAME  AIDLC_DEV_PASSWORD  AIDLC_QA_USERNAME  AIDLC_QA_PASSWORD
AIDLC_DEVOPS_USERNAME  AIDLC_DEVOPS_PASSWORD  AIDLC_MAINT_USERNAME  AIDLC_MAINT_PASSWORD
```

### macOS / Linux (zsh/bash)

```bash
cd /path/to/AST            # the repo root
# refresh your AWS credentials if needed (e.g. `aws sso login`, or renew your profile's credentials)
source scripts/kiro-testing-env.sh
```

The script pulls config + all six persona test-user credentials live from the
stack outputs and exports them (passwords are set but not printed). It ends with
the launch command. Then, from that same shell:

```bash
kiro -n "$PWD"             # open this repo in a new window
```

If `kiro` isn't on your PATH, run Kiro's Command Palette once ->
"Shell Command: Install 'kiro' command in PATH". If `kiro -n` focuses the existing
window instead of opening a second one, use the Command Palette ->
**"Duplicate Workspace"** from inside Kiro (both windows share the same `.kiro/`).

### Windows (PowerShell)

Prerequisites: Windows 11 with Windows Terminal / PowerShell (Command Prompt is
not supported by the Kiro CLI), AWS CLI v2, and Python 3.10+. If script execution
is blocked, allow local scripts once:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Install the MCP server (note the Windows venv path uses `Scripts\python.exe`):

```powershell
cd C:\path\to\AST\code\kiro-mcp
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

Then, from the repo root, **dot-source** the setup script (the leading `. ` makes
the exported vars persist in your session):

```powershell
cd C:\path\to\AST
. .\scripts\kiro-testing-env.ps1
```

It performs the same job as the bash script: pulls config + the six persona
credentials from the stack outputs and sets them as `$env:` variables (passwords
set, not printed). Then launch Kiro from that same PowerShell session, e.g.:

```powershell
kiro "$PWD"
```

Windows notes (from Kiro's docs, with gaps called out):

- **Approve the env vars** under Settings -> "Mcp Approved Env Vars" as above —
  this is required on Windows too. The `${VAR}` syntax is the same on all
  platforms (there is no `%VAR%` form).
- Kiro's docs do **not** document a `kiro -n` new-window flag on Windows, nor do
  they explicitly confirm that an IDE-launched MCP server inherits environment
  variables from the launching terminal. If the persona agents fail to authenticate
  in the IDE, the reliable fallbacks are: (a) confirm the vars are approved in
  Settings, and (b) put the values directly in the MCP `env` block instead of
  `${VAR}` (test users only — never commit real secrets). For a second window on
  the same folder, use the Command Palette -> "Duplicate Workspace".
- The MCP command must resolve on PATH; if Python isn't found, use the absolute
  `.venv\Scripts\python.exe` path (which is what `AIDLC_MCP_PYTHON` is set to).
- Optional PowerShell shell integration for Kiro's terminal (add to your
  `$Profile`):

  ```powershell
  if ($env:TERM_PROGRAM -eq "kiro") { . "$(kiro --locate-shell-integration-path pwsh)" }
  ```

### In the test window

1. Switch to the Business Analyst agent (`cmd+alt+1`, or `ctrl+alt+1` on Windows if
   your keybindings follow the platform default).
2. Ask *"Show me the projects I'm working on."* A clean `list_projects` confirms
   auth/config/connectivity.
3. Create your project and proceed through the persona steps above.

If a persona fails, note the persona, what you asked, and the exact error/output —
that's enough to diagnose and fix. If calls start returning HTTP 401 mid-session,
refresh your AWS credentials and retry.

---

## Recommended: one-command workspace setup

The env-export + "Mcp Approved Env Vars" flow works, but it's fiddly. The reliable
way to provision any test folder is:

```bash
bash scripts/setup-test-workspace.sh /path/to/folder [ba|sa|dev|qa|devops|maint]
```

This copies the skills/steering/hooks in, and **materializes** the six persona
agents and a `.kiro/settings/mcp.json` with an **absolute** `command` path and
**inlined** config + test-user credentials (pulled live from the stack outputs).
Then just open the folder in Kiro (no env exports, no approval step needed),
reload the window, and either ask the default agent to "list projects" or pick a
persona agent. Re-run it after every redeploy (config values change) or to
add another test folder. The generated files contain machine-specific paths and
test credentials - keep them local, don't commit them.

## Troubleshooting the MCP connection

Two gotchas that will stop the `aidlc-platform` server from connecting:

- **`spawn ${AIDLC_MCP_PYTHON} ENOENT` in the MCP logs.** Kiro does **not** expand
  `${VAR}` in an MCP server's `command` field (only in the `env` block). The
  `command` must be a literal, absolute path to the venv python, e.g.
  `/abs/path/to/code/kiro-mcp/.venv/bin/python`. This is exactly what
  `setup-test-workspace.sh` writes for you. (The `${AIDLC_MCP_PYTHON}` form in the
  repo's template agent files is a placeholder - it only resolves once
  materialized to an absolute path.)

- **`ModuleNotFoundError: No module named 'aidlc_mcp'`.** The venv doesn't have the
  package installed. Install it editable so it imports regardless of `PYTHONPATH`:

  ```bash
  cd code/kiro-mcp && .venv/bin/python -m pip install -e .
  .venv/bin/python -c "import aidlc_mcp && print('ok')"   # verify
  ```

- **Server shows connected but tools error with 401/403.** Auth or ownership issue,
  not a connection issue. Refresh your AWS credentials for 401; a 403/422 means it
  isn't that persona's turn (server-side RBAC) - switch to the owning persona.

To see the real reason a server won't start, open the **"Kiro - MCP Logs"** output
channel and read the `aidlc-platform` lines.
