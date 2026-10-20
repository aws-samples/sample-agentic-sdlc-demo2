"""An in-memory fake of the subset of the boto3 DynamoDB client used by
``project_api``.

The Project API record layer talks to DynamoDB through a handful of operations:
``get_item`` (strongly-consistent read), ``put_item`` (with a conditional
``ConditionExpression`` on ``version`` or ``attribute_not_exists(PK)``),
``query`` (GSI1 role queue / GSI2 broad selection, newest-first, with cursor
pagination) and ``delete_item``. This fake reproduces just those, holding items
in a dict keyed by ``(PK, SK)`` -> the raw DynamoDB attribute-value map.

It raises the *real* ``botocore.exceptions.ClientError`` with
``ConditionalCheckFailedException`` so the handler's conditional-write branches
(stale ``version`` -> HTTP 409, duplicate create -> 409) are exercised exactly
as in production.

A one-shot ``on_next_get`` hook lets a test simulate a concurrent writer that
mutates an item in the window between the handler's read and its conditional
write — the trigger for the version-mismatch (HTTP 409) path.

Mirrors :mod:`fake_s3` in shape and intent.
"""

from decimal import Decimal

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()

# Storage-only attributes stripped when returning a business record (kept in
# sync with ``project_api._STORAGE_ATTRS``).
_STORAGE_ATTRS = frozenset(
    {"PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK", "GSI3PK", "GSI3SK"}
)


