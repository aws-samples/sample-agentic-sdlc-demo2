"""Project API Lambda.

Provides the shared, server-side Project Store + Project API that replaces the
browser-local project list, so two different users (a Business Analyst and a
Solution Architect) can see and act on the same project (design Sections 2a-2c,
3a-3c, 7a).

Project records are stored in Amazon DynamoDB (``PROJECT_TABLE`` env var), the
system of record. Each project is one item (``PK=PROJECT#{uuid}``,
``SK=META``) carrying the whole business record plus a ``version`` attribute for
optimistic-concurrency (conditional-write) protection; GSIs provide the role
queue, broad "all projects" selection, and state dashboards without a scan.
Artifacts and ingested source remain in the project bucket (``PROJECT_BUCKET``);
the reserved ``_platform/projects/`` object-store prefix is retained but unused.

The data-driven handoff state machine (states, owning roles, permitted
transitions) is read from the shared ``workflow_registry`` module bundled
alongside this Lambda, so the BA->SA chain is encoded in exactly one place.

Routes (API Gateway proxy integration, Cognito-authorized):
  POST   /projects                                Create a project
  GET    /projects                                List projects (broad visibility)
  GET    /projects/{projectId}                    Read one project record
  GET    /projects/{projectId}/stage-brief        Active stage contract (owner-read)
  POST   /projects/{projectId}/stage-artifact-url Presigned PUT for a stage produces artifact
  POST   /projects/{projectId}/complete-stage     Validate + advance the stage cursor/state
  POST   /projects/{projectId}/transition         Perform a handoff transition
  POST   /projects/{projectId}/reassign           Manual reassignment
  POST   /projects/{projectId}/upload-url          Presigned PUT for requirements upload
  POST   /projects/{projectId}/link-repo           Link a Git repo (C7 round-trip)
  POST   /projects/{projectId}/ingest-callback     Ingestion pipeline callback (AWS_IAM)
  POST   /projects/{projectId}/run-tests           Start the QA test-runner (QA-owner-gated)
  POST   /projects/{projectId}/test-callback       QA test-runner callback (direct invoke)
  DELETE /projects/{projectId}                     Delete a project
"""

import base64
import json
import os
import re
import uuid as uuid_lib
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config
from botocore.exceptions import ClientError

import acceptance
import workflow_registry as registry

# ---------------------------------------------------------------------------
# AWS clients (module-level for Lambda container reuse)
# ---------------------------------------------------------------------------

_s3_client = None


def _s3():
    global _s3_client
    if _s3_client is None:
        # Force SigV4 presigning. The default client can presign with the
        # legacy SigV2 scheme (``AWSAccessKeyId=`` URLs), which folds
        # ``Content-Type`` into the signature. Browsers auto-add a
        # ``Content-Type`` header when PUTting a File body, which then no
        # longer matches the signed empty value → S3 returns 403
        # SignatureDoesNotMatch. SigV4 only signs ``host`` here, so the
        # browser's Content-Type is ignored and the upload succeeds.
        _s3_client = boto3.client(
            "s3",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
            config=Config(signature_version="s3v4"),
        )
    return _s3_client


_codebuild_client = None


def _codebuild():
    """Lazy CodeBuild client (module-level for Lambda container reuse)."""
    global _codebuild_client
    if _codebuild_client is None:
        _codebuild_client = boto3.client(
            "codebuild", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )
    return _codebuild_client


_secrets_client = None


