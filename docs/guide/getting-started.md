# Getting Started

This page gets you from "I have access" to "I'm working a project in Kiro." It covers what you need, how to select a persona agent, and how to sanity-check the connection. If you're the person deploying the platform, the deploy steps live in [Deployment Steps](../deployment-steps.md) and MCP configuration in [code/kiro-mcp/docs/setup.md](../../code/kiro-mcp/docs/setup.md); this page assumes the platform is already deployed and configured.

Kiro IDE is the only frontend — there is no URL to open, no portal, and no hosted login page. You work by selecting a persona **agent** in Kiro and talking to it; the agent calls the `aidlc-platform` MCP server under the hood.

---

## What you need

- **Kiro IDE**, with this repository opened. The six persona agents (`.kiro/agents/`), the stage Skills (`.kiro/skills/`), and the enforcement hooks (`.kiro/hooks/`) are picked up automatically.
- **The MCP server configured.** The `aidlc-platform` MCP server (`code/kiro-mcp/`) must be installed and its `AIDLC_*` environment variables set from the stack outputs. See [code/kiro-mcp/docs/setup.md](../../code/kiro-mcp/docs/setup.md).
- **Persona credentials.** One Cognito username/password per persona (`USER_PASSWORD_AUTH`). The deploy provisions six test users; for the current demo environment and its logins, see [docs/deployed-environment.md](../deployed-environment.md).

---

## Selecting a persona

Each persona is a Kiro agent backed by its own Cognito identity. Selecting a persona agent *is* signing in as that persona — the agent carries that identity via its `USER_PASSWORD_AUTH` credentials, and the backend enforces RBAC from the verified group claim.

Switch persona with the agent picker or the keyboard shortcut:

| Persona | Shortcut | Role value |
| --- | --- | --- |
| Business Analyst | `cmd+alt+1` | `requirements-analyst` |
| Solution Architect | `cmd+alt+2` | `system-architect` |
| Software Developer | `cmd+alt+3` | `software-developer` |
| QA Engineer | `cmd+alt+4` | `qa-engineer` |
| DevOps Engineer | `cmd+alt+5` | `devops-engineer` |
| Maintenance Specialist | `cmd+alt+6` | `maintenance-specialist` |

Your persona determines what you can *do*. Every persona can *see* every project (visibility is broad by design), but the backend rejects out-of-turn actions — so you can only act on a project when it's your persona's turn.

---

## Sanity check

Select any persona and ask:

> *"Show me the projects I'm working on."*

The agent calls `list_projects`. A clean response confirms the MCP server is connected, your `AIDLC_*` config resolved, and your persona credentials authenticate. If it fails, see the troubleshooting section of [docs/USER_GUIDE.md](../USER_GUIDE.md) and [code/kiro-mcp/docs/setup.md](../../code/kiro-mcp/docs/setup.md).

---

## How you interact

You talk to a persona agent in natural language; it calls MCP tools. The recurring shape of the work:

- **See what's yours.** `list_projects` shows every project and its state; `get_project` shows one project's state, owner, and any `latest_change_request`.
- **Read before you act.** `list_artifacts` and `get_artifact` read the upstream deliverables. Reading them also satisfies the review-before-approve hook.
- **Generate a stage.** `get_stage_brief` returns the active stage contract (`produces` / `consumes` / `acceptance` and the guiding `skill`); you generate the deliverable in Kiro, upload it with `submit_artifact`, then `complete_stage`.
- **Move the project.** `complete_stage` advances the stage cursor (and, where declared, the owned state); `handoff_project` transfers ownership to the next persona; `return_for_changes` bounces it back with a note.

Two client-side enforcement hooks keep everyone honest: **review-before-approve** blocks `complete_stage`/`handoff_project` until you've read the artifacts this session, and **tests-before-submit** blocks the developer's `submit_implementation` until tests pass. Neither ever blocks `return_for_changes`.

---

## Working more than one persona yourself

To run a full BA → SA → Dev → QA → DevOps flow yourself, switch between persona agents with the agent picker (`cmd+alt+1..6`). A common pattern is to run the personas in one Kiro window and keep a second window open on the same repo for fixing code — see the "Testing in a separate Kiro window" section of [docs/USER_GUIDE.md](../USER_GUIDE.md).

---

## Where to go next

Pick the walkthrough for your role:

- [Business Analyst Walkthrough](walkthrough-business-analyst.md)
- [Solution Architect Walkthrough](walkthrough-solution-architect.md)
- [Software Developer Walkthrough](walkthrough-software-developer.md)
- [QA Engineer Walkthrough](walkthrough-qa-engineer.md)
- [DevOps Engineer Walkthrough](walkthrough-devops-engineer.md)
- [Maintenance Dashboard](walkthrough-maintenance-dashboard.md)

Or read [Handoffs and Change Requests](handoffs-and-change-requests.md) to understand how work passes between roles.
