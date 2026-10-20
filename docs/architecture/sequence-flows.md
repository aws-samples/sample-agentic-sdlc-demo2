# Sequence flows

Interaction traces for the platform. Participants are accurate to the
deployed system: the Kiro persona agent, the `aidlc-platform` MCP server, the
Project API Lambda, DynamoDB, S3, CodeBuild, and Cognito. See
[`../ARCHITECTURE.md`](../ARCHITECTURE.md) for the component overview and
[`data-model.md`](data-model.md) for the item shapes.

---

## (a) Create a project and generate a stage (BA)

The Kiro-native generation flow: `get_stage_brief` → read inputs →
`submit_artifact` (presigned PUT) → `complete_stage` (validate → advance).

```mermaid
sequenceDiagram
    autonumber
    participant BA as Kiro (BA agent)
    participant MCP as aidlc-platform MCP
    participant API as Project API Lambda
    participant DDB as DynamoDB
    participant S3 as S3

    BA->>MCP: create_project("Acme")
    MCP->>API: POST /projects (Bearer access token)
    API->>DDB: PutItem META (attribute_not_exists(PK), version=1)
    DDB-->>API: ok
    API-->>MCP: 201 {project_uuid, handoff_state:draft, current_stage}
    MCP-->>BA: created

    BA->>MCP: get_stage_brief(uuid)
    MCP->>API: GET /projects/{id}/stage-brief
    API->>DDB: GetItem META (owner-gate on cognito:groups)
    API-->>MCP: 200 {stage, skill, consumes[], produces[{path,required,acceptance}]}

    BA->>MCP: get_artifact(uuid, consumesPath)
    MCP->>S3: GetObject {slug}/aidlc-docs/...
    S3-->>MCP: content
    Note over BA: Kiro generates the deliverable using the stage Skill

    BA->>MCP: submit_artifact(uuid, path, content)
    MCP->>API: POST /projects/{id}/stage-artifact-url {path}
    API->>API: owner-gate + validate path ∈ produces (else 400/403)
    API-->>MCP: 200 {upload_url (SigV4, ~300s), key}
    MCP->>S3: PUT upload_url (content)
    S3-->>MCP: 200

    BA->>MCP: complete_stage(uuid)
    MCP->>API: POST /projects/{id}/complete-stage
    API->>S3: HeadObject each required produces
    S3-->>API: exists/size
    alt required artifact missing/empty
        API-->>MCP: 200 {status:"blocked", missing[]}
    else present
        API->>S3: GetObject content
        API->>API: acceptance.evaluate (lenient → warnings)
        API->>DDB: TransactWriteItems (META version+owner guard, +HIST stage-complete)
        DDB-->>API: ok
        API-->>MCP: 200 {status:"advanced", current_stage, handoff_state, warnings[]}
    end
```

---

## (b) Cross-persona handoff and reviewer bounce

`handoff_project` transfers ownership to the next persona; `return_for_changes` is
the reviewer bounce. Both are thin wrappers over the authoritative `transition`
route (the MCP layer never re-decides legality).

```mermaid
sequenceDiagram
    autonumber
    participant P as Kiro (owning persona)
    participant MCP as aidlc-platform MCP
    participant API as Project API Lambda
    participant DDB as DynamoDB

    Note over P,API: Handoff (forward)
    P->>MCP: handoff_project(uuid)
    MCP->>API: POST /projects/{id}/transition {action:"advance"}
    API->>DDB: GetItem META
    API->>API: owner-gate; validate_transition(current, forward)
    alt permitted
        API->>DDB: TransactWriteItems (META→new state/owner, +HIST transition)
        API-->>MCP: 200 {handoff_state, owning_role}
    else illegal
        API-->>MCP: 422 ILLEGAL_TRANSITION (unchanged)
    end
    MCP-->>P: handed off to next persona

    Note over P,API: Reviewer bounce (never blocked by hooks)
    P->>MCP: return_for_changes(uuid, comments)
    MCP->>API: POST /projects/{id}/transition {action:"request-changes", note}
    API->>API: request_changes(current).target; owner-gate
    API->>DDB: TransactWriteItems (META→bounce target, +HIST transition)
    API-->>MCP: 200 {handoff_state (bounced back)}
    MCP-->>P: returned for changes
```