def _secrets():
    """Lazy Secrets Manager client (module-level for Lambda container reuse)."""
    global _secrets_client
    if _secrets_client is None:
        _secrets_client = boto3.client(
            "secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )
    return _secrets_client


_dynamodb_client = None


def _ddb():
    """Lazy DynamoDB client (module-level for Lambda container reuse).

    The project record store is DynamoDB (system of record). The client
    is patched in unit tests with an in-memory fake, mirroring the ``_s3`` seam.
    """
    global _dynamodb_client
    if _dynamodb_client is None:
        _dynamodb_client = boto3.client(
            "dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )
    return _dynamodb_client


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Reserved platform prefix for project records in the object store. Records
# themselves now live in DynamoDB (system of record — see the record
# layer below); this prefix remains a *reserved* stable identifier (steering
# ``10-stable-identifiers``) and is intentionally left unused for artifacts so
# it can never collide with a ``{project_id}/`` artifact tree.
PROJECTS_PREFIX = "_platform/projects/"

# ---------------------------------------------------------------------------
# DynamoDB record store (system of record)
# ---------------------------------------------------------------------------
#
# Single-table design (``PROJECT_TABLE`` env var, keys ``PK``/``SK``). The
# project record is one item (``PK=PROJECT#{uuid}``, ``SK=META``) that carries
# the whole business record (state, owning_role, current_stage, repo/qa fields,
# assignment_history, ...) plus a monotonically increasing ``version`` used for
# optimistic-concurrency (conditional-write) protection. GSI projection keys
# (GSI1/2/3) are derived on write for the role queue, broad selection, and
# state dashboards; they are storage-only and are stripped from the business
# record on read.
#
# Concurrency: a state-changing write is a conditional ``PutItem`` guarded on
# the ``version`` attribute (``version = :expected``); a stale write fails the
# condition -> HTTP 409. Create is guarded on ``attribute_not_exists(PK)`` so a
# duplicate create cannot clobber an existing record. (Task 7 upgrades the write
# to ``TransactWriteItems`` when the append-only history moves to separate
# ``HIST#`` items; a single-item record needs only the conditional put here.)

# Sort key of the project record ("META") item.
_META_SK = "META"

# DynamoDB (de)serializers translate between Python values and the DynamoDB
# attribute-value wire format.
_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()

# Storage-only attributes that are not part of the business record (partition/
# sort keys and GSI projection keys) — stripped when reading an item back.
_STORAGE_ATTRS = frozenset(
    {"PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK", "GSI3PK", "GSI3SK"}
)

# Broad "all projects" GSI2 partition value (every record shares it so a single
# indexed Query lists all projects for selection without an unbounded scan).
_ALL_PROJECTS_GSI2PK = "ENTITY#PROJECT"

# Presigned upload URLs are short-lived (seconds).
UPLOAD_URL_TTL_SECONDS = 300

# Uploaded requirements documents land under this per-project sub-path
# (design Section 7a, Req 5.1).
UPLOAD_KEY_TEMPLATE = "{prefix}/aidlc-docs/inception/requirements/uploaded/{filename}"

# A project_uuid is the stable identifier (design Section 2a, Req 3.1) and is
# interpolated directly into the S3 record key ``_platform/projects/{uuid}.json``.
# Constrain it to a safe character set so a client-supplied value can never
# escape the platform prefix (path traversal) or destabilize the
# identifier->record mapping. A standard ``crypto.randomUUID()`` (hex + hyphens)
# and the Frontend slug fallback (``[a-z0-9_-]``) both satisfy this pattern.
_VALID_PROJECT_UUID = re.compile(r"^[A-Za-z0-9_-]{1,256}$")

# ---------------------------------------------------------------------------
# Git repository linking (C7 Git round-trip)
# ---------------------------------------------------------------------------

# The handoff state a project enters once the developer submits the linked
# implementation repository. Kept as a single named constant (the C7 "submit"
# boundary) so the transition-gating check reads clearly; the registry remains
# the authority on whether the transition itself is permitted.
IMPLEMENTATION_SUBMITTED_STATE = "implementation_submitted"

# Allowlist of Git hosting providers accepted by POST /link-repo. Restricting to
# an explicit set of public hosts is an SSRF guard: a client-supplied repo URL
# can never point the downstream ingestion job at an internal/metadata
# endpoint. Extend this map to onboard self-managed GitLab / GitHub Enterprise
# hosts.
_ALLOWED_REPO_HOSTS = {
    "github.com": "github",
    "www.github.com": "github",
    "gitlab.com": "gitlab",
    "www.gitlab.com": "gitlab",
}

# https://<host>/<owner>/<repo>[...] only. The host is checked separately
# against _ALLOWED_REPO_HOSTS; the charset excludes credentials ("@"), an
# explicit port (":") and whitespace so only a bare https URL matches.
_REPO_URL_RE = re.compile(
    r"^https://(?P<host>[A-Za-z0-9.-]+)/(?P<path>[A-Za-z0-9][A-Za-z0-9._/-]*)$"
)

# Branch assumed when the caller does not specify one.
_DEFAULT_REPO_BRANCH = "main"

# CodeBuild project (name set by CDK) that clones the linked repo and syncs it
# into the project's ``{slug}/source/`` prefix, then calls the ingest-callback
# route to advance the project to QA. When this env var is unset (unit tests, or
# a deployment that has not wired ingestion yet) linking simply records
# ``pending`` and no build is triggered — the feature degrades gracefully.
_INGEST_CODEBUILD_PROJECT_ENV = "REPO_INGEST_CODEBUILD_PROJECT"

# Actor recorded in assignment_history for automated (non-human) transitions
# performed by the ingestion pipeline.
_SYSTEM_INGEST_ACTOR = "system:repo-ingest"

# CodeBuild project (name set by CDK) that runs the ingested repo's tests and
# writes results back. Unset in unit tests / undeployed environments.
_TEST_CODEBUILD_PROJECT_ENV = "REPO_TEST_CODEBUILD_PROJECT"

# Secrets Manager path for a project's optional private-repo Git token. Matches
# the ID the ingest CodeBuild reads (`aidlc/projects/{uuid}/git`) and the stable
# per-project secrets prefix (steering `10-stable-identifiers.md`).
_GIT_SECRET_TEMPLATE = "aidlc/projects/{uuid}/git"  # nosec B105 Secrets Manager path, not a credential


def _store_git_token(project_uuid: str, token: str) -> None:
    """Store a private-repo Git token in Secrets Manager (put-or-create).

    Writes the token to ``aidlc/projects/{uuid}/git`` so the ingest CodeBuild
    can clone a private repository. Uses ``put_secret_value`` when the secret
    already exists and falls back to ``create_secret`` on first use. The token
    is never logged, returned, or written to the project record — only stored
    in Secrets Manager, referenced by name.
    """
    secret_id = _GIT_SECRET_TEMPLATE.format(uuid=project_uuid)
    client = _secrets()
    try:
        client.put_secret_value(SecretId=secret_id, SecretString=token)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            client.create_secret(Name=secret_id, SecretString=token)
        else:
            raise


def _validate_repo_url(url: str):
    """Validate a client-supplied Git repository URL (SSRF guard).

    Returns ``(provider, normalized_url)`` for an accepted URL, or
    ``(None, None)`` when the value is not an ``https`` URL to an allowlisted
    public Git host with an ``owner/repo`` path. Rejecting arbitrary hosts (and
    path traversal) prevents the downstream ingestion job from being pointed at
    an internal endpoint.
    """
    if not isinstance(url, str):
        return None, None
    candidate = url.strip()
    match = _REPO_URL_RE.match(candidate)
    if not match:
        return None, None
    provider = _ALLOWED_REPO_HOSTS.get(match.group("host").lower())
    if provider is None:
        return None, None
    path = match.group("path")
    if ".." in path:
        return None, None
    # Require at least an owner/repo pair.
    if len([seg for seg in path.split("/") if seg]) < 2:
        return None, None
    return provider, candidate.rstrip("/")


# ---------------------------------------------------------------------------
# Transition action vocabulary (registry-driven, design Sections 3b, 8d, 8e)
# ---------------------------------------------------------------------------
#
# A transition request names its intent with a high-level ``action`` rather than
# a raw target state. Two intents are recognized; both are resolved *through*
# the registry helpers (``forward``/``request_changes``) so the BA->SA chain is
# never hardcoded here -- adding a persona extends the registry, not this map.
#
#   * Forward intent (``assign``, ``approve``, the SA completion forwards, ...)
#     -> ``forward(current)`` (the state's single forward successor).
#   * Request-changes intent (``request-changes``, ``reject``, ...)
#     -> ``request_changes(current).target`` (a registry-defined backward
#     reassignment), recording the note in history.
#
# Action strings are normalized (lower-cased, whitespace/underscores collapsed
# to a single hyphen) before lookup so ``request_changes`` / ``request-changes``
# / ``Request Changes`` all match. Request-changes is matched *first* so a
# request-changes synonym can never fall through and be treated as a forward.
# An unrecognized action resolves to no target (HTTP 400) rather than silently
# advancing the state, which would otherwise always pass ``validate_transition``.

_REQUEST_CHANGES_ACTIONS = frozenset(
    {
        "request-changes",
        "changes-requested",
        "reject",
    }
)

_FORWARD_ACTIONS = frozenset(
    {
        "assign",          # requirements_ready -> assigned_to_sa (6.4)
        "approve",         # assigned_to_sa -> in_design (7.4)
        "accept",
        "complete",        # generic SA completion forward
        "design-complete", # in_design -> design_ready / design_ready -> ready_for_dev (7.7)
        "mark-design-complete",
        "advance",
        "forward",
        "next",
        "submit",
        "handoff",
        "ready",
    }
)


def _normalize_action(action: str) -> str:
    """Normalize a raw ``action`` string for synonym lookup.

    Lower-cases and collapses any run of whitespace/underscores to a single
    hyphen so ``request_changes``, ``request-changes`` and ``Request Changes``
    all resolve identically.
    """
    return re.sub(r"[\s_]+", "-", action.strip().lower())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
        "body": json.dumps(body),
    }


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (seconds precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(name: str) -> str:
    """Derive the S3 artifact-prefix slug from a project name.

    Mirrors the Frontend's slug derivation
    (``name.toLowerCase().replace(/\\s+/g, '_').replace(/[^a-z0-9_\\-]/g, '')``)
    so both personas resolve the same ``artifact_prefix`` for the same project
    (design Section 2a, Req 3.3). No trimming is applied so the result matches
    the ``project_id`` the Frontend already sends over the WebSocket.
    """
    slug = name.lower()
    slug = re.sub(r"\s+", "_", slug)
    slug = re.sub(r"[^a-z0-9_\-]", "", slug)
    return slug


def _is_valid_uuid(project_uuid: str) -> bool:
    """Return ``True`` when ``project_uuid`` is safe to use as a record key.

    The value is interpolated into the DynamoDB partition key ``PROJECT#{uuid}``
    (and per-project Secrets Manager / S3 artifact keys); rejecting anything
    outside ``[A-Za-z0-9_-]`` prevents path traversal and keeps the
    identifier->record mapping stable (design Section 2a, Req 3.1).
    """
    return bool(project_uuid) and bool(_VALID_PROJECT_UUID.match(project_uuid))


def _caller_identity(event: dict) -> dict:
    """Extract the caller's identity from the Cognito authorizer claims.

    The API Gateway Cognito authorizer places the verified JWT claims into
    ``event.requestContext.authorizer.claims``; these cannot be forged by the
    client (design Section 4b). Returns the caller's email/username and the raw
    Cognito group names. ``cognito:groups`` may arrive as a comma-separated
    string or a list depending on the authorizer.
    """
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    claims = authorizer.get("claims") or {}

    actor = (
        claims.get("email")
        or claims.get("cognito:username")
        or claims.get("username")
        or "unknown"
    )

    raw_groups = claims.get("cognito:groups", "")
    if isinstance(raw_groups, list):
        groups = [g for g in raw_groups if g]
    elif isinstance(raw_groups, str):
        groups = [g for g in raw_groups.split(",") if g]
    else:
        groups = []

    return {"actor": actor, "groups": groups}


def _authorize_owner(record: dict, identity: dict):
    """Owner-gate a state-changing action against the record's current state.

    A state-changing action (transition, reassign, delete) is permitted only
    when the caller's Cognito groups CONTAIN the record's current ``owning_role``
    -- which equals ``owning_role(handoff_state)`` and is the single role
    permitted to act while the project sits in its current ``Handoff_State``
    (design Section 4b, Req 2.2/2.3). This also enforces Req 6.5: while a project
    is ``assigned_to_sa`` (owning role ``system-architect``) a Business Analyst
    is not in the owning group and is therefore blocked.

    The group claim is supplied by the API Gateway Cognito authorizer from the
    verified JWT, so it cannot be forged by the client -- this is the trust
    anchor for Project API RBAC.

    Returns ``None`` when the caller is authorized, or a ready-to-return HTTP 403
    ``_response`` when not. Callers must invoke this *before* any S3 mutation so
    an unauthorized caller never changes state.
    """
    owning = record.get("owning_role")
    groups = identity.get("groups") or []
    if owning in groups:
        return None
    return _response(
        403,
        {
            "error": (
                f"Caller is not the owning role '{owning}' for this project's "
                "current handoff state"
            ),
            "code": "FORBIDDEN",
        },
    )


def _table_name() -> str:
    """Return the DynamoDB project-record table name (``PROJECT_TABLE`` env)."""
    return os.environ["PROJECT_TABLE"]


def _pk(project_uuid: str) -> str:
    """Return the DynamoDB partition key of a project record."""
    return f"PROJECT#{project_uuid}"


def _normalize_numbers(value):
    """Recursively convert DynamoDB ``Decimal`` values back to ``int``/``float``.

    ``TypeDeserializer`` maps every DynamoDB ``N`` to a ``Decimal``; converting
    integral Decimals to ``int`` (and the rest to ``float``) keeps the returned
    record JSON-serializable and identical in shape to the pre-DynamoDB record
    (e.g. ``version`` reads back as ``1``, not ``Decimal('1')``).
    """
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [_normalize_numbers(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalize_numbers(v) for k, v in value.items()}
    return value


def _to_item(project_uuid: str, record: dict) -> dict:
    """Serialize a business record into a DynamoDB item (keys + GSI projections).

    The whole business ``record`` is stored on the ``META`` item; the PK/SK and
    GSI projection keys (role queue / broad selection / state dashboard) are
    derived here so the record itself never has to carry storage concerns.
    """
    item = {k: _SERIALIZER.serialize(v) for k, v in record.items()}
    item["PK"] = {"S": _pk(project_uuid)}
    item["SK"] = {"S": _META_SK}

    # Sort GSIs newest-first at query time; the sort key is the last-updated
    # timestamp (falls back to created_at for robustness).
    updated_at = record.get("updated_at") or record.get("created_at") or _now_iso()
    owning = record.get("owning_role") or ""
    state = record.get("handoff_state") or ""

    # GSI1 — role queue: "assigned to my role".
    item["GSI1PK"] = {"S": f"ROLE#{owning}"}
    item["GSI1SK"] = {"S": updated_at}
    # GSI2 — broad "all projects" selection.
    item["GSI2PK"] = {"S": _ALL_PROJECTS_GSI2PK}
    item["GSI2SK"] = {"S": updated_at}
    # GSI3 — state dashboards / SLA views.
    item["GSI3PK"] = {"S": f"STATE#{state}"}
    item["GSI3SK"] = {"S": updated_at}
    return item


def _from_item(item: dict) -> dict:
    """Deserialize a DynamoDB item into the business record (storage keys stripped)."""
    record = {
        k: _DESERIALIZER.deserialize(v)
        for k, v in item.items()
        if k not in _STORAGE_ATTRS
    }
    return _normalize_numbers(record)


def _is_concurrency_error(e: ClientError) -> bool:
    """Return ``True`` when a DynamoDB error is a failed conditional write.

    A conditional ``PutItem`` (guarded on ``version`` or ``attribute_not_exists``)
    raises ``ConditionalCheckFailedException`` on a stale/duplicate write; a
    ``TransactWriteItems`` raises ``TransactionCanceledException``. Both mean the
    caller lost a race and must refetch and retry (HTTP 409).
    """
    return e.response.get("Error", {}).get("Code") in (
        "ConditionalCheckFailedException",
        "TransactionCanceledException",
    )


def _read_record(project_uuid: str):
    """Read a project record from DynamoDB.

    Returns ``(record, version)`` where ``record`` is the parsed business dict
    and ``version`` is the optimistic-concurrency token (used to guard the
    conditional write). Returns ``(None, None)`` when the record does not exist.
    A strongly-consistent read is used so a read-modify-write sees the latest
    committed ``version``.
    """
    resp = _ddb().get_item(
        TableName=_table_name(),
        Key={"PK": {"S": _pk(project_uuid)}, "SK": {"S": _META_SK}},
        ConsistentRead=True,
    )
    item = resp.get("Item")
    if not item:
        return None, None
    record = _from_item(item)
    return record, record.get("version")


def _write_record(project_uuid: str, record: dict, expected_version=None) -> None:
    """Write a project record to DynamoDB with optimistic-concurrency protection.

    Sets ``updated_at`` and bumps ``version`` on the record, then writes the
    ``META`` item with a conditional expression:

      * create (``expected_version is None``) -> ``attribute_not_exists(PK)`` so
        a duplicate create cannot clobber an existing record;
      * update -> ``version = :expected`` so a lost update from a concurrent
        writer fails the condition (raising ``ConditionalCheckFailedException``,
        surfaced by callers as HTTP 409 — design Section 3c, Req 2.4/4.5).

    The write mutates ``record`` in place (``version``/``updated_at``) so the
    caller returns the freshly-versioned record.
    """
    record["updated_at"] = _now_iso()
    if expected_version is None:
        record["version"] = 1
        kwargs = {"ConditionExpression": "attribute_not_exists(PK)"}
    else:
        expected = int(expected_version)
        record["version"] = expected + 1
        kwargs = {
            "ConditionExpression": "version = :expected",
            "ExpressionAttributeValues": {":expected": {"N": str(expected)}},
        }
    _ddb().put_item(
        TableName=_table_name(),
        Item=_to_item(project_uuid, record),
        **kwargs,
    )


def _hist_sk(ts: str, seq) -> str:
    """Return the sort key of an append-only history item.

    ``HIST#{ts}#{seq}`` (design Section 3.5). ``seq`` (the record's new
    ``version``, zero-padded) breaks ties when multiple items share the same
    second-precision timestamp and keeps the items lexically ordered.
    """
    return f"HIST#{ts}#{int(seq):06d}"


def _transact_write_with_history(
    project_uuid: str,
    record: dict,
    expected_version,
    expected_owner: str,
    hist_entry: dict,
) -> None:
    """Atomically update a project's ``META`` record + append a ``HIST#`` item.

    Both a **stage completion** and a **handoff transition** are state-changing
    ops that must be captured in the single append-only audit trail (design
    Sections 3.5/9, Req 14.1): they update the ``META`` item (bumping
    ``version``/``updated_at``, re-deriving the GSI projection keys) and write an
    append-only ``HIST#`` audit item (``kind: "stage-complete"`` or
    ``kind: "transition"``) in ONE ``TransactWriteItems`` so the record and its
    history can never diverge.

    The ``META`` ``Put`` is guarded on BOTH the optimistic ``version`` AND the
    ``owning_role`` read at the start of the request; a concurrent writer that
    bumped the version — or a concurrent handoff that changed ownership — fails
    the condition, cancels the whole transaction, and surfaces as HTTP 409
    (``TransactionCanceledException`` → :func:`_is_concurrency_error`). The
    write mutates ``record`` in place (``version``/``updated_at``) so the caller
    returns the freshly-versioned record.
    """
    ts = _now_iso()
    record["updated_at"] = ts
    new_version = int(expected_version) + 1
    record["version"] = new_version

    meta_item = _to_item(project_uuid, record)
    hist_item = {k: _SERIALIZER.serialize(v) for k, v in hist_entry.items()}
    hist_item["PK"] = {"S": _pk(project_uuid)}
    hist_item["SK"] = {"S": _hist_sk(ts, new_version)}

    _ddb().transact_write_items(
        TransactItems=[
            {
                "Put": {
                    "TableName": _table_name(),
                    "Item": meta_item,
                    "ConditionExpression": "version = :expected AND owning_role = :owner",
                    "ExpressionAttributeValues": {
                        ":expected": {"N": str(int(expected_version))},
                        ":owner": {"S": expected_owner},
                    },
                }
            },
            {
                "Put": {
                    "TableName": _table_name(),
                    "Item": hist_item,
                }
            },
        ]
    )


def _delete_record(project_uuid: str) -> None:
    """Delete a project record's ``META`` item from DynamoDB."""
    _ddb().delete_item(
        TableName=_table_name(),
        Key={"PK": {"S": _pk(project_uuid)}, "SK": {"S": _META_SK}},
    )


def _delete_artifact_prefix(bucket: str, artifact_prefix: str) -> int:
    """Best-effort delete of every object under a project's artifact prefix.

    Removes ``{artifact_prefix}/*`` when a project is deleted so a later
    project whose name slugifies to the same prefix starts clean, instead of
    inheriting the deleted project's stale state/artifacts (the cross-project
    "same name -> same slug" bleed).

    Guarded so it can never wipe unintended data:
      * a blank/whitespace-only prefix is ignored (never deletes the whole
        bucket), and
      * the trailing-slash boundary (``{prefix}/``) prevents a prefix like
        ``task`` from also matching ``task_flow/``.

    Returns the number of objects deleted (0 when nothing matched).
    """
    prefix = (artifact_prefix or "").strip().strip("/")
    if not prefix:
        return 0
    prefix_with_slash = prefix + "/"

    s3 = _s3()
    deleted = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix_with_slash):
        objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if not objects:
            continue
        # delete_objects accepts up to 1000 keys; list pages are <= 1000.
        s3.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
        deleted += len(objects)
    return deleted


def _list_records(role: str = None, state: str = None, limit: int = None,
                  start_key: dict = None):
    """List project records via an indexed Query (no unbounded scan — Req 3.5/13.1).

    Three access patterns, all indexed:
      * ``state`` given -> GSI3 (``STATE#{state}``): the cross-project state
        dashboard / SLA view — the read-only Maintenance persona filters
        projects by handoff state without a scan (design Section 4/9, Req 14.2);
      * ``role`` given  -> GSI1 (``ROLE#{role}``): the "assigned to my role"
        queue (Req 3.4 / 12.2);
      * otherwise       -> GSI2 (``ENTITY#PROJECT``): the broad "all projects"
        selection list, matching the current cross-creator visibility (Req 3.2).

    ``state`` is matched first so a maintenance/SLA dashboard can filter by state
    directly. Results are ordered newest-first (by the GSI sort key =
    ``updated_at``). When ``limit`` is given, a single page is returned as
    ``(records, last_evaluated_key)`` for cursor pagination; otherwise every
    page is aggregated and ``last_evaluated_key`` is ``None`` (preserves the
    "list returns everything visible" contract for routine selection).
    """
    if state:
        kwargs = {
            "IndexName": "GSI3",
            "KeyConditionExpression": "GSI3PK = :pk",
            "ExpressionAttributeValues": {":pk": {"S": f"STATE#{state}"}},
        }
    elif role:
        kwargs = {
            "IndexName": "GSI1",
            "KeyConditionExpression": "GSI1PK = :pk",
            "ExpressionAttributeValues": {":pk": {"S": f"ROLE#{role}"}},
        }
    else:
        kwargs = {
            "IndexName": "GSI2",
            "KeyConditionExpression": "GSI2PK = :pk",
            "ExpressionAttributeValues": {":pk": {"S": _ALL_PROJECTS_GSI2PK}},
        }
    kwargs["TableName"] = _table_name()
    kwargs["ScanIndexForward"] = False  # newest-first

    records = []

    if limit is not None:
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        kwargs["Limit"] = limit
        resp = _ddb().query(**kwargs)
        records = [_from_item(item) for item in resp.get("Items", [])]
        return records, resp.get("LastEvaluatedKey")

    # No limit: aggregate all pages so routine selection sees the full list.
    last = start_key
    while True:
        if last:
            kwargs["ExclusiveStartKey"] = last
        resp = _ddb().query(**kwargs)
        records.extend(_from_item(item) for item in resp.get("Items", []))
        last = resp.get("LastEvaluatedKey")
        if not last:
            break
    return records, None


def _encode_page_token(last_key: dict) -> str:
    """Encode a DynamoDB ``LastEvaluatedKey`` as an opaque pagination cursor."""
    return base64.urlsafe_b64encode(json.dumps(last_key).encode("utf-8")).decode("ascii")


def _decode_page_token(token: str):
    """Decode a pagination cursor back into a DynamoDB ``ExclusiveStartKey``.

    Returns ``None`` for a malformed token rather than raising, so a bad cursor
    degrades to "list from the start" instead of a 500.
    """
    try:
        return json.loads(base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8"))
    except (ValueError, TypeError):
        return None


def _resolve_target_state(current_state: str, body: dict):
    """Resolve the requested target Handoff_State from a transition request.

    A request may specify the target either explicitly (``target_state`` /
    ``target``) or, preferably, via a high-level ``action`` that is mapped to a
    registry edge:

      * a request-changes synonym (``request-changes``, ``reject`` ...) ->
        ``request_changes(current).target`` (a registry-defined backward
        reassignment); the note is taken from the request, or the registry's
        ``label`` when the caller supplied none (design Sections 3b, 8d);
      * a forward synonym (``assign``, ``approve``, ``design-complete`` ...) ->
        ``forward(current)`` (the state's single forward successor, design
        Sections 8d/8e).

    Request-changes synonyms are matched *first* so they can never fall through
    and be misread as a forward. An unrecognized action resolves to ``None`` so
    the caller returns HTTP 400 rather than silently advancing the state.

    Returns ``(target_state, note)`` or ``(None, None)`` when the request does
    not name a resolvable target. The returned target is *not* yet validated --
    ``validate_transition`` is the authority on whether it is permitted (Req 4.3,
    4.4).
    """
    note = body.get("note")

    explicit = body.get("target_state") or body.get("target")
    if explicit:
        return explicit, note

    raw_action = body.get("action")
    if not raw_action:
        return None, None

    action = _normalize_action(raw_action)
    if not action:
        return None, None

    # Request-changes is resolved first so a request-changes synonym can never
    # be misclassified as a forward transition.
    if action in _REQUEST_CHANGES_ACTIONS:
        rc = registry.request_changes(current_state)
        if rc is None:
            # No request-changes edge from this state; surface as unresolved so
            # validate_transition is not handed a forward target by accident.
            return None, note
        if note is None:
            note = rc.get("label")
        return rc.get("target"), note

    if action in _FORWARD_ACTIONS:
        return registry.forward(current_state), note

    # Unknown action: do not guess. Returning None yields an HTTP 400 instead of
    # silently treating it as a forward (which would always pass validation).
    return None, note


def _apply_transition(project_uuid: str, body: dict, identity: dict) -> dict:
    """Validate and apply a handoff transition with history + atomic write.

    Reads the record (capturing its ``version``), owner-gates the action against
    the record's current ``owning_role`` (HTTP 403 before any mutation),
    validates the requested transition against the registry, mutates
    ``handoff_state`` + ``owning_role`` together, appends one
    ``assignment_history`` entry, and writes the whole item back with a
    ``version``-conditional ``PutItem`` (design Sections 3c/4b, Req 2.2, 2.3,
    2.4, 4.3-4.6, 6.5).
    """
    record, version = _read_record(project_uuid)
    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    # RBAC: only the current owning role may change state. Checked after the
    # read but before any validate/mutate/write so an unauthorized caller never
    # changes state (design Section 4b, Req 2.2/2.3/6.5).
    denied = _authorize_owner(record, identity)
    if denied is not None:
        return denied

    actor = identity["actor"]
    current_state = record.get("handoff_state")
    target_state, note = _resolve_target_state(current_state, body)

    if not target_state:
        return _response(
            400,
            {"error": "Could not resolve a target state from the request",
             "code": "INVALID_REQUEST"},
        )

    if not registry.validate_transition(current_state, target_state):
        # Illegal transition: reject and leave the record unchanged (Req 4.4).
        return _response(
            422,
            {"error": f"Illegal transition: {current_state} -> {target_state}",
             "code": "ILLEGAL_TRANSITION"},
        )

    # C7 gate: entering the implementation-submitted state requires a linked
    # repository — the developer must link the pushed repo (POST /link-repo)
    # before submitting the implementation for QA. The registry permits the
    # transition; this adds the repo precondition on top.
    if target_state == IMPLEMENTATION_SUBMITTED_STATE and not record.get("repo_url"):
        return _response(
            422,
            {"error": "A repository must be linked (POST /link-repo) before "
                      "submitting the implementation",
             "code": "REPO_NOT_LINKED"},
        )

    # Capture the owner BEFORE the mutation: the atomic write is guarded on the
    # owning_role read at the start of the request (so a concurrent handoff that
    # changed ownership cancels the transaction → HTTP 409).
    expected_owner = record.get("owning_role")

    record["handoff_state"] = target_state
    record["owning_role"] = registry.owning_role(target_state)
    # Keep the in-record ``assignment_history`` list (additive — existing
    # consumers/tests rely on it) alongside the append-only ``HIST#`` audit
    # trail below.
    record.setdefault("assignment_history", []).append(
        {
            "from": current_state,
            "to": target_state,
            "actor": actor,
            "timestamp": _now_iso(),
            "note": note,
        }
    )

    # Append-only transition audit item written atomically with the META update
    # (design Sections 3.5/9, Req 14.1): the single history trail captures
    # transitions AND stage completions (who/from/to/when/note).
    hist_entry = {
        "kind": "transition",
        "from_state": current_state,
        "to_state": target_state,
        "actor": actor,
        "note": note,
        "timestamp": _now_iso(),
    }

    try:
        _transact_write_with_history(
            project_uuid, record, version, expected_owner, hist_entry
        )
    except ClientError as e:
        if _is_concurrency_error(e):
            # A concurrent writer updated the record first (Req 2.4/4.5).
            return _response(
                409,
                {"error": "Project was modified concurrently; refetch and retry",
                 "code": "ETAG_MISMATCH"},
            )
        print(f"[PROJECT_API] DynamoDB write error on transition: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_WRITE_FAILED"})

    # When the developer submits (project enters implementation_submitted), kick
    # off the repo ingestion build. Triggering here (not on link-repo) means the
    # ingest-callback always sees ``implementation_submitted`` and can advance to
    # QA deterministically. Guarded/degradable and best-effort (see helper).
    if target_state == IMPLEMENTATION_SUBMITTED_STATE:
        _maybe_trigger_ingest(project_uuid, record)

    return _response(200, record)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def _handle_create_project(body: dict, identity: dict) -> dict:
    """POST /projects — create and persist a new project record.

    Accepts a client-provided ``project_uuid`` (the Frontend already mints one
    via ``crypto.randomUUID()`` and threads it through the WebSocket init
    message) or generates a stable one. The id is validated and, when supplied
    by the client, must not already name an existing record so a create can
    never silently clobber a project's handoff state/history (Req 3.1).
    """
    name = (body.get("name") or "").strip()
    if not name:
        return _response(400, {"error": "'name' is required", "code": "INVALID_REQUEST"})

    client_supplied_uuid = bool(body.get("project_uuid"))
    project_uuid = body.get("project_uuid") or str(uuid_lib.uuid4())

    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'project_uuid' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    # A client-provided id must not already exist: re-creating onto an existing
    # record would reset its handoff_state/assignment_history, breaking the
    # stable identifier->record guarantee (Req 3.1).
    if client_supplied_uuid:
        try:
            existing, _ = _read_record(project_uuid)
        except ClientError as e:
            print(f"[PROJECT_API] DynamoDB read error on create existence check: {e}")
            return _response(500, {"error": str(e), "code": "RECORD_READ_FAILED"})
        if existing is not None:
            return _response(
                409,
                {"error": f"Project '{project_uuid}' already exists",
                 "code": "ALREADY_EXISTS"},
            )

    slug = _slugify(name)
    now = _now_iso()
    actor = identity["actor"]

    # Handoff state machine seeded from the registry's single source of truth
    # (design Section 3a): initial_state == "draft", owned by "requirements-analyst".
    initial_state = registry.initial_state()
    owning = registry.owning_role(initial_state)
    # Stage cursor (Req 6.2): seed to the first stage of the initial
    # state's segment, resolved through the registry so the cursor is never a
    # hardcoded literal.
    initial_segment = registry.segment_of(initial_state)
    initial_stage = registry.segment_stages(initial_segment)[0]

    record = {
        "project_uuid": project_uuid,
        "project_id": slug,
        "name": name,
        "description": body.get("description", ""),
        "type": body.get("type", "development"),
        "created_at": now,
        "created_by": actor,
        "handoff_state": initial_state,
        "owning_role": owning,
        # Fine-grained stage cursor within the current segment (design Section
        # 3.11); advanced by complete-stage (task 7).
        "current_stage": initial_stage,
        "artifact_prefix": slug,
        # Git round-trip (C7) metadata; populated by POST /link-repo and the
        # ingestion job. Present (as defaults) from creation so the UI/dashboard
        # can render repo status uniformly.
        "repo_url": None,
        "repo_provider": None,
        "repo_branch": None,
        "repo_commit": None,
        "repo_ingest_status": "not_linked",
        "repo_last_synced_at": None,
        "repo_ingest_error": None,
        # QA test execution (CodeBuild test-runner) status.
        "qa_test_status": "not_run",
        "qa_test_summary": None,
        "qa_test_last_run_at": None,
        "assignment_history": [
            {
                "from": None,
                "to": initial_state,
                "actor": actor,
                "timestamp": now,
                "note": "created",
            }
        ],
    }

    try:
        _write_record(project_uuid, record)
    except ClientError as e:
        if _is_concurrency_error(e):
            # A concurrent create for the same id won the race (Req 3.1).
            return _response(
                409,
                {"error": f"Project '{project_uuid}' already exists",
                 "code": "ALREADY_EXISTS"},
            )
        print(f"[PROJECT_API] DynamoDB write error on create: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_WRITE_FAILED"})

    return _response(201, record)


def _handle_list_projects(identity: dict, query_params: dict = None) -> dict:
    """GET /projects — list project records for selection (Req 3.1/3.2/3.5).

    Query parameters (all optional):
      * ``state`` (or ``handoff_state``) -> the cross-project state dashboard
        (GSI3), e.g. the read-only Maintenance persona filtering by handoff
        state for status/SLA monitoring (Req 14.2/14.3).
      * ``role`` (or ``owning_role``) -> the "assigned to my role" queue (GSI1);
        omitted -> the broad "all projects" list (GSI2), cross-creator visible.
      * ``limit`` + ``next`` -> cursor pagination so selection stays reliable as
        the number of projects grows (Req 3.5).

    This is a read route (not owner-gated), so the Maintenance persona — which
    owns no segment — can view cross-project status without any ability to
    modify state (Req 14.2).
    """
    query_params = query_params or {}
    role = query_params.get("role") or query_params.get("owning_role")
    state = query_params.get("state") or query_params.get("handoff_state")

    limit = None
    raw_limit = query_params.get("limit")
    if raw_limit is not None:
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return _response(
                400, {"error": "'limit' must be an integer", "code": "INVALID_REQUEST"}
            )
        if limit <= 0:
            return _response(
                400, {"error": "'limit' must be positive", "code": "INVALID_REQUEST"}
            )

    start_key = None
    raw_next = query_params.get("next")
    if raw_next:
        start_key = _decode_page_token(raw_next)

    try:
        records, last_key = _list_records(
            role=role, state=state, limit=limit, start_key=start_key
        )
    except ClientError as e:
        print(f"[PROJECT_API] DynamoDB list error: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_READ_FAILED"})

    body = {"projects": records}
    if last_key:
        body["next"] = _encode_page_token(last_key)
    return _response(200, body)


