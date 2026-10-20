#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { AIDLCPlatformStack } from "../lib/aidlc-platform";
import {
  DEFAULT_REGION,
  assertExpectedAccount,
  resolveAccount,
} from "../lib/utils/account-guard";

const app = new cdk.App();

const ideUrl = app.node.tryGetContext("ideUrl") || undefined;
const assetsBucketName = app.node.tryGetContext("assetsBucketName");

if (!assetsBucketName) {
  throw new Error(
    "Missing CDK context 'assetsBucketName'. Run via 'yarn deploy-infrastructure' " +
      "or pass '-c assetsBucketName=<bucket>' after running scripts/package-assets.sh.",
  );
}

// Resolve the deployment account using the standard CDK precedence
// (-c account > CDK_DEFAULT_ACCOUNT > pinned default) and assert it matches
// the pinned account when no explicit override was supplied. The guard is
// only bypassed by an explicit account override, preserving the ability to
// deploy elsewhere intentionally (Req 13.1, 13.2, 13.3, 13.4).
//
// The pinned default in account-guard.ts is this codebase's account
// (123456789012). You can still override it per-run (e.g. a one-off deploy
// elsewhere) without editing tracked source via either:
//   * '-c account=<id>' on the CLI, or
//   * the AIDLC_DEPLOY_ACCOUNT env var (e.g. from a git-ignored 'cdk/.env';
//     see cdk/.env.example).
// Both are treated as an explicit override, so the guard yields to them.
const accountResolution = resolveAccount({
  contextAccount:
    app.node.tryGetContext("account") || process.env.AIDLC_DEPLOY_ACCOUNT,
  envAccount: process.env.CDK_DEFAULT_ACCOUNT,
});
assertExpectedAccount(accountResolution);

const region =
  app.node.tryGetContext("region") ||
  process.env.CDK_DEFAULT_REGION ||
  DEFAULT_REGION;

const stack = new AIDLCPlatformStack(app, "AIDLCPlatformWorkshop", {
  ideUrl: ideUrl,
  assetsBucketName: assetsBucketName,
  env: {
    account: accountResolution.account,
    region: region,
  },
});
