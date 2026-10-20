import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";

import * as path from "path";
import * as apigw from "aws-cdk-lib/aws-apigateway";
import * as codebuild from "aws-cdk-lib/aws-codebuild";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda_ from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { DeploymentConfig } from "./config";
import { AIDLCPlatformAuth } from "./aidlc-platform-auth";
import { AssetsBucket } from "./aidlc-assetbucket";

export interface Params {
  auth: AIDLCPlatformAuth;
  assetsBucket: AssetsBucket;
  readonly config: DeploymentConfig;
}

export class AIDLCPlatformAgents extends Construct {
  public readonly projectBucket: s3.Bucket;
  public readonly projectTable: dynamodb.Table;
  public readonly projectApiUrl: string;

  constructor(scope: Construct, id: string, props: Params) {
    super(scope, id);

    const { config } = props;

    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

    const removalPolicy =
      config.s3.removalPolicy === "DESTROY"
        ? cdk.RemovalPolicy.DESTROY
        : cdk.RemovalPolicy.RETAIN;

    this.projectBucket = new s3.Bucket(this, "ProjectBucket", {
      bucketName: `${config.s3.bucketNameAgentPrefix}-${account}-${region}`,
      removalPolicy,
      autoDeleteObjects: config.s3.removalPolicy === "DESTROY",
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
    });
    // Preserve the original CloudFormation logical IDs from when this bucket
    // was defined at the stack root level. Moving it into this construct
    // changed the generated logical IDs, which causes CloudFormation to
    // try delete + recreate the existing bucket (fails if not empty) and
    // creates a new bucket policy resource that conflicts with the old one
    // (the old policy deletion wipes the new policy, leaving a Deny-all).
    (this.projectBucket.node.defaultChild as cdk.CfnResource).overrideLogicalId(
      "ProjectBucket01EBB8FB",
    );
    const bucketPolicy = this.projectBucket.policy;
    if (bucketPolicy) {
      (bucketPolicy.node.defaultChild as cdk.CfnResource).overrideLogicalId(
        "ProjectBucketPolicy4340EDEA",
      );
    }

    this.projectBucket.addCorsRule({
      allowedOrigins: ["*"],
      // GET: Document Viewer fetches artifacts via presigned GET URLs.
      // PUT: Business Analyst uploads requirements docs via presigned PUT URLs
      //      (RequirementsUploadControl). Without PUT the browser preflight
      //      blocks the upload and surfaces as "Failed to fetch".
      allowedMethods: [s3.HttpMethods.GET, s3.HttpMethods.PUT],
      allowedHeaders: ["*"],
      maxAge: 3600,
    });

    // -------------------------------------------------------
    // Project API Lambda + API Gateway
    // -------------------------------------------------------
    // Shared, server-side Project Store + Project API (design Sections 2b, 7a):
    // a Python Lambda behind an apigw.RestApi protected by a Cognito authorizer.
    // -------------------------------------------------------
    // Project record store (DynamoDB) — system of record.
    // Single-table design (PK/SK) with GSIs for the role queue, broad
    // "all projects" selection, and state dashboards. Conditional writes +
    // TransactWriteItems (from the Project API) provide lost-update protection
    // and atomic transition + history. Replaces the previous S3-JSON records.
    // -------------------------------------------------------
    this.projectTable = new dynamodb.Table(this, "ProjectTable", {
      tableName: `${config.projectName}-projects`,
      partitionKey: { name: "PK", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "SK", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    // GSI1 — role queue: "assigned to my role" (PK ROLE#{owning_role}), newest first.
    this.projectTable.addGlobalSecondaryIndex({
      indexName: "GSI1",
      partitionKey: { name: "GSI1PK", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "GSI1SK", type: dynamodb.AttributeType.STRING },
    });
    // GSI2 — broad "all projects" selection (PK ENTITY#PROJECT), matches current
    // selection behaviour without an unbounded scan.
    this.projectTable.addGlobalSecondaryIndex({
      indexName: "GSI2",
      partitionKey: { name: "GSI2PK", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "GSI2SK", type: dynamodb.AttributeType.STRING },
    });
    // GSI3 — state dashboards / SLA (PK STATE#{handoff_state}).
    this.projectTable.addGlobalSecondaryIndex({
      indexName: "GSI3",
      partitionKey: { name: "GSI3PK", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "GSI3SK", type: dynamodb.AttributeType.STRING },
    });

    const projectApiFn = new lambda_.Function(this, "ProjectApiLambda", {
      functionName: `${config.projectName}-project-api`,
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: "project_api.handler",
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      code: lambda_.Code.fromAsset(
        path.join(__dirname, "..", "custom-resources"),
        { bundling: undefined },
      ),
      environment: {
        PROJECT_BUCKET: this.projectBucket.bucketName,
        PROJECT_TABLE: this.projectTable.tableName,
      },
    });

    // DynamoDB: the Project API owns the record store — read/write including the
    // GSIs and TransactWriteItems (atomic transition + history append).
    this.projectTable.grantReadWriteData(projectApiFn);

    // IAM: record CRUD scoped to the platform projects prefix, ListBucket on the
    // bucket, and a separate PutObject for presigned-URL signing of uploaded
    // requirements documents (design Section 2b). The handler additionally
    // restricts keys in code; these statements keep the role least-privilege.
    projectApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        // Record CRUD for `_platform/projects/{uuid}.json`.
        actions: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
        resources: [`${this.projectBucket.bucketArn}/_platform/projects/*`],
      }),
    );

    projectApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        // Artifact tree access under each project's `aidlc-docs/` prefix:
        //   * `s3:PutObject` — presigned-PUT signing for requirements uploads
        //     (`upload-url`) and Kiro-generated stage deliverables
        //     (`stage-artifact-url`);
        //   * `s3:GetObject` — `complete-stage` HeadObjects the required
        //     `produces` (missing/empty hard block) and GetObjects their
        //     content to run acceptance validation + record the audit manifest
        //     (Req 8.1/8.2/9.3). HeadObject is authorized by `s3:GetObject`.
        // Scoped to `{*}/aidlc-docs/*` (the stored, stable artifact prefix —
        // steering 10-stable-identifiers); the handler additionally narrows
        // each presigned URL to the exact key an active stage `produces`, so a
        // client can never write outside its stage contract and never holds
        // broad S3 write creds (design Section 3.10).
        actions: ["s3:PutObject", "s3:GetObject"],
        resources: [`${this.projectBucket.bucketArn}/*/aidlc-docs/*`],
      }),
    );

    projectApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        // ListBucket is bucket-scoped (the listing is constrained to the
        // platform prefix in handler code).
        actions: ["s3:ListBucket"],
        resources: [this.projectBucket.bucketArn],
      }),
    );

    projectApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        // DeleteObject for artifact-tree cleanup when a project is deleted.
        // The handler restricts deletion to the project's own
        // `{artifact_prefix}/` (with a trailing-slash boundary), so a future
        // same-named project starts with a clean prefix. Object-scoped on the
        // project bucket; the handler is the authority on which keys are
        // removed (mirrors the upload PutObject statement above).
        actions: ["s3:DeleteObject"],
        resources: [
          `${this.projectBucket.bucketArn}/*/aidlc-docs/*`,
          `${this.projectBucket.bucketArn}/*/source/*`,
        ],
      }),
    );

    projectApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        // Store an optional private-repo Git token for the C7 round-trip. The
        // link-repo handler writes it (put-or-create) to
        // `aidlc/projects/{uuid}/git`, which the ingest CodeBuild reads. Scoped
        // to that per-project secret path only (the `-*` suffix matches the
        // random suffix Secrets Manager appends to ARNs).
        actions: ["secretsmanager:CreateSecret", "secretsmanager:PutSecretValue"],
        resources: [
          `arn:aws:secretsmanager:${region}:${account}:secret:aidlc/projects/*/git-*`,
        ],
      }),
    );

    const projectApi = new apigw.RestApi(this, "ProjectApi", {
      restApiName: `${config.projectName}-project-api`,
      defaultCorsPreflightOptions: {
        allowOrigins: apigw.Cors.ALL_ORIGINS,
        allowMethods: apigw.Cors.ALL_METHODS,
      },
    });

    const projectApiAuthorizer = new apigw.CognitoUserPoolsAuthorizer(
      this,
      "ProjectApiAuthorizer",
      { cognitoUserPools: [props.auth.userPool] },
    );

    const projectApiIntegration = new apigw.LambdaIntegration(projectApiFn);
    const projectApiAuthOptions: apigw.MethodOptions = {
      authorizer: projectApiAuthorizer,
      authorizationType: apigw.AuthorizationType.COGNITO,
    };

    // Collection: /projects (POST create, GET list)
    const projectApiProjects = projectApi.root.addResource("projects");
    projectApiProjects.addMethod(
      "POST",
      projectApiIntegration,
      projectApiAuthOptions,
    );
    projectApiProjects.addMethod(
      "GET",
      projectApiIntegration,
      projectApiAuthOptions,
    );

    // Item: /projects/{projectId} (GET read, DELETE)
    const projectApiProjectId = projectApiProjects.addResource("{projectId}");
    projectApiProjectId.addMethod(
      "GET",
      projectApiIntegration,
      projectApiAuthOptions,
    );
    projectApiProjectId.addMethod(
      "DELETE",
      projectApiIntegration,
      projectApiAuthOptions,
    );

    // Item sub-resource: stage-brief (GET) — the active stage contract derived
    // from the workflow registry + the project's stage cursor (Req
    // 6.4). Owner-read: the handler gates on the current owning_role (403 for
    // non-owners) before returning the brief.
    const projectApiStageBrief =
      projectApiProjectId.addResource("stage-brief");
    projectApiStageBrief.addMethod(
      "GET",
      projectApiIntegration,
      projectApiAuthOptions,
    );

    // Item sub-resources: transition, reassign, upload-url (all POST)
    const projectApiTransition = projectApiProjectId.addResource("transition");
    projectApiTransition.addMethod(
      "POST",
      projectApiIntegration,
      projectApiAuthOptions,
    );

    const projectApiReassign = projectApiProjectId.addResource("reassign");
    projectApiReassign.addMethod(
      "POST",
      projectApiIntegration,
      projectApiAuthOptions,
    );

    const projectApiUploadUrl = projectApiProjectId.addResource("upload-url");
    projectApiUploadUrl.addMethod(
      "POST",
      projectApiIntegration,
      projectApiAuthOptions,
    );

    // Item sub-resource: stage-artifact-url (POST) — presigned PUT for a
    // Kiro-generated stage deliverable (Req 7.2/9.1). Owner-gated: the
    // handler validates the requested path against the active stage's declared
    // `produces` (400 off-contract) and mints a short-TTL SigV4 PUT scoped to
    // the exact `{slug}/aidlc-docs/...` key before returning it.
    const projectApiStageArtifactUrl =
      projectApiProjectId.addResource("stage-artifact-url");
    projectApiStageArtifactUrl.addMethod(
      "POST",
      projectApiIntegration,
      projectApiAuthOptions,
    );

    // Item sub-resource: complete-stage (POST) — validate then advance
    // (Req 6.3/7.3/7.4/8.1/8.2/14.1). Owner-gated: the handler HeadObjects the
    // required `produces` (missing/empty → status:"blocked", no advance), runs
    // acceptance (lenient warnings), advances the stage cursor (and, where the
    // stage declares `advances_state`, the coarse handoff_state via the
    // registry — 422 if illegal), and appends a HIST item atomically.
    const projectApiCompleteStage =
      projectApiProjectId.addResource("complete-stage");
    projectApiCompleteStage.addMethod(
      "POST",
      projectApiIntegration,
      projectApiAuthOptions,
    );

    // Item sub-resource: link-repo (POST) — Git round-trip (C7). The developer
    // links the pushed GitHub/GitLab repo; the handler is owner-gated and
    // validates the URL against an allowlist.
    const projectApiLinkRepo = projectApiProjectId.addResource("link-repo");
    projectApiLinkRepo.addMethod(
      "POST",
      projectApiIntegration,
      projectApiAuthOptions,
    );

    this.projectApiUrl = projectApi.url;

    // -------------------------------------------------------
    // Git repository ingestion (C7 round-trip)
    // -------------------------------------------------------
    // A CodeBuild project clones the developer's linked repo and syncs it into
    // the project's `{slug}/source/` prefix, then invokes the Project API
    // Lambda's ingest-callback (direct Lambda invoke — no new network route) to
    // advance the project from implementation_submitted to assigned_to_qa.
    //
    // The Project API Lambda references the ingest project only by its literal
    // name + ARN (not a CDK object reference) to avoid a Lambda↔CodeBuild
    // circular dependency: the Lambda is the RestApi integration target, and the
    // build depends on the Lambda (invoke + function name). This mirrors the
    // string-ARN pattern used elsewhere in the stack to break cycles.
    const ingestProjectName = `${config.projectName}-repo-ingest`;

    const repoIngestBuild = new codebuild.Project(this, "RepoIngestBuild", {
      projectName: ingestProjectName,
      description:
        "Clones a linked Git repo and syncs it into the project source prefix (C7 ingestion)",
      // Fail fast rather than hang on a pathological repo.
      timeout: cdk.Duration.minutes(30),
      environment: {
        buildImage: codebuild.LinuxBuildImage.STANDARD_7_0,
        // MEDIUM gives more vCPU/RAM/disk than SMALL for larger working trees.
        computeType: codebuild.ComputeType.MEDIUM,
      },
      environmentVariables: {
        PROJECT_BUCKET: { value: this.projectBucket.bucketName },
        PROJECT_API_FUNCTION: { value: projectApiFn.functionName },
        // Ingest guardrail: a working tree over ~2 GB or 50k files fails fast
        // (reported as status=failed with a reason) instead of timing out on a
        // huge/monorepo checkout. Tune per environment.
        MAX_INGEST_BYTES: { value: "2147483648" },
        MAX_INGEST_FILES: { value: "50000" },
        // Per-build values (PROJECT_UUID / REPO_URL / REPO_BRANCH /
        // PROJECT_SLUG) are supplied as environmentVariablesOverride by
        // StartBuild in the Project API's link-repo handler.
      },
      buildSpec: codebuild.BuildSpec.fromObject({
        version: "0.2",
        phases: {
          build: {
            commands: [
              "set -e",
              "echo Ingesting repo for project $PROJECT_UUID",
              // Optional per-project git token for private repos (may be absent).
              "TOKEN=$(aws secretsmanager get-secret-value --secret-id aidlc/projects/$PROJECT_UUID/git --query SecretString --output text 2>/dev/null || true)",
              "CLONE_URL=$REPO_URL",
              'if [ -n "$TOKEN" ]; then CLONE_URL=$(echo "$REPO_URL" | sed -E "s#https://#https://$TOKEN@#"); fi',
              'git clone --depth 1 --branch "$REPO_BRANCH" "$CLONE_URL" repo',
              // Size guardrail: fail fast with a reason instead of timing out.
              "BYTES=$(du -sb repo 2>/dev/null | cut -f1 || echo 0)",
              "FILES=$(find repo -type f -not -path '*/.git/*' | wc -l | tr -d ' ')",
              'echo "Ingest working tree: $BYTES bytes, $FILES files (limits: $MAX_INGEST_BYTES bytes / $MAX_INGEST_FILES files)"',
              'if [ "$BYTES" -gt "$MAX_INGEST_BYTES" ] || [ "$FILES" -gt "$MAX_INGEST_FILES" ]; then echo "Repository exceeds the ingest size limit ($FILES files, $BYTES bytes)" > /tmp/ingest_reason.txt; exit 1; fi',
              "git -C repo rev-parse HEAD > /tmp/commit.txt",
              // Exclude VCS, dependency, build and vendored trees so only source
              // is synced (far fewer PUTs, smaller footprint).
              "aws s3 sync repo \"s3://$PROJECT_BUCKET/$PROJECT_SLUG/source/\" --delete --exclude '*.git/*' --exclude '*node_modules/*' --exclude '*/.venv/*' --exclude '*/dist/*' --exclude '*/build/*' --exclude '*/target/*' --exclude '*/vendor/*' --exclude '*__pycache__/*'",
            ],
          },
          post_build: {
            commands: [
              // Runs whether or not the build phase succeeded; report the
              // outcome (and any guardrail reason) back to the Project API so
              // it can advance to QA (ready) or record a failure.
              'if [ "$CODEBUILD_BUILD_SUCCEEDING" = "1" ]; then export STATUS=ready; else export STATUS=failed; fi',
              "export COMMIT=$(cat /tmp/commit.txt 2>/dev/null || echo '')",
              "export REASON=$(cat /tmp/ingest_reason.txt 2>/dev/null || echo '')",
              "export PAYLOAD=$(python3 -c \"import json,os;print(json.dumps({'httpMethod':'POST','resource':'/projects/{projectId}/ingest-callback','pathParameters':{'projectId':os.environ['PROJECT_UUID']},'body':json.dumps({'status':os.environ['STATUS'],'commit':os.environ.get('COMMIT',''),'reason':os.environ.get('REASON','')})}))\")",
              'aws lambda invoke --function-name "$PROJECT_API_FUNCTION" --cli-binary-format raw-in-base64-out --payload "$PAYLOAD" /tmp/callback-out.json',
              "cat /tmp/callback-out.json || true",
            ],
          },
        },
      }),
    });

    // CodeBuild role: sync into the project source prefix, read the optional
    // per-project git token, and invoke the Project API for the callback.
    repoIngestBuild.role?.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["s3:PutObject", "s3:DeleteObject"],
        resources: [`${this.projectBucket.bucketArn}/*/source/*`],
      }),
    );
    repoIngestBuild.role?.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["s3:ListBucket"],
        resources: [this.projectBucket.bucketArn],
      }),
    );
    repoIngestBuild.role?.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["secretsmanager:GetSecretValue"],
        resources: [
          `arn:aws:secretsmanager:${region}:${account}:secret:aidlc/projects/*/git-*`,
        ],
      }),
    );
    projectApiFn.grantInvoke(repoIngestBuild);

    // Let the Project API Lambda start the ingest build. Reference by literal
    // name + ARN (no CDK object reference) to avoid the circular dependency
    // described above.
    projectApiFn.addEnvironment("REPO_INGEST_CODEBUILD_PROJECT", ingestProjectName);
    projectApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["codebuild:StartBuild"],
        resources: [
          `arn:aws:codebuild:${region}:${account}:project/${ingestProjectName}`,
        ],
      }),
    );

    // -------------------------------------------------------
    // Git push webhook (C7 auto-ingest) — PUBLIC endpoint, authenticated by a
    // shared-secret signature (GitHub HMAC / GitLab token), NOT Cognito. On a
    // verified push it maps repo->project and re-triggers the ingest build.
    // -------------------------------------------------------
    const gitWebhookSecret = new secretsmanager.Secret(this, "GitWebhookSecret", {
      secretName: "aidlc/git-webhook/secret",
      description:
        "Shared secret for verifying GitHub/GitLab push webhooks (configure it on the repo's webhook).",
      generateSecretString: {
        excludePunctuation: true,
        passwordLength: 40,
      },
    });

    const gitWebhookFn = new lambda_.Function(this, "GitWebhookLambda", {
      functionName: `${config.projectName}-git-webhook`,
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: "git_webhook.handler",
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      code: lambda_.Code.fromAsset(path.join(__dirname, "..", "custom-resources"), {
        bundling: undefined,
      }),
      environment: {
        PROJECT_BUCKET: this.projectBucket.bucketName,
        GIT_WEBHOOK_SECRET_ARN: gitWebhookSecret.secretArn,
        // Literal name (no ref) — the webhook reuses project_api._maybe_trigger_ingest.
        REPO_INGEST_CODEBUILD_PROJECT: ingestProjectName,
      },
    });
    gitWebhookSecret.grantRead(gitWebhookFn);
    // List/read project records to match the pushed repo to a project.
    gitWebhookFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["s3:GetObject"],
        resources: [`${this.projectBucket.bucketArn}/_platform/projects/*`],
      }),
    );
    gitWebhookFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["s3:ListBucket"],
        resources: [this.projectBucket.bucketArn],
      }),
    );
    gitWebhookFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["codebuild:StartBuild"],
        resources: [
          `arn:aws:codebuild:${region}:${account}:project/${ingestProjectName}`,
        ],
      }),
    );

    // Public POST /git-webhook on a DEDICATED RestApi (keeps the Project API
    // fully Cognito-gated — this endpoint is authenticated by the HMAC/token
    // signature the handler verifies before any access, not by Cognito).
    const gitWebhookApi = new apigw.RestApi(this, "GitWebhookApi", {
      restApiName: `${config.projectName}-git-webhook`,
      description:
        "Public Git push webhook (signature-verified) for C7 auto-ingest.",
    });
    gitWebhookApi.root
      .addResource("git-webhook")
      .addMethod("POST", new apigw.LambdaIntegration(gitWebhookFn), {
        authorizationType: apigw.AuthorizationType.NONE,
      });

    // -------------------------------------------------------
    // QA test-runner (C7 real test execution)
    // -------------------------------------------------------
    // Pulls the ingested source from S3, runs the auto-detected test suite,
    // writes aidlc-docs/qa/test-results.md, and calls back with pass/fail via
    // direct Lambda invoke. Same literal-name pattern to avoid a cycle.
    const testProjectName = `${config.projectName}-repo-test`;
    const repoTestBuild = new codebuild.Project(this, "RepoTestBuild", {
      projectName: testProjectName,
      description:
        "Runs the ingested repo's test suite and reports pass/fail to the Project API (C7 QA)",
      timeout: cdk.Duration.minutes(30),
      environment: {
        buildImage: codebuild.LinuxBuildImage.STANDARD_7_0,
        computeType: codebuild.ComputeType.MEDIUM,
      },
      environmentVariables: {
        PROJECT_BUCKET: { value: this.projectBucket.bucketName },
        PROJECT_API_FUNCTION: { value: projectApiFn.functionName },
      },
      buildSpec: codebuild.BuildSpec.fromObject({
        version: "0.2",
        phases: {
          install: {
            // Match the runtime most modern Node repos declare (engines.node
            // >= 20). CodeBuild STANDARD_7_0 defaults to Node 18, where the
            // global Web Crypto (`crypto.randomUUID()`) is not exposed, so
            // handlers relying on it throw at runtime and tests spuriously
            // fail. Node 20 provides global `crypto` and satisfies `>=20`.
            "runtime-versions": { nodejs: 20 },
          },
          build: {
            commands: [
              "echo Running QA tests for $PROJECT_UUID",
              "aws s3 sync \"s3://$PROJECT_BUCKET/$PROJECT_SLUG/source/\" src || true",
              "cd src",
              "TEST_RC=0",
              // Best-effort auto-detect: npm test, else pytest, else no-op.
              "if [ -f package.json ]; then npm ci > /tmp/test.log 2>&1 && npm test >> /tmp/test.log 2>&1; TEST_RC=$?; elif [ -f requirements.txt ] || [ -f pyproject.toml ]; then python3 -m pip install -q pytest > /tmp/test.log 2>&1 || true; [ -f requirements.txt ] && python3 -m pip install -q -r requirements.txt >> /tmp/test.log 2>&1 || true; python3 -m pytest >> /tmp/test.log 2>&1; TEST_RC=$?; else echo 'No recognized test suite found.' > /tmp/test.log; TEST_RC=0; fi",
              "echo $TEST_RC > /tmp/test_rc",
              "cd ..",
            ],
          },
          post_build: {
            commands: [
              "TEST_RC=$(cat /tmp/test_rc 2>/dev/null || echo 1)",
              'if [ "$TEST_RC" = "0" ]; then export STATUS=passed; else export STATUS=failed; fi',
              "python3 -c \"import os;rc=(open('/tmp/test_rc').read().strip() if os.path.exists('/tmp/test_rc') else '1');log=(open('/tmp/test.log').read() if os.path.exists('/tmp/test.log') else '');open('/tmp/test-results.md','w').write('# Test Results\\n\\nExit code: '+rc+'\\n\\n'+chr(96)*3+'\\n'+log[-4000:]+'\\n'+chr(96)*3+'\\n')\"",
              "aws s3 cp /tmp/test-results.md \"s3://$PROJECT_BUCKET/$PROJECT_SLUG/aidlc-docs/qa/test-results.md\" || true",
              "export SUMMARY=$(head -c 400 /tmp/test.log 2>/dev/null | tr '\\n' ' ' || echo '')",
              "export PAYLOAD=$(python3 -c \"import json,os;print(json.dumps({'httpMethod':'POST','resource':'/projects/{projectId}/test-callback','pathParameters':{'projectId':os.environ['PROJECT_UUID']},'body':json.dumps({'status':os.environ['STATUS'],'summary':os.environ.get('SUMMARY','')})}))\")",
              'aws lambda invoke --function-name "$PROJECT_API_FUNCTION" --cli-binary-format raw-in-base64-out --payload "$PAYLOAD" /tmp/test-callback-out.json',
              "cat /tmp/test-callback-out.json || true",
            ],
          },
        },
      }),
    });
    repoTestBuild.role?.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["s3:GetObject"],
        resources: [`${this.projectBucket.bucketArn}/*/source/*`],
      }),
    );
    repoTestBuild.role?.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["s3:PutObject"],
        resources: [`${this.projectBucket.bucketArn}/*/aidlc-docs/qa/*`],
      }),
    );
    repoTestBuild.role?.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["s3:ListBucket"],
        resources: [this.projectBucket.bucketArn],
      }),
    );
    projectApiFn.grantInvoke(repoTestBuild);

    // Project API can start the test build (literal name/ARN to avoid a cycle).
    projectApiFn.addEnvironment("REPO_TEST_CODEBUILD_PROJECT", testProjectName);
    projectApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["codebuild:StartBuild"],
        resources: [
          `arn:aws:codebuild:${region}:${account}:project/${testProjectName}`,
        ],
      }),
    );

    // QA-owner-gated route to start the test-runner.
    const projectApiRunTests = projectApiProjectId.addResource("run-tests");
    projectApiRunTests.addMethod("POST", projectApiIntegration, projectApiAuthOptions);
  }
}
