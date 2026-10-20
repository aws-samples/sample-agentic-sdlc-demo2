# CDK app — Agentic SDLC Transformation

This CDK v2 (TypeScript) app defines the single `AIDLCPlatformWorkshop` stack: the
serverless backend — **Amazon Cognito** + the **Project API** (AWS
Lambda + Amazon API Gateway) + **Amazon DynamoDB** (`AidlcProjects`) + **Amazon
S3** + **AWS CodeBuild** (repo-ingest + test-runner) + a signature-verified Git
webhook. There is no AgentCore (no Runtime, Gateway, or Memory) and no web UI —
Kiro IDE is the only frontend, and Kiro generates every deliverable.

`cdk.json` tells the CDK Toolkit how to run the app (`bin/cdk.ts`). Deploys go
through the account-mismatch guard in `bin/cdk.ts` / `lib/utils/account-guard.ts`
— see the root [`README.md`](../README.md) and
[`docs/deployment-steps.md`](../docs/deployment-steps.md).

## Deploy / tear down

Prefer the root scripts, which stage assets and inject the assets bucket:

- `yarn deploy-infrastructure` — package assets + `cdk deploy`
- `yarn delete-infrastructure` — `cdk destroy`

Set `AIDLC_DEPLOY_ACCOUNT=<your-account-id>` (or `cdk/.env`) first so the account
guard passes. Docker is **not** required — Lambdas bundle from
`custom-resources/` via `Code.fromAsset`.

## Useful commands

- `yarn workspace cdk build` — compile TypeScript to JS
- `yarn workspace cdk test` — run the Jest assertion + account-guard tests
- `yarn workspace cdk cdk synth` — emit the synthesized CloudFormation template
- `yarn workspace cdk cdk diff` — compare the deployed stack with the current state
- `yarn workspace cdk cdk deploy` — deploy directly (needs `-c assetsBucketName=<bucket>`; prefer `yarn deploy-infrastructure`)