def _handle_get_project(project_uuid: str) -> dict:
    """GET /projects/{projectId} — read one project record.

    Broad visibility: the record is returned regardless of ``created_by`` so
    both personas resolve the same ``artifact_prefix`` for the same
    ``project_uuid`` (design Sections 2a/2c, Req 3.3). The record carries both
    the coarse ``handoff_state`` and the fine-grained ``current_stage`` cursor
    (Req 3.3/6.2). Not owner-gated.
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    try:
        record, _ = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] DynamoDB read error: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    return _response(200, record)


def _slug_prefix_path(artifact_prefix: str, path: str) -> str:
    """Slug-substitute a registry-relative artifact path into a concrete key.

    Stage contracts declare ``produces``/``consumes`` paths relative to the
    project (``aidlc-docs/...``); the concrete per-project artifact key is that
    path under the project's ``artifact_prefix`` (slug) — ``{slug}/{path}`` —
    mirroring how ``UPLOAD_KEY_TEMPLATE`` builds the uploaded-requirements key
    (design Sections 3.3/3.10). Leading/trailing slashes are normalized so the
    join never yields a doubled or empty separator; a blank prefix returns the
    bare relative path.
    """
    prefix = (artifact_prefix or "").strip().strip("/")
    rel = (path or "").strip().lstrip("/")
    if not prefix:
        return rel
    return f"{prefix}/{rel}"


def _derive_stage_brief(record: dict) -> dict:
    """Derive the active stage's contract (StageBrief) from the registry + cursor.

    Resolves the project's ``current_stage`` cursor to its registry stage
    contract (``generator`` / ``skill`` / ``consumes`` / ``produces`` +
    per-artifact ``required`` / ``acceptance``) and slug-substitutes every
    declared ``produces``/``consumes`` path into a concrete per-project key
    (``{artifact_prefix}/{path}``) so Kiro can read inputs and write outputs at
    the exact shared-storage locations (design Sections 3.3/5, Req 6.4). The
    contract is resolved *through* the registry helpers (``stage_segment`` /
    ``stage_contract`` / ``segment_owning_role``) — stage paths, skills, and the
    owning role are never hardcoded here (registry is SSOT, steering
    ``20-workflow-registry``).

    ``owning_role`` is the segment's owning role (the persona that produces the
    stage). ``approval_required`` is ``True`` for Kiro-generated stages: in
    the Kiro-native architecture every stage is completed by an explicit human
    persona action, and
    that act constitutes the recorded approval (Req 7.5).

    A cursor whose stage has no declared contract yet (e.g. the seeded
    ``workspace_detection``) yields a well-formed brief with an empty
    ``produces``/``consumes`` and null ``generator``/``skill`` rather than an
    error, so the endpoint degrades gracefully as stages are rolled onto the
    Kiro-native architecture.
    """
    prefix = record.get("artifact_prefix") or record.get("project_id") or ""
    stage = record.get("current_stage")

    segment = registry.stage_segment(stage) if stage else None
    contract = registry.stage_contract(stage) if stage else None

    if segment is not None:
        owning = registry.segment_owning_role(segment)
    else:
        owning = record.get("owning_role")

    if not contract:
        return {
            "stage": stage,
            "segment": segment,
            "owning_role": owning,
            "generator": None,
            "skill": None,
            "consumes": [],
            "produces": [],
            "approval_required": False,
        }

    generator = contract.get("generator", "kiro")
    consumes = [
        {"path": _slug_prefix_path(prefix, c)}
        for c in contract.get("consumes", [])
    ]
    produces = [
        {
            "path": _slug_prefix_path(prefix, art.get("path", "")),
            "required": bool(art.get("required", False)),
            "acceptance": list(art.get("acceptance", [])),
        }
        for art in contract.get("produces", [])
    ]
    return {
        "stage": stage,
        "segment": segment,
        "owning_role": owning,
        "generator": generator,
        "skill": contract.get("skill"),
        "consumes": consumes,
        "produces": produces,
        "approval_required": generator == "kiro",
    }


def _handle_stage_brief(project_uuid: str, identity: dict) -> dict:
    """GET /projects/{projectId}/stage-brief — derive the active stage contract.

    Owner-read (design Section 3.3, Req 6.4/7.1): the record is read first (404
    when missing) and the caller is authorized against the record's current
    ``owning_role`` before the brief is returned, so only the project's current
    owning role can fetch the brief for the stage it is producing. A non-owning
    caller is rejected with HTTP 403 before any contract is derived — the brief
    (which reveals the consume/produce artifact keys) is not disclosed to a
    persona whose turn it is not.
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    try:
        record, _ = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] DynamoDB read error on stage-brief: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    # RBAC: only the current owning role may read the stage brief. Checked after
    # the read (so a missing project is a 404, not a 403) but before the brief
    # is derived/returned (design Section 3.3, Req 6.4).
    denied = _authorize_owner(record, identity)
    if denied is not None:
        return denied

    return _response(200, _derive_stage_brief(record))


