# Implementation Plan — Kiro-Native Agentic SDLC Platform (Model 1)

Sequenced, incremental tasks. Each builds on the previous; tests are written with
the code. Deployments and AgentCore/CDK teardown are high-blast-radius — confirm
before running against the live account. Greenfield: no data migration.

- [x] 1. Workflow registry: stage contract + generator + advances_state
  - Add per-stage fields to `workflow_registry.json` for all active stages:
    `generator: "kiro"`, `produces[]` (`{path, required, acceptance[]}`),
    `consumes[]`, `skill`, and `advances_state` where a stage bumps the coarse
    owned state.
  - Extend `validate()` in the Python helper and the frontend TS mirror to check
    the new fields, skill references, `segment.stages` consistency, and that any
    `advances_state` is a legal forward target for the owning role.
  - Sync all three registry copies byte-identically; update the CDK drift
    assertion test to cover the new fields.
  - Unit tests for `validate()` (valid + malformed contracts).
  - _Requirements: 1.4, 4.1, 4.5, 6.1_

- [x] 2. DynamoDB record store (infrastructure)
  - Add the `AidlcProjects` DynamoDB table (on-demand) with GSIs: GSI1
    `ROLE#{owning_role}`, GSI2 `ENTITY#PROJECT` (all-projects/selection), GSI3
    `STATE#{handoff_state}` — in `cdk/lib/aidlc-platform-agents.ts`.
  - Grant the Project API Lambda least-privilege access (GetItem/PutItem/Query/
    TransactWriteItems on the table + GSIs).
  - CDK assertion tests for the table + GSIs.
  - _Requirements: 2.1, 13.1, 13.3_

- [x] 3. Project API record layer → DynamoDB
  - Replace `_read_record` / `_write_record` / `_list_records` in
    `cdk/custom-resources/project_api.py` with DynamoDB (GetItem / TransactWrite /
    Query); keep the registry, RBAC, and transition logic unchanged.
  - Implement conditional-write concurrency via a `version` attribute → HTTP 409
    on stale write.
  - Implement listing via GSI2 (broad visibility) + GSI1 (role queue) with
    pagination; `get_project` returns `current_stage` and `handoff_state`.
  - Seed new records with `current_stage` = first stage of the initial segment,
    `owning_role`, `version`, and the seed history entry.
  - Tests with a local DynamoDB / moto: CRUD, 409 concurrency, list/queue, and
    that RBAC (403/422) is unaffected.
  - _Requirements: 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.5, 5.1, 5.2, 6.2, 12.3, 12.4_

- [x] 4. Stage cursor + stage-brief endpoint
  - Add a helper to compute the active stage contract from the registry +
    `current_stage`, slug-substituting `produces`/`consumes` paths.
  - Add `GET /projects/{id}/stage-brief` (owner-read; returns the StageBrief).
  - Wire the route in CDK; tests for brief derivation and non-owner behavior.
  - _Requirements: 6.2, 6.4, 7.1_

- [x] 5. Acceptance validators (lenient v1)
  - Implement a validator registry (`nonempty`, `has_sections:...`,
    `min_words:N`, `valid_markdown`, extensible) as pure server-side functions.
  - Add `ACCEPTANCE_MODE` (lenient default | strict); lenient → warnings only,
    strict → block; unknown validator → warn (lenient).
  - Unit tests per validator and for lenient-warn vs strict-block behavior.
  - _Requirements: 8.1, 8.2, 8.3, 8.4_

- [x] 6. Artifact submission endpoint (presigned PUT)
  - Add `POST /projects/{id}/stage-artifact-url`: owner-gate (403); validate the
    requested `path` is in the active stage's `produces` (400 off-contract); mint
    a short-TTL SigV4 presigned PUT scoped to `{slug}/{path}`.
  - Scope the Lambda's `s3:PutObject` to `{*}/aidlc-docs/*`.
  - Wire the route in CDK; tests for owner-gate + path-guard + presign.
  - _Requirements: 5.1, 7.2, 9.1, 9.4_

- [x] 7. complete-stage endpoint (validate → advance)
  - Add `POST /projects/{id}/complete-stage`: owner-gate → HeadObject the required
    `produces` (block with `status:"blocked"` if a required artifact is
    missing/empty) → run acceptance (lenient warnings) → record artifact manifest
    (`submitted_by`/`at`/`etag`) → advance the cursor (and, if `advances_state`,
    advance the coarse state via `validate_transition`, 422 if illegal) → append a
    `HIST` item, all via `TransactWriteItems`.
  - Return `{status, warnings[], current_stage, handoff_state}`.
  - Tests: intra-segment advance, `advances_state`, missing-required block,
    lenient warnings recorded, 403 / 422 / 409.
  - _Requirements: 6.3, 7.3, 7.4, 8.1, 8.2, 9.3, 14.1_

