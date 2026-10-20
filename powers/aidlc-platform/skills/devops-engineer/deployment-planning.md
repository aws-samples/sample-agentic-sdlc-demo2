---
name: deployment-planning
description: "DevOps stage playbook for the deployment_planning stage: read the application design and QA report and produce an AWS deployment-plan.md (target environments, infrastructure, rollout/rollback). Registry generator: kiro; skill: deployment-planning; advances_state: in_deployment."
stage: deployment_planning
segment: devops_segment
persona: devops-engineer
generator: kiro
---
# Stage Skill — Deployment Planning

You are the **DevOps Engineer** generating the deployment plan for the
`deployment_planning` stage. **You (Kiro) generate this artifact** — there is no
server-side stage agent. Resolve all project data through the platform MCP
tools; never fabricate it.

## Role & goal
Read the application design and the QA sign-off and produce an **AWS deployment
plan**: target environments, infrastructure outline, and the rollout/rollback
strategy. This is the first DevOps stage — completing it advances the coarse
state to `in_deployment`.

**AWS-only constraint:** every environment, infrastructure, and pipeline choice
MUST target AWS services. Do not reference other cloud providers.

## Consumes (read first)
Registry `consumes`:
- `aidlc-docs/inception/application-design/application-design.md`
- `aidlc-docs/qa/qa-report.md`

Read both with `get_artifact`; the NFR design and units of work are useful
supporting context.

## Produces (write here)
Registry `produces`:
- `aidlc-docs/devops/deployment-plan.md` — **required**.

Submit it with `submit_artifact(project_uuid, "aidlc-docs/devops/deployment-plan.md", <content>)`,
then call `complete_stage`.

## Required output structure
The deliverable MUST satisfy the stage `acceptance` rules:
- `nonempty`
- `min_words:120`
- `has_sections:Target Environments,Rollback` — these headings MUST be present
  (verbatim), each with real content.

Recommended layout:

```markdown
# Deployment Plan — <Project Name>

## Target Environments
The AWS environments (dev/staging/prod), accounts/regions, and their purpose.

## Infrastructure
The AWS resources to provision (compute, data, networking, IAM) and how (CDK/
CloudFormation/pipeline). Reference the NFR design for scaling/availability.

## Rollout Strategy
The deployment approach (blue/green, canary, rolling) and the promotion path.

## Rollback
The rollback triggers and procedure for each environment.
```

## Quality guidance
- Ground the plan in the application design and QA findings; note any
  deployment risks the QA report raised.
- Prefer AWS-native, well-architected deployment patterns.
- Refine concrete resource sizing before the release runbook.

## Flow
1. `get_stage_brief` — confirm you're on `deployment_planning`.
2. `get_artifact` — read the application design and QA report.
3. Generate `deployment-plan.md` per the structure above.
4. `submit_artifact` to the produces path.
5. `complete_stage` — validates acceptance (lenient warnings; missing required
   blocks). Because this stage declares `advances_state: in_deployment`,
   completing it advances the coarse owned state to `in_deployment` and moves the
   cursor to `release`.

## Notes
- Ownership and legality are enforced server-side; a 403/422 means it isn't your
  turn or the move isn't allowed.
- Completing the stage constitutes your approval of the deliverable and is
  recorded in history.