def _handle_stage_artifact_url(project_uuid: str, body: dict, identity: dict) -> dict:
    """POST /projects/{projectId}/stage-artifact-url — presigned PUT for a
    stage-produced artifact (Kiro-native submission, design Section 3.10).

    Mints a short-TTL SigV4 presigned PUT scoped to the *exact* S3 key of an
    artifact the active stage is declared to ``produces`` — so Kiro can stream a
    generated deliverable directly to shared storage without the client ever
    holding broad S3 write credentials (design Sections 3.6/3.10;
    Req 5.1/7.2/9.1/9.4).

    Guards, in order (a failing guard mints no URL):
      * invalid ``projectId`` → HTTP 400;
      * missing ``path`` → HTTP 400;
      * unknown project → HTTP 404;
      * caller is not the current ``owning_role`` → HTTP 403 (owner-gate, RBAC —
        design Section 4b, Req 5.1/9.4);
      * requested ``path`` is not one of the active stage's ``produces`` paths →
        HTTP 400 (off-contract, Req 7.2).

    Path contract (robust to either form the client sends): a stage contract's
    ``produces`` paths are declared **registry-relative** (``aidlc-docs/...``),
    but ``stage-brief`` returns them **slug-prefixed** (``{slug}/aidlc-docs/...``).
    A client may therefore submit either form; both are matched against the
    active stage's declared produces (comparing the requested path against the
    registry-relative path *and* its slug-prefixed form). The final S3 key is
    always rebuilt canonically as ``{slug}/{registry-relative-path}`` via
    ``_slug_prefix_path`` — so the signed key is identical regardless of which
    form the client sent, and can never fall outside the declared contract.

    Returns ``{upload_url, key, expires_in}`` (mirrors ``/upload-url``). The URL
    is minted with SigV4 (``_s3()``) so a client-added ``Content-Type`` cannot
    break the signature, and it expires in ``UPLOAD_URL_TTL_SECONDS``
    (short-lived). This endpoint is stateless/idempotent — artifact metadata
    (``submitted_by``/``submitted_at``/``etag``) is recorded later at
    ``complete-stage`` (design Section 3.10).
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    requested_path = (body.get("path") or "").strip()
    if not requested_path:
        return _response(400, {"error": "'path' is required", "code": "INVALID_REQUEST"})

    try:
        record, _ = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] DynamoDB read error on stage-artifact-url: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    # RBAC: only the current owning role may request a submission URL. Checked
    # after the read (a missing project is a 404, not a 403) but before any
    # presigned URL is minted, so a non-owning caller never obtains write access
    # to the artifact key (Req 5.1/9.4).
    denied = _authorize_owner(record, identity)
    if denied is not None:
        return denied

    # Resolve the active stage's declared produces (registry SSOT — never
    # hardcoded here) and match the requested path against them, accepting both
    # the registry-relative and the slug-prefixed form (see docstring).
    prefix = record.get("artifact_prefix") or record.get("project_id") or ""
    stage = record.get("current_stage")
    contract = registry.stage_contract(stage) if stage else None
    produces = (contract or {}).get("produces", [])

    normalized = requested_path.lstrip("/")
    matched_rel = None
    for art in produces:
        rel = (art.get("path") or "").strip().lstrip("/")
        if not rel:
            continue
        if normalized == rel or normalized == _slug_prefix_path(prefix, rel):
            matched_rel = rel
            break

    if matched_rel is None:
        return _response(
            400,
            {"error": (
                f"Path '{requested_path}' is not a declared 'produces' artifact "
                f"for the active stage '{stage}'"
            ),
             "code": "OFF_CONTRACT_PATH"},
        )

    bucket = os.environ["PROJECT_BUCKET"]
    # Canonical key: {slug}/{registry-relative-path}. The slug-prefixed key lands
    # under the project's `aidlc-docs/` artifact tree (the stored, stable prefix
    # — steering 10-stable-identifiers), which is exactly the scope of the
    # Lambda role's PutObject grant (`{*}/aidlc-docs/*`).
    key = _slug_prefix_path(prefix, matched_rel)

    try:
        url = _s3().generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=UPLOAD_URL_TTL_SECONDS,
        )
    except ClientError as e:
        print(f"[PROJECT_API] Presigned URL error: {e}")
        return _response(500, {"error": str(e), "code": "PRESIGN_FAILED"})

    return _response(200, {"upload_url": url, "key": key, "expires_in": UPLOAD_URL_TTL_SECONDS})


def _head_artifact(bucket: str, key: str):
    """HeadObject a produced artifact; return ``(exists, size, etag)``.

    Used for the missing/empty *required* artifact hard block (design Section 7):
    a ``404`` means the artifact was never submitted; ``ContentLength == 0`` means
    it is present but empty. Returns ``(False, 0, None)`` for a missing key and
    ``(True, size, etag)`` when present.
    """
    try:
        head = _s3().head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False, 0, None
        raise
    return True, int(head.get("ContentLength", 0)), head.get("ETag")


def _read_artifact_content(bucket: str, key: str) -> str:
    """GetObject a produced artifact and return its decoded text (``""`` if empty)."""
    resp = _s3().get_object(Bucket=bucket, Key=key)
    body = resp["Body"].read()
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    return body


def _handle_complete_stage(project_uuid: str, body: dict, identity: dict) -> dict:
    """POST /projects/{projectId}/complete-stage — validate then advance (design
    Sections 3.3/3.11/5, Req 6.3/7.3/7.4/8.1/8.2/9.3/14.1).

    Flow (a failing guard advances nothing):
      * invalid ``projectId`` → HTTP 400; unknown project → HTTP 404;
      * caller is not the current ``owning_role`` → HTTP 403 (owner-gate);
      * **HeadObject every required ``produces``**: a missing or empty required
        artifact is the one hard precondition → ``{status:"blocked"}`` with no
        advance (Req 7.4/8.1);
      * **run acceptance** over each present artifact's content (lenient v1:
        rule failures become ``warnings[]`` and DO NOT block; strict mode blocks
        — Req 8.2). An unknown validator warns (lenient) / blocks (strict);
      * **record the artifact manifest** (``submitted_by``/``submitted_at``/
        ``etag``) on the record (Req 9.3) and **advance the stage cursor** to the
        segment's next stage; WHEN the stage declares ``advances_state`` also
        advance the coarse ``handoff_state`` within the same owner via
        ``validate_transition`` (HTTP 422 if illegal — Req 6.3);
      * append a ``HIST#`` stage-completion item (with the validation warnings)
        atomically with the ``META`` update via ``TransactWriteItems`` (Req 14.1);
        a concurrent writer → HTTP 409.

    Completing the segment-terminal stage leaves the project in the segment's
    terminal *owned* state (the cursor stays; no next stage) — the cross-persona
    handoff is a SEPARATE explicit ``handoff_project`` action (design Section
    3.11), not performed here.

    Returns ``{status, warnings[], current_stage, handoff_state}``.
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    note = body.get("note")

    try:
        record, version = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] DynamoDB read error on complete-stage: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    # RBAC: only the current owning role may complete the active stage. Checked
    # after the read (missing project → 404, not 403) but before any validation
    # or write (design Section 4b, Req 5.1).
    denied = _authorize_owner(record, identity)
    if denied is not None:
        return denied

    current_state = record.get("handoff_state")
    stage = record.get("current_stage")
    prefix = record.get("artifact_prefix") or record.get("project_id") or ""
    bucket = os.environ["PROJECT_BUCKET"]

    # Resolve the active stage's declared produces (registry SSOT — never
    # hardcoded here). A stage without a contract (e.g. the seeded
    # workspace_detection) has no produces: nothing to validate, cursor advances.
    contract = registry.stage_contract(stage) if stage else None
    produces = (contract or {}).get("produces", [])
    mode = acceptance.resolve_mode()

    warnings: list = []
    missing_required: list = []
    manifest: dict = {}
    strict_blocked = False

    for art in produces:
        rel = (art.get("path") or "").strip().lstrip("/")
        if not rel:
            continue
        required = bool(art.get("required", False))
        rules = list(art.get("acceptance", []))
        key = _slug_prefix_path(prefix, rel)

        try:
            exists, size, etag = _head_artifact(bucket, key)
        except ClientError as e:
            print(f"[PROJECT_API] HeadObject error on complete-stage: {e}")
            return _response(500, {"error": str(e), "code": "ARTIFACT_HEAD_FAILED"})

        if not exists or size == 0:
            # The one hard precondition: a missing/empty REQUIRED artifact blocks
            # advancement (Req 7.4/8.1). A missing OPTIONAL artifact is a warning.
            reason = "missing" if not exists else "empty"
            if required:
                missing_required.append({"path": key, "reason": reason})
            else:
                warnings.append({
                    "path": key,
                    "rule": "nonempty",
                    "message": f"optional artifact not submitted ({reason})",
                })
            continue

        # Present: record the audit manifest and run content-based acceptance.
        manifest[key] = {
            "submitted_by": identity["actor"],
            "submitted_at": _now_iso(),
            "etag": etag,
        }
        try:
            content = _read_artifact_content(bucket, key)
        except ClientError as e:
            print(f"[PROJECT_API] GetObject error on complete-stage: {e}")
            return _response(500, {"error": str(e), "code": "ARTIFACT_READ_FAILED"})

        result = acceptance.evaluate(content, rules, mode=mode)
        for w in result.warnings:
            warnings.append({"path": key, **w})
        if result.blocked:
            strict_blocked = True

    # Hard block: a required artifact is missing/empty → do not advance (Req 7.4).
    if missing_required:
        return _response(
            200,
            {
                "status": "blocked",
                "reason": "required artifact(s) missing or empty",
                "missing": missing_required,
                "warnings": warnings,
                "current_stage": stage,
                "handoff_state": current_state,
            },
        )

    # Strict-mode acceptance failure also blocks (lenient default never sets this).
    if strict_blocked:
        return _response(
            200,
            {
                "status": "blocked",
                "reason": "acceptance validation failed (strict mode)",
                "warnings": warnings,
                "current_stage": stage,
                "handoff_state": current_state,
            },
        )

    # Resolve the advance: cursor to the segment's next stage, and — where the
    # stage declares advances_state — the coarse handoff_state within the same
    # owner (design Section 3.11). Both resolved through the registry.
    next_stage = registry.next_stage(stage) if stage else None
    advances_state = registry.stage_advances_state(stage) if stage else None

    new_state = current_state
    new_owner = record.get("owning_role")
    if advances_state:
        if not registry.validate_transition(current_state, advances_state):
            # A stage's declared advances_state must be a legal forward target
            # for the owning role; reject an illegal one and leave the record
            # unchanged (Req 6.3, mirrors the transition 422 semantics).
            return _response(
                422,
                {"error": f"Illegal stage advance: {current_state} -> {advances_state}",
                 "code": "ILLEGAL_TRANSITION"},
            )
        new_state = advances_state
        new_owner = registry.owning_role(advances_state)

    expected_owner = record.get("owning_role")

    # Apply the advance to the record.
    if next_stage is not None:
        record["current_stage"] = next_stage
    record["handoff_state"] = new_state
    record["owning_role"] = new_owner
    if manifest:
        record.setdefault("artifacts", {}).update(manifest)

    # Append-only stage-completion audit item (design Sections 3.5/9, Req 14.1).
    hist_entry = {
        "kind": "stage-complete",
        "stage": stage,
        "next_stage": record.get("current_stage"),
        "from_state": current_state,
        "to_state": new_state,
        "actor": identity["actor"],
        "note": note,
        "timestamp": _now_iso(),
        "warnings": warnings,
    }

    try:
        _transact_write_with_history(
            project_uuid, record, version, expected_owner, hist_entry
        )
    except ClientError as e:
        if _is_concurrency_error(e):
            # A concurrent writer/handoff won the race (Req 2.4/4.5).
            return _response(
                409,
                {"error": "Project was modified concurrently; refetch and retry",
                 "code": "ETAG_MISMATCH"},
            )
        print(f"[PROJECT_API] DynamoDB write error on complete-stage: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_WRITE_FAILED"})

    return _response(
        200,
        {
            "status": "advanced",
            "warnings": warnings,
            "current_stage": record.get("current_stage"),
            "handoff_state": record.get("handoff_state"),
        },
    )


