---
inclusion: manual
---

# Build & Deploy (manual — add with /context add when needed)

Deploys touch live AWS resources and are high-risk. Confirm intent before
running deploy/teardown. Region defaults to `us-east-1` unless overridden.

Model 1: the stack is **Cognito + Project API (Lambda + API Gateway) + DynamoDB +
S3 + CodeBuild**. There is **no AgentCore Runtime/Gateway/Memory** and **no agent
container image build** — nothing to bake, so deploys are fast.

## Prerequisites

- AWS CLI v2 configured; Node 20+ with Yarn; AWS CDK v2 CLI; Docker running
  (referenced by `cdk synth` for Lambda asset bundling).
- **Bedrock model access is no longer a stack prerequisite** — generation is done
  by **Kiro** (its own agent + model), not by a server-side runtime. Kiro itself
  needs model access, but the deployed stack runs no model.

## First-time setup

```bash
yarn install
yarn workspace cdk cdk bootstrap   # once per account/region
```

## Deploy

```bash
yarn deploy-infrastructure
```

This runs `scripts/package-assets.sh` (packages + uploads the Lambda zips to
`aidlc-assets-<account>-<region>`) then deploys the `AIDLCPlatformWorkshop` stack.
With the ARM64 agent container build removed, the first deploy is much faster than
the old ~15–20 min.

### Deploy to a non-default account (bypasses the account guard)

```bash
yarn workspace cdk cdk deploy -c account=<id> -c region=<id>
```

## Repackage code without a full deploy

```bash
bash scripts/package-code.sh      # packages the Lambda tool zips
```

`package-code.sh` no longer syncs a runtime-image registry copy — with AgentCore
removed, the Project API Lambda asset
(`cdk/custom-resources/workflow_registry.json`) is the sole canonical Python
registry copy. Keep the Kiro MCP server copy
(`code/kiro-mcp/aidlc_mcp/workflow_registry.json`) consistent with it (see
`20-workflow-registry.md`).

## Tear down

```bash
yarn delete-infrastructure
```

The assets bucket (`aidlc-assets-<account>-<region>`) is created outside the
stack, so `cdk destroy` will not remove it — delete it manually if no longer
needed.

## Stack outputs

The Project API URL, Cognito user pool / client IDs, the files bucket, and the
pre-provisioned per-persona test-user credentials are printed on deploy
completion. Configure the Kiro MCP server with these `Kiro*` outputs (see
`code/kiro-mcp/docs/setup.md`). There is no CloudFront/UI URL output (the web UI
was removed).
