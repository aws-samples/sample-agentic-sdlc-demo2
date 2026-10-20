"""Shared test fakes for the Platform MCP server unit tests.

No network, no boto3, no mcp SDK required — every external dependency is faked
and injected, per the design's testability goal.
"""

import base64
import json
import time
from typing import List, Optional, Tuple


def make_id_token(groups=None, exp: Optional[int] = None, extra: Optional[dict] = None) -> str:
    """Build a fake (unsigned) JWT whose payload carries cognito:groups + exp."""
    payload = {"cognito:groups": groups or []}
    if exp is not None:
        payload["exp"] = exp
    if extra:
        payload.update(extra)
    b = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{b}.sig"


class FakeCognito:
    """Fake cognito-idp client: records initiate_auth calls, returns scripted results."""

    def __init__(self, results=None, error=None):
        self._results = list(results or [])
        self._error = error
        self.calls: List[dict] = []

    def initiate_auth(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        if self._results:
            return self._results.pop(0)
        raise AssertionError("FakeCognito: no scripted result left")


class FakeTransport:
    """Fake HTTP transport for ProjectApiClient. Scriptable per call."""

    def __init__(self, responses):
        # responses: list of (status:int, body:dict|bytes|None)
        self._responses = list(responses)
        self.calls: List[Tuple[str, str, dict, Optional[bytes]]] = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        status, payload = self._responses.pop(0)
        if payload is None:
            return status, b""
        if isinstance(payload, (bytes, bytearray)):
            return status, bytes(payload)
        return status, json.dumps(payload).encode("utf-8")


class FakeS3:
    """Fake S3 client supporting list_objects_v2 + get_object."""

    def __init__(self, objects: dict):
        # objects: {key: str_content}
        self._objects = objects

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for k in self._objects if k.startswith(Prefix))
        return {"Contents": [{"Key": k, "Size": len(self._objects[k])} for k in keys],
                "IsTruncated": False}

    def get_object(self, Bucket, Key):
        if Key not in self._objects:
            raise KeyError(Key)
        data = self._objects[Key].encode("utf-8")

        class _Body:
            def read(self_inner):
                return data

        return {"Body": _Body()}