def _handle_transition(project_uuid: str, body: dict, identity: dict) -> dict:
    """POST /projects/{projectId}/transition — perform a handoff transition."""
    try:
        return _apply_transition(project_uuid, body, identity)
    except ClientError as e:
        print(f"[PROJECT_API] DynamoDB error on transition: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_READ_FAILED"})


def _handle_reassign(project_uuid: str, body: dict, identity: dict) -> dict:
    """POST /projects/{projectId}/reassign — manual reassignment.

    Reassignment is validated through the same registry transition rules as a
    normal transition (design Section 3b); only registry-permitted targets are
    accepted.
    """
    try:
        return _apply_transition(project_uuid, body, identity)
    except ClientError as e:
        print(f"[PROJECT_API] DynamoDB error on reassign: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_READ_FAILED"})


def _handle_upload_url(project_uuid: str, body: dict, identity: dict) -> dict:
    """POST /projects/{projectId}/upload-url — presigned PUT for an upload.

    Returns a short-lived presigned PUT URL constrained to the exact
    ``uploaded/`` key for the project's artifact prefix (design Section 7a,
    Req 5.1). Keeps the browser's standing S3 permissions read-only.

    Owner-gated (BA action): the record is read first (404 when missing) and the
    caller is authorized against the record's current ``owning_role`` before any
    presigned URL is minted, so only the project's current owning role (the
    Business_Analyst while the project is in its requirements/draft phase) may
    request an upload URL (design Section 4b/7a, Req 5.1). The 403 occurs before
    the presigned PUT is generated.
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    filename = (body.get("filename") or "").strip()
    if not filename:
        return _response(400, {"error": "'filename' is required", "code": "INVALID_REQUEST"})

    # Guard against path traversal: only a bare filename is allowed.
    if "/" in filename or "\\" in filename or filename in (".", ".."):
        return _response(400, {"error": "Invalid filename", "code": "INVALID_REQUEST"})

    try:
        record, _ = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] DynamoDB read error on upload-url: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    # RBAC: only the current owning role may request an upload URL. Checked
    # after the read but before any presigned URL is minted so a non-owning
    # caller never obtains write access to the uploaded/ key (Req 5.1).
    denied = _authorize_owner(record, identity)
    if denied is not None:
        return denied

    bucket = os.environ["PROJECT_BUCKET"]
    key = UPLOAD_KEY_TEMPLATE.format(prefix=record["artifact_prefix"], filename=filename)

    try:
        url = _s3().generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=UPLOAD_URL_TTL_SECONDS,
        )
    except ClientError as e:
        print(f"[PROJECT_API] Presigned URL error: {e}")
        return _response(500, {"error": str(e), "code": "PRESIGN_FAILED"})

    return _response(200, {"upload_url": url, "key": key, "expires_in": UPLOAD_URL_TTL_SECONDS})


def _handle_link_repo(project_uuid: str, body: dict, identity: dict) -> dict:
    """POST /projects/{projectId}/link-repo — attach a Git repository (C7).

    After implementing from the construction brief in an external IDE (e.g.
    Kiro) and pushing to GitHub/GitLab, the Software Developer links the pushed
    repository to the project. This records the repo metadata on the project
    record so the (forthcoming) ingestion job can clone the code into the
    project's artifact prefix and the downstream QA/DevOps personas can act on
    it (design: Git round-trip C7).

    Owner-gated exactly like /upload-url: the record is read first (404 when
    missing) and the caller is authorized against the record's current
    ``owning_role`` before any mutation, so only the project's current owning
    role (the Software Developer while the project sits in ``assigned_to_dev``)
    may link a repo (design Section 4b, Req 2.2/2.3). The repo URL is validated
    against an allowlist of public Git hosts (SSRF guard) before it is stored.

    Records metadata only: it does not itself advance the handoff state (the
    developer submits via the normal transition route, which is gated on a
    linked repo) and does not yet trigger ingestion (the CodeBuild ingest job is
    wired in a later step — C7-D). The write is conditional on the record
    ``version``, so a concurrent update is rejected with HTTP 409.
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    provider, normalized_url = _validate_repo_url(body.get("repo_url") or "")
    if provider is None:
        return _response(
            400,
            {"error": "'repo_url' must be an https URL to a supported Git host "
                      "(github.com or gitlab.com) with an owner/repo path",
             "code": "INVALID_REPO_URL"},
        )

    branch = (body.get("branch") or _DEFAULT_REPO_BRANCH).strip() or _DEFAULT_REPO_BRANCH

    try:
        record, version = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] DynamoDB read error on link-repo: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    # RBAC: only the current owning role may link a repo. Checked after the read
    # but before any mutation so a non-owning caller never changes the record
    # (design Section 4b, Req 2.2/2.3).
    denied = _authorize_owner(record, identity)
    if denied is not None:
        return denied

    # Optional private-repo token: stored in Secrets Manager only (never on the
    # project record, never logged, never returned) so the ingest job can clone
    # a private repository. Stored before the record write so a credential
    # failure surfaces without half-linking the repo.
    git_token = body.get("git_token")
    if git_token:
        try:
            _store_git_token(project_uuid, git_token)
        except ClientError as e:
            # Log the error TYPE only — never the message/token.
            print(
                f"[PROJECT_API] Failed to store git token for {project_uuid}: "
                f"{type(e).__name__}"
            )
            return _response(
                500,
                {"error": "Failed to store repository credentials",
                 "code": "SECRET_WRITE_FAILED"},
            )

    record["repo_url"] = normalized_url
    record["repo_provider"] = provider
    record["repo_branch"] = branch
    # (Re)linking resets ingest tracking; the ingestion job (C7-D) will set the
    # synced commit, status and timestamp.
    record["repo_commit"] = None
    record["repo_ingest_status"] = "pending"
    record["repo_last_synced_at"] = None
    record["repo_ingest_error"] = None

    try:
        _write_record(project_uuid, record, expected_version=version)
    except ClientError as e:
        if _is_concurrency_error(e):
            return _response(
                409,
                {"error": "Project was modified concurrently; refetch and retry",
                 "code": "ETAG_MISMATCH"},
            )
        print(f"[PROJECT_API] DynamoDB write error on link-repo: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_WRITE_FAILED"})

    # Note: ingestion is NOT triggered here. It is triggered when the project
    # actually enters ``implementation_submitted`` (the submit transition), so
    # the ingest-callback always observes the submit state and can advance to QA
    # deterministically (avoids a link/submit race).
    return _response(200, record)