def _normalize_numbers(value):
    """Convert DynamoDB ``Decimal`` values back to ``int``/``float`` recursively."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [_normalize_numbers(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalize_numbers(v) for k, v in value.items()}
    return value


class FakeDynamoDBClient:
    """In-memory fake DynamoDB client exposing only what ``project_api`` calls."""

    def __init__(self):
        # (PK, SK) -> raw attribute-value item.
        self.store = {}
        self._on_next_get = None
        # Test-visible counters.
        self.put_calls = 0
        self.delete_calls = 0
        self.query_calls = 0

    # -- test helpers -------------------------------------------------------

    def get_record(self, project_uuid):
        """Return the deserialized business record for ``project_uuid`` or ``None``."""
        item = self.store.get((f"PROJECT#{project_uuid}", "META"))
        if item is None:
            return None
        return self._to_record(item)

    def history_items(self, project_uuid):
        """Return the deserialized ``HIST#`` items for ``project_uuid``, ts-ordered.

        ``complete-stage`` writes an append-only ``HIST#{ts}#{seq}`` item
        alongside the ``META`` update inside a single ``TransactWriteItems`` (the
        stage-completion audit trail, design Sections 3.5/9). This helper lets a
        test read them back without going through the (equality-only) GSI query.
        """
        pk = f"PROJECT#{project_uuid}"
        hist = [
            (sk, self._to_record(item))
            for (item_pk, sk), item in self.store.items()
            if item_pk == pk and sk.startswith("HIST#")
        ]
        hist.sort(key=lambda pair: pair[0])
        return [rec for _sk, rec in hist]

    def raw_item(self, project_uuid):
        """Return the stored raw attribute-value item, or ``None``."""
        return self.store.get((f"PROJECT#{project_uuid}", "META"))

    def bump_version(self, project_uuid, *, mutate=None):
        """Simulate a concurrent writer: increment the stored ``version``.

        Optionally applies ``mutate(record)`` to the deserialized record before
        re-storing, so a test can both bump the version (forcing the handler's
        conditional write to fail) and assert the concurrent writer's value
        survived.
        """
        key = (f"PROJECT#{project_uuid}", "META")
        item = self.store[key]
        record = self._to_record(item)
        record["version"] = int(record.get("version", 1)) + 1
        if mutate is not None:
            mutate(record)
        # Re-serialize, preserving the storage/GSI keys already on the item.
        new_item = {k: _SERIALIZER.serialize(v) for k, v in record.items()}
        for attr in _STORAGE_ATTRS:
            if attr in item:
                new_item[attr] = item[attr]
        self.store[key] = new_item

    def on_next_get(self, callback):
        """Register a one-shot callback fired after the next get_item."""
        self._on_next_get = callback

    # -- internals ----------------------------------------------------------

    def _to_record(self, item):
        record = {
            k: _DESERIALIZER.deserialize(v)
            for k, v in item.items()
            if k not in _STORAGE_ATTRS
        }
        return _normalize_numbers(record)

    # -- emulated DynamoDB operations --------------------------------------

    def get_item(self, TableName, Key, **_kwargs):  # noqa: N803
        pk = Key["PK"]["S"]
        sk = Key["SK"]["S"]
        item = self.store.get((pk, sk))
        response = {} if item is None else {"Item": dict(item)}
        cb = self._on_next_get
        if cb is not None:
            self._on_next_get = None
            cb(self)
        return response

    def put_item(self, TableName, Item, ConditionExpression=None,  # noqa: N803
                 ExpressionAttributeValues=None, **_kwargs):
        self.put_calls += 1
        pk = Item["PK"]["S"]
        sk = Item["SK"]["S"]
        existing = self.store.get((pk, sk))

        if ConditionExpression is not None:
            self._check_condition(
                ConditionExpression, existing, ExpressionAttributeValues or {}
            )

        self.store[(pk, sk)] = dict(Item)
        return {}

    def delete_item(self, TableName, Key, **_kwargs):  # noqa: N803
        self.delete_calls += 1
        self.store.pop((Key["PK"]["S"], Key["SK"]["S"]), None)
        return {}

    def transact_write_items(self, TransactItems, **_kwargs):  # noqa: N803
        """Emulate an all-or-nothing ``TransactWriteItems`` of ``Put`` actions.

        ``complete-stage`` writes the ``META`` update (conditional on ``version``
        AND ``owning_role``) and the ``HIST#`` audit item atomically (design
        Section 3.5). This fake evaluates every item's ``ConditionExpression``
        first; if any fails it raises a real ``TransactionCanceledException``
        (which ``project_api._is_concurrency_error`` maps to HTTP 409) and
        applies NO writes — matching DynamoDB's all-or-nothing semantics.
        """
        # Phase 1: evaluate all conditions against the CURRENT store (nothing is
        # written until every condition holds — all-or-nothing).
        prepared = []
        for entry in TransactItems:
            put = entry.get("Put")
            if put is None:
                raise AssertionError(f"Unsupported transact item in fake: {entry!r}")
            item = put["Item"]
            key = (item["PK"]["S"], item["SK"]["S"])
            cond = put.get("ConditionExpression")
            if cond is not None:
                existing = self.store.get(key)
                try:
                    self._check_condition(
                        cond, existing, put.get("ExpressionAttributeValues") or {}
                    )
                except ClientError:
                    # Any failed condition cancels the whole transaction.
                    self._raise_transaction_cancelled()
            prepared.append((key, dict(item)))

        # Phase 2: all conditions held — apply every write.
        self.put_calls += 1
        for key, item in prepared:
            self.store[key] = item
        return {}

    def query(self, TableName, IndexName=None, KeyConditionExpression=None,  # noqa: N803
              ExpressionAttributeValues=None, ScanIndexForward=True,
              Limit=None, ExclusiveStartKey=None, **_kwargs):
        self.query_calls += 1
        values = ExpressionAttributeValues or {}

        # Parse "<attr> = :placeholder".
        attr, _, placeholder = KeyConditionExpression.partition(" = ")
        attr = attr.strip()
        placeholder = placeholder.strip()
        target = values[placeholder]["S"]
        sk_attr = attr[:-2] + "SK"  # GSI2PK -> GSI2SK

        matches = [
            item for item in self.store.values()
            if item.get(attr, {}).get("S") == target
        ]
        # Sort by the index sort key.
        matches.sort(key=lambda it: it.get(sk_attr, {}).get("S", ""))
        if not ScanIndexForward:
            matches.reverse()

        # Cursor pagination: resume after the item named by ExclusiveStartKey.
        if ExclusiveStartKey is not None:
            start_pk = ExclusiveStartKey.get("PK", {}).get("S")
            start_sk = ExclusiveStartKey.get("SK", {}).get("S")
            for i, item in enumerate(matches):
                if item.get("PK", {}).get("S") == start_pk and \
                        item.get("SK", {}).get("S") == start_sk:
                    matches = matches[i + 1:]
                    break

        last_key = None
        if Limit is not None and len(matches) > Limit:
            page = matches[:Limit]
            tail = page[-1]
            last_key = {"PK": tail["PK"], "SK": tail["SK"]}
        else:
            page = matches

        response = {"Items": [dict(it) for it in page]}
        if last_key is not None:
            response["LastEvaluatedKey"] = last_key
        return response

    # -- condition evaluation ----------------------------------------------

    def _check_condition(self, expression, existing, values):
        """Evaluate the two ConditionExpressions ``project_api`` emits.

        Raises a real ``ConditionalCheckFailedException`` ClientError when the
        condition does not hold, matching production DynamoDB semantics.
        """
        expr = expression.strip()
        if expr == "attribute_not_exists(PK)":
            if existing is not None:
                self._raise_conditional_failed()
            return
        if expr == "version = :expected":
            expected = values[":expected"]["N"]
            current = (existing or {}).get("version", {}).get("N")
            if current != expected:
                self._raise_conditional_failed()
            return
        if expr == "version = :expected AND owning_role = :owner":
            # complete-stage guards the META update on BOTH the optimistic
            # version AND the owning_role (so a concurrent handoff that changed
            # ownership also cancels the transaction).
            expected = values[":expected"]["N"]
            owner = values[":owner"]["S"]
            current_version = (existing or {}).get("version", {}).get("N")
            current_owner = (existing or {}).get("owning_role", {}).get("S")
            if current_version != expected or current_owner != owner:
                self._raise_conditional_failed()
            return
        raise AssertionError(f"Unsupported ConditionExpression in fake: {expr!r}")

    @staticmethod
    def _raise_conditional_failed():
        raise ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException",
                       "Message": "The conditional request failed"}},
            "PutItem",
        )

    @staticmethod
    def _raise_transaction_cancelled():
        raise ClientError(
            {"Error": {"Code": "TransactionCanceledException",
                       "Message": "Transaction cancelled, please refer to "
                                  "reasons for details"}},
            "TransactWriteItems",
        )
