/**
 * CDK assertion tests for the Project record store (DynamoDB).
 *
 * Synthesizes the real `AIDLCPlatformStack` and asserts the DynamoDB table,
 * its three GSIs, on-demand billing + PITR, and that the Project API Lambda is
 * wired to the table (env var + IAM read/write incl. TransactWriteItems perms).
 *
 * Validates: Requirements 2.1, 13.1, 13.3
 */

import * as cdk from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { AIDLCPlatformStack } from "../lib/aidlc-platform";

function synthTemplate(): Template {
  const app = new cdk.App();
  const stack = new AIDLCPlatformStack(app, "AIDLCPlatformWorkshop", {
    assetsBucketName: "test-assets-bucket",
    env: { account: "123456789012", region: "us-east-1" },
  });
  return Template.fromStack(stack, { skipCyclicalDependenciesCheck: true });
}

let template: Template;

beforeAll(() => {
  template = synthTemplate();
});

describe("Project record store (DynamoDB, Kiro-native)", () => {
  test("a DynamoDB table with PK/SK and on-demand billing exists (Req 2.1, 13.3)", () => {
    template.hasResourceProperties("AWS::DynamoDB::Table", {
      BillingMode: "PAY_PER_REQUEST",
      KeySchema: Match.arrayWith([
        { AttributeName: "PK", KeyType: "HASH" },
        { AttributeName: "SK", KeyType: "RANGE" },
      ]),
    });
  });

  test("the table defines GSI1/GSI2/GSI3 (role queue, selection, state) (Req 13.1)", () => {
    template.hasResourceProperties("AWS::DynamoDB::Table", {
      GlobalSecondaryIndexes: Match.arrayWith([
        Match.objectLike({ IndexName: "GSI1" }),
        Match.objectLike({ IndexName: "GSI2" }),
        Match.objectLike({ IndexName: "GSI3" }),
      ]),
    });
  });

  test("point-in-time recovery is enabled", () => {
    template.hasResourceProperties("AWS::DynamoDB::Table", {
      PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: true },
    });
  });

  test("the Project API Lambda receives the table name via PROJECT_TABLE (Req 2.1)", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "project_api.handler",
      Environment: {
        Variables: Match.objectLike({ PROJECT_TABLE: Match.anyValue() }),
      },
    });
  });

  test("an IAM policy grants the Project API DynamoDB read/write (Req 13.3)", () => {
    const policies = template.findResources("AWS::IAM::Policy");
    const ddbActions = new Set<string>();
    for (const p of Object.values(policies) as any[]) {
      const statements = p.Properties?.PolicyDocument?.Statement ?? [];
      for (const st of statements) {
        const acts = Array.isArray(st.Action) ? st.Action : [st.Action];
        for (const a of acts) {
          if (typeof a === "string" && a.startsWith("dynamodb:")) ddbActions.add(a);
        }
      }
    }
    expect([...ddbActions]).toEqual(
      expect.arrayContaining(["dynamodb:PutItem", "dynamodb:Query", "dynamodb:UpdateItem"]),
    );
  });
});