def _maybe_trigger_ingest(project_uuid: str, record: dict) -> None:
    """Start the ingest CodeBuild for ``project_uuid`` when one is configured."""
    codebuild_project = os.environ.get(_INGEST_CODEBUILD_PROJECT_ENV)
    if not codebuild_project:
        return
    try:
        _codebuild().start_build(
            projectName=codebuild_project,
            environmentVariablesOverride=[
                {"name": "PROJECT_UUID", "value": project_uuid, "type": "PLAINTEXT"},
                {"name": "REPO_URL", "value": record.get("repo_url") or "", "type": "PLAINTEXT"},
                {"name": "REPO_BRANCH", "value": record.get("repo_branch") or _DEFAULT_REPO_BRANCH, "type": "PLAINTEXT"},
                {"name": "PROJECT_SLUG", "value": record.get("artifact_prefix") or "", "type": "PLAINTEXT"},
            ],
        )
    except ClientError as e:
        print(f"[PROJECT_API] StartBuild failed for ingest of {project_uuid}: {e}")


def _maybe_trigger_tests(project_uuid: str, record: dict) -> None:
    """Start the QA test-runner CodeBuild for ``project_uuid`` when configured."""
    codebuild_project = os.environ.get(_TEST_CODEBUILD_PROJECT_ENV)
    if not codebuild_project:
        return
    try:
        _codebuild().start_build(
            projectName=codebuild_project,
            environmentVariablesOverride=[
                {"name": "PROJECT_UUID", "value": project_uuid, "type": "PLAINTEXT"},
                {"name": "PROJECT_SLUG", "value": record.get("artifact_prefix") or "", "type": "PLAINTEXT"},
            ],
        )
    except ClientError as e:
        print(f"[PROJECT_API] StartBuild failed for tests of {project_uuid}: {e}")


