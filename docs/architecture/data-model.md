# Data model — DynamoDB single-table

The system of record is a single Amazon DynamoDB table,
`agentcore-strands-projects` (CDK construct `ProjectTable`, logical
`AidlcProjects`). It uses a single-table design keyed on `PK`/`SK` with three
global secondary indexes for the role queue, broad selection, and state
dashboards. Artifacts and ingested source stay in **S3**, not DynamoDB.

Defined in `cdk/lib/aidlc-platform-agents.ts`; all reads/writes are in
`cdk/custom-resources/project_api.py`.

- **Table key:** `PK` (String), `SK` (String)
- **Billing:** on-demand (`PAY_PER_REQUEST`)
- **Point-in-time recovery:** enabled

---

## 1. Item types

A project is a partition (`PK = PROJECT#{project_uuid}`) holding two kinds of item.

### 1.1 The `META` item (business record)

`PK = PROJECT#{uuid}`, `SK = META`. Carries the whole business record plus a
monotonically increasing `version` for optimistic concurrency. GSI projection keys
are derived on write and stripped on read.

| Field | Type | Notes |
| --- | --- | --- |
| `project_uuid` | string | client-minted UUID; the stable id and partition key source |
| `project_id` | string | slugified name (= `artifact_prefix`) |
| `name` | string | display name |
| `description` | string | |
| `type` | string | default `development` |
| `created_at` / `created_by` | string | ISO-8601 / actor email |
| `handoff_state` | string | coarse lifecycle state (`draft` … `deployed`) |
| `owning_role` | string | Cognito group that currently owns the project |
| `current_stage` | string | fine-grained stage cursor within the owning segment |
| `artifact_prefix` | string | S3 slug prefix for `{slug}/aidlc-docs/…` |
| `repo_url` / `repo_provider` / `repo_branch` / `repo_commit` | string/null | Git round-trip metadata |
| `repo_ingest_status` | string | `not_linked` → `pending` → `ready`/`failed` |
| `repo_last_synced_at` / `repo_ingest_error` | string/null | ingest outcome |
| `qa_test_status` | string | `not_run` → `running` → `passed`/`failed` |
| `qa_test_summary` / `qa_test_last_run_at` | string/null | test-runner outcome |
| `assignment_history` | list | in-record transition log (additive; complements the `HIST#` trail) |
| `artifacts` | map | manifest recorded at `complete-stage`: `{key: {submitted_by, submitted_at, etag}}` |
| `version` | number | optimistic-concurrency token |
| `updated_at` | string | set on every write; also the GSI sort key |

Storage-only attributes (not part of the business record, stripped on read):
`PK`, `SK`, `GSI1PK`, `GSI1SK`, `GSI2PK`, `GSI2SK`, `GSI3PK`, `GSI3SK`.

### 1.2 `HIST#` items (append-only audit trail)

`PK = PROJECT#{uuid}`, `SK = HIST#{ts}#{seq}` where `seq` is the record's new
`version` zero-padded to 6 digits (breaks ties within a one-second timestamp and
keeps items lexically ordered). History items carry **no GSI keys**, so they never
appear in the role/selection/state indexes.

| Field | `kind: "transition"` | `kind: "stage-complete"` |
| --- | --- | --- |
| `kind` | `transition` | `stage-complete` |
| `from_state` / `to_state` | ✓ | ✓ |
| `actor` | ✓ | ✓ |
| `note` | ✓ | ✓ |
| `timestamp` | ✓ | ✓ |
| `stage` / `next_stage` | — | ✓ |
| `warnings` | — | ✓ (acceptance warnings) |

A stage completion and a handoff transition both write one `HIST#` item atomically
with the `META` update (see [§3](#3-optimistic-concurrency)). A status-only update
(e.g. a late ingest callback that does not advance) writes no `HIST#` item.

---

## 2. Access patterns

Every list is served by an indexed `Query` — there is no unbounded `Scan`. GSI sort
keys are `updated_at`, queried `ScanIndexForward=False` (newest first).

| Access pattern | Index | Key condition |
| --- | --- | --- |
| Read one project record | base table | `PK = PROJECT#{uuid}` AND `SK = META` (strongly consistent) |
| Read a project's history | base table | `PK = PROJECT#{uuid}` AND `begins_with(SK, "HIST#")` |
| "Assigned to my role" queue | **GSI1** | `GSI1PK = ROLE#{owning_role}` |
| Broad "all projects" selection | **GSI2** | `GSI2PK = ENTITY#PROJECT` |
| State dashboard / SLA view | **GSI3** | `GSI3PK = STATE#{handoff_state}` |

`GET /projects` chooses the index from query params: `state` → GSI3, else `role` →
GSI1, else GSI2. With `limit`, a single page is returned with an opaque `next`
cursor (base64 of `LastEvaluatedKey`); without `limit`, all pages are aggregated so
routine selection sees the full list.

### GSI projection keys

Derived on every `META` write (`_to_item`):

| Index | PK | SK |
| --- | --- | --- |
| GSI1 (role queue) | `ROLE#{owning_role}` | `updated_at` |
| GSI2 (broad selection) | `ENTITY#PROJECT` | `updated_at` |
| GSI3 (state dashboard) | `STATE#{handoff_state}` | `updated_at` |

---

## 3. Optimistic concurrency

Every state-changing write is a **conditional** write guarded on `version`:

- **Create** — `PutItem` with `ConditionExpression: attribute_not_exists(PK)`, so a
  duplicate create cannot clobber an existing record. `version` seeds to `1`.
- **Simple update** (link-repo, run-tests, status-only ingest/test callback) —
  `PutItem` with `ConditionExpression: version = :expected`; a concurrent writer that
  bumped the version fails the condition.
- **Transition + stage-complete** — a `TransactWriteItems` writes the updated `META`
  and the new `HIST#` item together. The `META` `Put` is guarded on **both**
  `version = :expected` **and** `owning_role = :owner` (the owner read at the start
  of the request), so a concurrent handoff that changed ownership also cancels the
  transaction.

A failed conditional write (`ConditionalCheckFailedException`) or cancelled
transaction (`TransactionCanceledException`) surfaces as **HTTP 409**
(`ETAG_MISMATCH` / `ALREADY_EXISTS`) — the caller must refetch and retry. On write,
`updated_at` is refreshed and `version` bumped in place so the response carries the
freshly-versioned record.

---

## 4. What lives in S3, not DynamoDB

The DynamoDB record holds workflow state and a lightweight artifact manifest. The
artifact **content** and ingested source live in the project bucket
(`agentcore-files-<account>-<region>`):

- **Generated artifacts:** `{slug}/aidlc-docs/…` (the stored, stable prefix). The
  MCP server may display this as `agentic-sdlc/…`; the S3 key is never rewritten.
- **Uploaded requirements:** `{slug}/aidlc-docs/inception/requirements/uploaded/…`
- **Ingested repo source:** `{slug}/source/…`

The reserved `_platform/projects/` prefix is retained but unused — it exists so a
client-supplied `project_uuid` can never collide with a `{slug}/` artifact tree.

See [`sequence-flows.md`](sequence-flows.md) for how these items are written during
the generation flow and Git round-trip, and [`../ARCHITECTURE.md`](../ARCHITECTURE.md)
for the full system context.