On a stale write (concurrent modification / ownership change) the transaction is
cancelled and the API returns **409 `ETAG_MISMATCH`**.

---

## (c) Git round-trip (link → submit → ingest → advance to QA)

```mermaid
sequenceDiagram
    autonumber
    participant Dev as Kiro (Developer)
    participant MCP as aidlc-platform MCP
    participant API as Project API Lambda
    participant SM as Secrets Manager
    participant CB as CodeBuild (repo-ingest)
    participant Git as GitHub/GitLab
    participant S3 as S3
    participant DDB as DynamoDB

    Dev->>MCP: link_repo(uuid, repo_url, token?)
    MCP->>API: POST /projects/{id}/link-repo
    API->>API: owner-gate + SSRF allowlist check
    opt private-repo token
        API->>SM: PutSecretValue aidlc/projects/{uuid}/git
    end
    API->>DDB: PutItem META (repo_* set, version guard)
    API-->>MCP: 200 {repo_url, repo_ingest_status:"pending"}

    Dev->>MCP: submit_implementation(uuid)
    MCP->>API: POST /projects/{id}/transition {action:"submit"}
    API->>API: gate: repo linked? (else 422 REPO_NOT_LINKED)
    API->>DDB: TransactWriteItems (→ implementation_submitted, +HIST)
    API->>CB: StartBuild (PROJECT_UUID, REPO_URL, REPO_BRANCH, PROJECT_SLUG)
    API-->>MCP: 200 {handoff_state:"implementation_submitted"}

    CB->>SM: GetSecretValue git token (optional)
    CB->>Git: git clone --depth 1
    CB->>S3: s3 sync → {slug}/source/ (--delete, excludes)
    CB->>API: aws lambda invoke → ingest-callback {status:"ready", commit}
    API->>API: advance implementation_submitted → assigned_to_qa (registry)
    API->>DDB: TransactWriteItems (→ assigned_to_qa, +HIST transition)
    API-->>CB: 200 {advanced:true}
```

A public, signature-verified push webhook (`agentcore-strands-git-webhook`) can
re-trigger the same ingest build after verifying the GitHub HMAC / GitLab token
against `aidlc/git-webhook/secret`.

---

## (d) QA test-runner (run_tests → CodeBuild → test-callback)

```mermaid
sequenceDiagram
    autonumber
    participant QA as Kiro (QA Engineer)
    participant MCP as aidlc-platform MCP
    participant API as Project API Lambda
    participant CB as CodeBuild (repo-test)
    participant S3 as S3
    participant DDB as DynamoDB

    QA->>MCP: run_tests(uuid)
    MCP->>API: POST /projects/{id}/run-tests
    API->>API: owner-gate (QA owns the project)
    API->>DDB: PutItem META (qa_test_status:"running", version guard)
    API->>CB: StartBuild (PROJECT_UUID, PROJECT_SLUG)
    API-->>MCP: 202 {qa_test_status:"running"}

    CB->>S3: s3 sync {slug}/source/ → src
    Note over CB: auto-detect suite (npm test / pytest)
    CB->>S3: PutObject {slug}/aidlc-docs/qa/test-results.md
    CB->>API: aws lambda invoke → test-callback {status:"passed"|"failed", summary}
    API->>DDB: PutItem META (qa_test_status, qa_test_summary, version guard)
    API-->>CB: 200 {record}

    QA->>MCP: get_project(uuid)
    MCP->>API: GET /projects/{id}
    API-->>MCP: 200 {qa_test_status, qa_test_summary}
    Note over QA: QA decides — advance (forward) or return_for_changes (QA→Dev)
```

The `test-callback` records the outcome only; it never changes `handoff_state`. The
QA Engineer decides whether to advance or bounce the project back to the developer.