def _handle_ingest_callback(project_uuid: str, body: dict) -> dict:
    """POST /projects/{projectId}/ingest-callback — ingestion pipeline callback.

    Called by the ingest CodeBuild job (authorized at the API Gateway with
    ``AWS_IAM`` — the CodeBuild role — NOT the Cognito user authorizer), so this
    handler is a trusted internal caller and is deliberately NOT owner-gated by
    Cognito group; the IAM policy on the route is the trust anchor.

    Records the sync outcome on the project record and, on success, advances the
    handoff state from ``implementation_submitted`` to its forward successor
    (``assigned_to_qa``) through the shared registry — ``validate_transition``
    remains the single authority on transition legality. The advance is
    idempotent: if the project is already past ``implementation_submitted`` (a
    duplicate/late callback) the status fields are updated but the state is left
    unchanged. Uses a ``version``-conditional write (409 on concurrent modification).
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    status = (body.get("status") or "").strip().lower()
    if status not in ("ready", "failed"):
        return _response(
            400,
            {"error": "'status' must be 'ready' or 'failed'", "code": "INVALID_REQUEST"},
        )

    try:
        record, version = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] DynamoDB read error on ingest-callback: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    # Owner read before any mutation: the atomic advance write is guarded on it.
    expected_owner = record.get("owning_role")

    record["repo_commit"] = body.get("commit") or record.get("repo_commit")
    record["repo_last_synced_at"] = _now_iso()
    record["repo_ingest_status"] = "ready" if status == "ready" else "failed"
    # Capture/clear a human-readable failure reason (e.g. "repository exceeds
    # the ingest size limit") so the UI can explain a failed ingest.
    record["repo_ingest_error"] = None if status == "ready" else (
        body.get("reason") or "ingestion failed"
    )

    advanced = False
    advance_from = None
    advance_to = None
    if status == "ready":
        current_state = record.get("handoff_state")
        # Only advance out of the submit state; a duplicate/late callback for a
        # project already in QA (or beyond) updates status only.
        if current_state == IMPLEMENTATION_SUBMITTED_STATE:
            target_state = registry.forward(current_state)
            if target_state and registry.validate_transition(current_state, target_state):
                record["handoff_state"] = target_state
                record["owning_role"] = registry.owning_role(target_state)
                record.setdefault("assignment_history", []).append(
                    {
                        "from": current_state,
                        "to": target_state,
                        "actor": _SYSTEM_INGEST_ACTOR,
                        "timestamp": _now_iso(),
                        "note": "repository ingested; advanced to QA",
                    }
                )
                advanced = True
                advance_from = current_state
                advance_to = target_state

    try:
        if advanced:
            # An auto-advance is a transition: capture it in the append-only
            # HIST# trail atomically with the META update (design Sections
            # 3.5/9, Req 14.1). A status-only update (no advance) is not a
            # transition and writes no HIST item.
            hist_entry = {
                "kind": "transition",
                "from_state": advance_from,
                "to_state": advance_to,
                "actor": _SYSTEM_INGEST_ACTOR,
                "note": "repository ingested; advanced to QA",
                "timestamp": _now_iso(),
            }
            _transact_write_with_history(
                project_uuid, record, version, expected_owner, hist_entry
            )
        else:
            _write_record(project_uuid, record, expected_version=version)
    except ClientError as e:
        if _is_concurrency_error(e):
            return _response(
                409,
                {"error": "Project was modified concurrently; refetch and retry",
                 "code": "ETAG_MISMATCH"},
            )
        print(f"[PROJECT_API] DynamoDB write error on ingest-callback: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_WRITE_FAILED"})

    return _response(200, {"record": record, "advanced": advanced})


def _handle_run_tests(project_uuid: str, identity: dict) -> dict:
    """POST /projects/{projectId}/run-tests — start the QA test-runner (C7).

    Owner-gated (only the project's current owning role — the QA Engineer while
    the project sits in a QA state — may run tests). Marks the record
    ``qa_test_status="running"`` and starts the test CodeBuild, which pulls the
    ingested source, runs the auto-detected test suite, writes
    ``aidlc-docs/qa/test-results.md``, and calls back with pass/fail. Degrades
    gracefully (no build started) when no test project is configured.
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    try:
        record, version = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] DynamoDB read error on run-tests: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    denied = _authorize_owner(record, identity)
    if denied is not None:
        return denied

    record["qa_test_status"] = "running"
    record["qa_test_last_run_at"] = _now_iso()

    try:
        _write_record(project_uuid, record, expected_version=version)
    except ClientError as e:
        if _is_concurrency_error(e):
            return _response(
                409,
                {"error": "Project was modified concurrently; refetch and retry",
                 "code": "ETAG_MISMATCH"},
            )
        print(f"[PROJECT_API] DynamoDB write error on run-tests: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_WRITE_FAILED"})

    _maybe_trigger_tests(project_uuid, record)
    return _response(202, record)


