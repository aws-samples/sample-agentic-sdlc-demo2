---
inclusion: always
---

# Testing

Run the suite for the area you changed, from the repo root, before presenting
results. Add/extend tests when adding features or fixing bugs.

## CDK (assertions + account guard) — Jest

```bash
yarn workspace cdk test
```

## Project API + record store + validators + post-confirm + e2e — Python (pytest)

The Project API is the Model 1 system of record (DynamoDB-backed). Uses the
dedicated venv at `cdk/custom-resources/.venv-project-api`:

```bash
cdk/custom-resources/.venv-project-api/bin/python -m pytest cdk/custom-resources/tests/
```

The DynamoDB/S3 access is exercised against **in-memory fakes**
(`tests/fake_dynamodb.py`, `tests/fake_s3.py`) — **not moto**, no AWS calls, no
extra dependency. This suite covers:

- `test_project_api.py` — CRUD, conditional-write concurrency (HTTP 409), the
  broad selection + role-queue listings (GSI2/GSI1), RBAC (403 non-owner / 422
  illegal transition), the stage cursor, stage-brief, presigned
  `stage-artifact-url` path-guard, and `complete-stage` advance/handoff.
- `test_acceptance.py` — the lenient v1 acceptance validators.
- `test_workflow_registry_contracts.py` — `validate()` over the `stage_contracts`
  (generator/skill/produces/consumes/advances_state well-formedness).
- `test_segment_stage_walk_e2e.py` — per-segment stage walks with fixture-generated
  artifacts.
- `test_two_persona_handoff_e2e.py` / `test_six_persona_handoff_e2e.py` — the full
  BA → SA → Dev → QA → DevOps lifecycle (incl. a request-changes bounce).
- `test_git_webhook.py`, `test_post_confirmation_role_assign.py`.

## Kiro frontend MCP server — Python (pytest)

The Kiro IDE MCP server is the only frontend. Uses the venv at `code/kiro-mcp/.venv`:

```bash
code/kiro-mcp/.venv/bin/python -m pytest code/kiro-mcp/tests/
```

This suite covers the Model 1 tools (`get_stage_brief` / `submit_artifact` /
`complete_stage` in `test_tools.py`), the end-to-end multi-persona handoff
scenario (`test_e2e.py`: BA → SA [bounce + feedback] → Developer → QA → DevOps to
`deployed`), the enforcement-hook gating tests (`test_hooks.py`), and
agent/skill/Power/steering config validation (`test_packaging.py`, `test_config.py`).

## Notes

- Some tests use Hypothesis; treat the generated `.hypothesis/` output as scratch.
- The former **AgentCore Runtime** unit/property suite
  (`code/agentcore-runtime/icode-agent/tests/`) is gone — the runtime was deleted
  in the Model 1 migration.
- The **gateway lambda-tools** (`code/agentcore-gateway/lambda-tools/`) remain
  in-repo with their own tests but are **no longer part of the active deployed
  path** (the AgentCore Gateway that hosted them was removed). Don't rely on them
  for Model 1 coverage.
- If a venv is missing, the suite can't run in this environment — say so rather
  than skipping verification silently.
