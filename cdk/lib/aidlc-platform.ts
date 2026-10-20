import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";
import { getConfig } from "./config";
import { AssetsBucket } from "./aidlc-assetbucket";

import { AIDLCPlatformAuth } from "./aidlc-platform-auth";
import { AIDLCPlatformAgents } from "./aidlc-platform-agents";

export interface CdkStackProps extends cdk.StackProps {
  /**
   * Name of the S3 bucket that holds the packaged deployment assets.
   * Created and populated by `scripts/package-assets.sh` before deploy.
   */
  assetsBucketName: string;
  /**
   * Optional URL to an external IDE (VSCode, Cloud9, etc.). When set,
   * the UI will render an "Open IDE" button that links to this URL.
   * Pass via CDK context: `-c ideUrl=https://your-ide.example.com`.
   */
  ideUrl?: string;
}

export interface LambdaFunctionInfo {
  function: lambda.Function;
  toolNames: string[];
}

export class AIDLCPlatformStack extends cdk.Stack {
  constructor(scope: Construct, id: string, prop: CdkStackProps) {
    super(scope, id, prop);

    const config = getConfig();

    const assetsBucket = new AssetsBucket(
      this,
      "AssetsBucket",
      prop.assetsBucketName,
    );

    const auth = new AIDLCPlatformAuth(this, "AIDLCPlatformAuth", {
      config,
    });

    const agent = new AIDLCPlatformAgents(this, "AIDLCPlatformAgents", {
      config,
      auth,
      assetsBucket,
    });

    agent.node.addDependency(auth);

    // Grant Identity Pool authenticated users read access to project files bucket.
    // Use string-based ARN to avoid circular dependency between Auth and Agents.
    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;
    const projectBucketName = `${config.s3.bucketNameAgentPrefix}-${account}-${region}`;
    auth.identityPool.authenticatedRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["s3:GetObject", "s3:ListBucket"],
        resources: [
          `arn:aws:s3:::${projectBucketName}`,
          `arn:aws:s3:::${projectBucketName}/*`,
        ],
      }),
    );


    // -------------------------------------------------------
    // Kiro frontend (MCP server) configuration outputs.
    // These replace the role /config.json played for the (removed) React UI:
    // the Kiro MCP server reads these values from the environment. See
    // code/kiro-mcp/docs/setup.md for the mapping to AIDLC_* env vars.
    // -------------------------------------------------------
    new cdk.CfnOutput(this, "KiroCognitoUserPoolId", {
      value: auth.userPool.userPoolId,
      description: "AIDLC_COGNITO_USER_POOL_ID for the Kiro MCP server",
    });
    new cdk.CfnOutput(this, "KiroCognitoClientId", {
      value: auth.pkceClient.userPoolClientId,
      description: "AIDLC_COGNITO_CLIENT_ID (Kiro client with USER_PASSWORD_AUTH)",
    });
    new cdk.CfnOutput(this, "KiroFilesBucket", {
      value: agent.projectBucket.bucketName,
      description: "AIDLC_FILES_BUCKET for the Kiro MCP server",
    });
    new cdk.CfnOutput(this, "KiroProjectApiUrl", {
      value: agent.projectApiUrl,
      description: "AIDLC_PROJECT_API_URL for the Kiro MCP server",
    });
    new cdk.CfnOutput(this, "TestUserEmail", {
      value: auth.testUserEmail,
    });
    new cdk.CfnOutput(this, "TestUserPassword", {
      value: auth.testUserPassword,
    });
    new cdk.CfnOutput(this, "BATestUserEmail", {
      value: auth.baTestUserEmail,
    });
    new cdk.CfnOutput(this, "BATestUserPassword", {
      value: auth.baTestUserPassword,
    });
    new cdk.CfnOutput(this, "SATestUserEmail", {
      value: auth.saTestUserEmail,
    });
    new cdk.CfnOutput(this, "SATestUserPassword", {
      value: auth.saTestUserPassword,
    });
    new cdk.CfnOutput(this, "QATestUserEmail", {
      value: auth.qaTestUserEmail,
    });
    new cdk.CfnOutput(this, "QATestUserPassword", {
      value: auth.qaTestUserPassword,
    });
    new cdk.CfnOutput(this, "DevOpsTestUserEmail", {
      value: auth.devopsTestUserEmail,
    });
    new cdk.CfnOutput(this, "DevOpsTestUserPassword", {
      value: auth.devopsTestUserPassword,
    });
    new cdk.CfnOutput(this, "MaintTestUserEmail", {
      value: auth.maintTestUserEmail,
    });
    new cdk.CfnOutput(this, "MaintTestUserPassword", {
      value: auth.maintTestUserPassword,
    });
  }
}