def _handle_test_callback(project_uuid: str, body: dict) -> dict:
    """POST /projects/{projectId}/test-callback — QA test-runner callback.

    Trusted internal caller (invoked by the test CodeBuild via direct Lambda
    invoke, not Cognito-gated). Records the test outcome (``passed``/``failed``)
    and a short summary; does not itself change the handoff state (the QA
    Engineer decides whether to advance or bounce). ``version``-conditional write.
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    status = (body.get("status") or "").strip().lower()
    if status not in ("passed", "failed"):
        return _response(
            400,
            {"error": "'status' must be 'passed' or 'failed'", "code": "INVALID_REQUEST"},
        )

    try:
        record, version = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] DynamoDB read error on test-callback: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    record["qa_test_status"] = status
    record["qa_test_summary"] = body.get("summary") or None
    record["qa_test_last_run_at"] = _now_iso()

    try:
        _write_record(project_uuid, record, expected_version=version)
    except ClientError as e:
        if _is_concurrency_error(e):
            return _response(
                409,
                {"error": "Project was modified concurrently; refetch and retry",
                 "code": "ETAG_MISMATCH"},
            )
        print(f"[PROJECT_API] DynamoDB write error on test-callback: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_WRITE_FAILED"})

    return _response(200, {"record": record})


def _handle_delete_project(project_uuid: str, identity: dict) -> dict:
    """DELETE /projects/{projectId} — delete a project record.

    Owner-gated: the record is read first (404 when missing) and the caller is
    authorized against the record's current ``owning_role`` before any delete,
    so a non-owning caller never removes a project (design Section 4b, Req
    2.2/2.3). The 403 occurs before the S3 ``delete_object`` mutation.
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    try:
        record, _ = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] DynamoDB read error on delete: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    denied = _authorize_owner(record, identity)
    if denied is not None:
        return denied

    bucket = os.environ["PROJECT_BUCKET"]

    # Remove the project's artifact tree first (best-effort) so a future
    # same-named project does not inherit stale state. Artifacts remain in S3
    # even though the record now lives in DynamoDB. Failures here are logged but
    # do not block deletion of the record itself.
    artifact_prefix = record.get("artifact_prefix") or record.get("project_id") or ""
    artifacts_removed = 0
    try:
        artifacts_removed = _delete_artifact_prefix(bucket, artifact_prefix)
    except ClientError as e:
        print(
            f"[PROJECT_API] Artifact cleanup error for prefix "
            f"'{artifact_prefix}': {e}"
        )

    try:
        _delete_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] DynamoDB delete error: {e}")
        return _response(500, {"error": str(e), "code": "RECORD_DELETE_FAILED"})

    return _response(
        200,
        {
            "deleted": True,
            "project_uuid": project_uuid,
            "artifacts_removed": artifacts_removed,
        },
    )


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def _parse_body(event: dict):
    """Parse the JSON request body; returns ``(body, error_response)``."""
    raw_body = event.get("body") or "{}"
    try:
        return json.loads(raw_body), None
    except (json.JSONDecodeError, TypeError):
        return None, _response(400, {"error": "Invalid JSON body", "code": "INVALID_REQUEST"})


def handler(event, context):
    """Lambda entry point for API Gateway proxy integration."""
    print(f"[PROJECT_API] Event: {json.dumps(event)[:500]}")

    http_method = event.get("httpMethod", "")
    resource = event.get("resource", "")
    path_params = event.get("pathParameters") or {}
    project_uuid = path_params.get("projectId", "")

    identity = _caller_identity(event)

    # Collection routes ------------------------------------------------------

    if http_method == "POST" and resource == "/projects":
        body, err = _parse_body(event)
        if err:
            return err
        return _handle_create_project(body, identity)

    if http_method == "GET" and resource == "/projects":
        return _handle_list_projects(identity, event.get("queryStringParameters"))

    # Item routes ------------------------------------------------------------

    if resource.startswith("/projects/{projectId}") and not project_uuid:
        return _response(400, {"error": "Missing projectId", "code": "INVALID_REQUEST"})

    if http_method == "GET" and resource == "/projects/{projectId}":
        return _handle_get_project(project_uuid)

    if http_method == "GET" and resource == "/projects/{projectId}/stage-brief":
        return _handle_stage_brief(project_uuid, identity)

    if http_method == "POST" and resource == "/projects/{projectId}/stage-artifact-url":
        body, err = _parse_body(event)
        if err:
            return err
        return _handle_stage_artifact_url(project_uuid, body, identity)

    if http_method == "POST" and resource == "/projects/{projectId}/complete-stage":
        body, err = _parse_body(event)
        if err:
            return err
        return _handle_complete_stage(project_uuid, body, identity)

    if http_method == "DELETE" and resource == "/projects/{projectId}":
        return _handle_delete_project(project_uuid, identity)

    if http_method == "POST" and resource == "/projects/{projectId}/transition":
        body, err = _parse_body(event)
        if err:
            return err
        return _handle_transition(project_uuid, body, identity)

    if http_method == "POST" and resource == "/projects/{projectId}/reassign":
        body, err = _parse_body(event)
        if err:
            return err
        return _handle_reassign(project_uuid, body, identity)

    if http_method == "POST" and resource == "/projects/{projectId}/upload-url":
        body, err = _parse_body(event)
        if err:
            return err
        return _handle_upload_url(project_uuid, body, identity)

    if http_method == "POST" and resource == "/projects/{projectId}/link-repo":
        body, err = _parse_body(event)
        if err:
            return err
        return _handle_link_repo(project_uuid, body, identity)

    if http_method == "POST" and resource == "/projects/{projectId}/ingest-callback":
        body, err = _parse_body(event)
        if err:
            return err
        return _handle_ingest_callback(project_uuid, body)

    if http_method == "POST" and resource == "/projects/{projectId}/run-tests":
        return _handle_run_tests(project_uuid, identity)

    if http_method == "POST" and resource == "/projects/{projectId}/test-callback":
        body, err = _parse_body(event)
        if err:
            return err
        return _handle_test_callback(project_uuid, body)

    return _response(
        400, {"error": f"Unknown route: {http_method} {resource}", "code": "INVALID_REQUEST"}
    )
