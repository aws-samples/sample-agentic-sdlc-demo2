---
inclusion: fileMatch
fileMatchPattern: "cdk/bin/**/*.ts|cdk/lib/**/*.ts|cdk/test/**/*.ts"
---

# CDK / TypeScript Infrastructure Conventions

Applies to the CDK app under `cdk/` (AWS CDK v2, TypeScript). The single stack
is `AIDLCPlatformWorkshop` (see `10-stable-identifiers.md`).

## Structure

- Entry point: `cdk/bin/cdk.ts`.
- Root stack: `cdk/lib/aidlc-platform.ts`, composed of constructs:
  - `aidlc-platform-auth.ts` — Cognito user pool, the Kiro `USER_PASSWORD_AUTH`
    app client, persona groups, post-confirmation trigger, test users. (The
    client-credentials M2M client and the `agentcore` resource-server scope were
    removed with AgentCore.)
  - `aidlc-platform-agents.ts` — the Project API (Lambda + API Gateway), the
    `AidlcProjects` **DynamoDB** table (GSI1/2/3), the integration-config Lambda,
    the CodeBuild repo-ingest + test-runner projects, and the Git webhook. (No
    AgentCore Runtime/Gateway/Memory — those were removed in the Model 1
    migration.)
  - `aidlc-assetbucket.ts` — the S3 artifact/project bucket.

## Configuration

- All deployment config lives in `cdk/lib/config.ts` (`DeploymentConfig`).
  Prefer adding to config + env overrides (deep-merged via `getConfig(env)`)
  over inlining literals in constructs.
- Bucket names are derived as `${prefix}-${account}-${region}`. Don't hardcode
  account/region.

## Account guard (deploy safety)

- `cdk/bin/cdk.ts` calls `assertExpectedAccount(resolveAccount(...))` so a
  deploy to an unintended account halts before any resource is created. Do not
  weaken or bypass this guard.
- Precedence: `-c account=<id>` (explicit override, bypasses guard) >
  `CDK_DEFAULT_ACCOUNT` (gated) > pinned default. Region defaults to `us-east-1`
  and is not gated.

## Practices

- Inline Python Lambda code is bundled from `cdk/custom-resources/`; keep the
  bundled `workflow_registry.json` in sync (see `20-workflow-registry.md`).
- Add/maintain assertion tests in `cdk/test/` (Jest) when changing synth output,
  auth, or resource placement.
- Watch for circular dependencies between constructs (Auth ↔ Agents) — the code
  uses string-based ARNs in places specifically to avoid them; follow that
  pattern.
- Flag any new network-exposed resource that lacks authentication.