- [x] 8. MCP server: new tools + AgentCore-client removal
  - Add `get_stage_brief`, `submit_artifact` (calls `stage-artifact-url` then PUTs
    the content), and `complete_stage` in `code/kiro-mcp/aidlc_mcp/tools.py`;
    register in `server.py`.
  - Remove `run_stage` / `answer_prompt` / `stage_status` / `close_stage` and
    delete `aidlc_mcp/agentcore_ws.py`; drop `AIDLC_RUNTIME_ARN` from `config.py`.
  - Retire `advance_project`; keep `handoff_project` and `return_for_changes`;
    ensure `get_project` surfaces `current_stage`.
  - Update tests (add new tools against fakes; confirm removals don't break
    registration).
  - _Requirements: 1.1, 1.2, 1.3, 7.1, 7.2, 7.3_

- [x] 9. Stage Skills (generation playbooks)
  - Author `.kiro/skills/<persona>/<stage>.md` for each active stage, encoding
    role/goal, `consumes` inputs, required output structure (aligned to
    `acceptance`), quality guidance, and `produces` paths. Start with
    `.kiro/skills/business-analyst/requirements-analysis.md`.
  - Ensure each `skill` name in the registry resolves to a file.
  - _Requirements: 1.4, 7.1_

- [x] 10. Persona agents: drive the brief→generate→submit→complete flow
  - Update `.kiro/agents/*.md`: remove `run_stage` tooling; add
    `get_stage_brief` / `submit_artifact` / `complete_stage`; keep
    `handoff_project` / `return_for_changes`; reference the stage Skills; keep
    Maintenance read-only.
  - _Requirements: 1.1, 10.2_

- [x] 11. Remove AgentCore (CDK + code)
  - Delete the Gateway, Runtime, and Memory constructs, the runtime
    env/authorizer wiring, and the M2M (client-credentials) client from
    `cdk/lib/aidlc-platform-agents.ts`; remove the agent image build
    (`AIDLCPlatformBuild` for `icode-agent`); remove the `Kiro*RuntimeArn` output
    and the Gateway/Memory SSM params.
  - Delete `code/agentcore-runtime/` outright.
  - Update CDK tests: drop Runtime/Gateway assertions; keep Project API / DynamoDB
    / Cognito / CodeBuild assertions.
  - _Requirements: 1.3, 10.1_

- [x] 12. Auth cleanup
  - Remove the client-credentials app client and the `agentcore` resource-server
    scope from `cdk/lib/aidlc-platform-auth.ts`; keep the Kiro client + the six
    groups + the post-confirmation role-assign trigger.
  - Update auth CDK tests.
  - _Requirements: 10.1, 10.3_

- [x] 13. Git round-trip on DynamoDB (retained, no AgentCore)
  - Update the ingest-callback and test-callback handlers to read/write the
    DynamoDB record (advance to QA on ingest ready; record `qa_test_status`).
  - Confirm `link_repo` / `submit_implementation` / `run_tests` operate against
    DynamoDB records and the CodeBuild projects are unchanged.
  - Tests for callbacks + submit gating (repo-linked).
  - _Requirements: 11.1, 11.2, 11.3, 11.4_

- [x] 14. Observability & audit
  - Ensure `HIST` items capture transitions, stage completions, and validation
    warnings; expose `current_stage` / `handoff_state`; provide Maintenance
    read-only queries via GSI2/GSI3.
  - Tests for history append and read-only Maintenance access.
  - _Requirements: 14.1, 14.2, 14.3_

- [x] 15. End-to-end pilot: BA `requirements_analysis`
  - Flip `requirements_analysis` to `generator: kiro` with its contract + skill;
    exercise the full path: `get_stage_brief` → (fixture-generated artifact) →
    `submit_artifact` → `complete_stage` → cursor advances to `user_stories`.
  - E2E test mirroring the existing harness (Kiro generation faked by fixtures).
  - _Requirements: 1.1, 6.3, 7.1, 7.2, 7.3, 8.1, 8.2_

- [x] 16. Roll forward remaining stages
  - Author Skills + set `generator: kiro` (+ `advances_state`) for the remaining
    BA, SA, Dev, QA, and DevOps stages; add per-segment E2E coverage; keep the Git
    round-trip through the Dev→QA boundary.
  - _Requirements: 4.3, 4.4, 6.1, 6.3, 7.1, 7.2, 7.3_

- [x] 17. Docs & steering update
  - Update steering (`00-project-overview`, `10-stable-identifiers` [drop runtime
    ARN / Gateway scheme], `60-testing` [add DynamoDB/moto suite], `70-build-and-
    deploy`) and the README to Model 1 (Kiro generates; AgentCore removed;
    DynamoDB record store). Reconcile `docs/` design notes.
  - _Requirements: cross-cutting_

- [x] 18. Full verification
  - Run all suites (CDK jest, `code/kiro-mcp` pytest, Project API pytest);
    `cdk synth`; grep the tree to confirm no residual AgentCore/Runtime/Gateway
    references remain in the active path.
  - _Requirements: all_
