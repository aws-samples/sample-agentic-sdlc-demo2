"""An in-memory fake of the subset of the boto3 S3 client used by ``project_api``.

The Project API handler talks to S3 through a handful of operations:
``get_object``, ``put_object`` (with optional conditional ``IfMatch``),
``list_objects_v2`` (via a paginator), ``delete_object`` and
``generate_presigned_url``. This fake reproduces just those, holding objects in
a plain dict keyed by ``(bucket, key)`` -> ``{"body": bytes, "etag": str}``.

It raises the *real* ``botocore.exceptions.ClientError`` so the handler's
``except ClientError`` branches (NoSuchKey -> 404, PreconditionFailed -> 409)
are exercised exactly as in production. ETags are content hashes wrapped in
quotes, mirroring S3's quoted ETag format, so a conditional write whose stored
content changed between read and write fails ``IfMatch`` just like real S3.

A one-shot ``on_next_get`` hook lets a test simulate a concurrent writer that
mutates an object in the window between the handler's read and its conditional
write — the trigger for the ETag-mismatch (HTTP 409) path.
"""

import hashlib
import io

from botocore.exceptions import ClientError


def _compute_etag(body: bytes) -> str:
    """Return a quoted content-hash ETag, mirroring S3's quoted ETag format."""
    return '"' + hashlib.md5(body, usedforsecurity=False).hexdigest() + '"'  # noqa: S324 (not security-sensitive)


class _Paginator:
    """Minimal stand-in for the boto3 ``list_objects_v2`` paginator."""

    def __init__(self, client):
        self._client = client

    def paginate(self, Bucket, Prefix="", **_kwargs):  # noqa: N803 (boto3 kwarg names)
        contents = [
            {"Key": key, "Size": len(rec["body"])}
            for (bucket, key), rec in sorted(self._client.store.items())
            if bucket == Bucket and key.startswith(Prefix)
        ]
        # A single page is sufficient for these tests; the handler iterates
        # pages and reads ``page.get("Contents", [])`` either way.
        yield {"Contents": contents}


class FakeS3Client:
    """In-memory fake S3 client exposing only what ``project_api`` calls."""

    def __init__(self):
        # (bucket, key) -> {"body": bytes, "etag": str}
        self.store = {}
        # Optional one-shot callback fired right after a get_object response is
        # built, used to simulate a concurrent writer (ETag-mismatch path).
        self._on_next_get = None
        # Test-visible counters/inspection aids.
        self.put_calls = 0
        self.delete_calls = 0
        self.presigned_calls = 0

    # -- test helpers -------------------------------------------------------

    def seed(self, bucket, key, body: bytes):
        """Directly place an object (bypassing conditional checks)."""
        self.store[(bucket, key)] = {"body": body, "etag": _compute_etag(body)}

    def raw_body(self, bucket, key):
        """Return the stored object body bytes, or ``None`` if absent."""
        rec = self.store.get((bucket, key))
        return rec["body"] if rec else None

    def on_next_get(self, callback):
        """Register a one-shot callback fired after the next get_object."""
        self._on_next_get = callback

    # -- emulated S3 operations --------------------------------------------

    def head_object(self, Bucket, Key, **_kwargs):  # noqa: N803
        """Return object metadata (``ContentLength``/``ETag``) or raise 404.

        Mirrors the boto3 ``head_object`` contract used by ``complete-stage`` to
        HeadObject the required ``produces`` (the missing/empty hard block): a
        missing key raises a ``404`` ``ClientError`` (S3 returns a bare ``404``
        error code for HEAD, not ``NoSuchKey``), and a present key reports its
        byte length so a zero-length (empty) required artifact can be detected.
        """
        rec = self.store.get((Bucket, Key))
        if rec is None:
            raise ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}},
                "HeadObject",
            )
        return {"ContentLength": len(rec["body"]), "ETag": rec["etag"]}

    def get_object(self, Bucket, Key, **_kwargs):  # noqa: N803
        rec = self.store.get((Bucket, Key))
        if rec is None:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
                "GetObject",
            )
        response = {
            "Body": io.BytesIO(rec["body"]),
            "ETag": rec["etag"],
        }
        # Fire the one-shot concurrent-writer hook, if any, AFTER the read
        # response is captured but BEFORE the caller's subsequent write.
        cb = self._on_next_get
        if cb is not None:
            self._on_next_get = None
            cb(self)
        return response

    def put_object(self, Bucket, Key, Body, IfMatch=None, **_kwargs):  # noqa: N803
        self.put_calls += 1
        if IfMatch is not None:
            current = self.store.get((Bucket, Key))
            current_etag = current["etag"] if current else None
            if current_etag != IfMatch:
                raise ClientError(
                    {"Error": {"Code": "PreconditionFailed",
                               "Message": "At least one of the preconditions "
                                          "you specified did not hold"}},
                    "PutObject",
                )
        body_bytes = Body if isinstance(Body, bytes) else bytes(Body)
        etag = _compute_etag(body_bytes)
        self.store[(Bucket, Key)] = {"body": body_bytes, "etag": etag}
        return {"ETag": etag}

    def delete_object(self, Bucket, Key, **_kwargs):  # noqa: N803
        self.delete_calls += 1
        self.store.pop((Bucket, Key), None)
        return {}

    def delete_objects(self, Bucket, Delete=None, **_kwargs):  # noqa: N803
        """Batch delete, mirroring the boto3 ``delete_objects`` contract."""
        deleted = []
        for obj in (Delete or {}).get("Objects", []):
            key = obj.get("Key")
            self.delete_calls += 1
            self.store.pop((Bucket, key), None)
            deleted.append({"Key": key})
        result = {}
        if not (Delete or {}).get("Quiet"):
            result["Deleted"] = deleted
        return result

    def get_paginator(self, operation_name):
        assert operation_name == "list_objects_v2", operation_name
        return _Paginator(self)

    def generate_presigned_url(self, ClientMethod, Params=None, ExpiresIn=None, **_kwargs):  # noqa: N803
        self.presigned_calls += 1
        params = Params or {}
        return (
            f"https://fake-s3.local/{params.get('Bucket')}/{params.get('Key')}"
            f"?op={ClientMethod}&expires={ExpiresIn}"
        )
