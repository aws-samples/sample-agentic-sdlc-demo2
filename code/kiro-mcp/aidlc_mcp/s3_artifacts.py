"""S3 artifact reader (design Section 2d, Requirement 9).

Lists and reads a project's generated artifacts under its ``artifact_prefix``,
using the MCP server's own AWS credentials. Reads are constrained to the
requested project's prefix (Requirement 9.4). Artifacts are addressed by their
**stored** S3 key using the stable ``aidlc-docs/`` prefix; the key is never
rewritten (Requirement 9.3 / stable-identifiers). The ``agentic-sdlc`` display
label is presentation-only and applied in the tool layer, not here.

The S3 client is injected for testability; :meth:`_default_client` lazily builds
a ``boto3`` client.
"""

from __future__ import annotations

from typing import List, Optional


class ArtifactAccessError(RuntimeError):
    """Raised when a requested key escapes the project's artifact prefix."""


class S3ArtifactReader:
    def __init__(self, bucket: str, region: str, s3_client=None):
        self._bucket = bucket
        self._region = region
        self._client = s3_client

    def _s3(self):
        if self._client is None:
            import boto3  # lazy

            self._client = boto3.client("s3", region_name=self._region)
        return self._client

    @staticmethod
    def _norm_prefix(artifact_prefix: str) -> str:
        prefix = (artifact_prefix or "").strip().strip("/")
        if not prefix:
            raise ArtifactAccessError("Empty artifact prefix.")
        return prefix

    def list_artifacts(self, artifact_prefix: str) -> List[dict]:
        """Return objects under ``{artifact_prefix}/`` as ``{key, size, last_modified}``."""
        prefix = self._norm_prefix(artifact_prefix) + "/"
        s3 = self._s3()
        items: List[dict] = []
        token: Optional[str] = None
        while True:
            kwargs = {"Bucket": self._bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []) or []:
                lm = obj.get("LastModified")
                items.append(
                    {
                        "key": obj.get("Key"),
                        "size": obj.get("Size"),
                        "last_modified": lm.isoformat() if hasattr(lm, "isoformat") else lm,
                    }
                )
            if resp.get("IsTruncated") and resp.get("NextContinuationToken"):
                token = resp["NextContinuationToken"]
            else:
                break
        return items

    def resolve_key(self, artifact_prefix: str, key_or_relpath: str) -> str:
        """Resolve a stored key from either a full key or a prefix-relative path.

        Guards against path traversal: the resolved key must live under
        ``{artifact_prefix}/`` (Requirement 9.4).
        """
        prefix = self._norm_prefix(artifact_prefix)
        candidate = (key_or_relpath or "").strip().lstrip("/")
        if ".." in candidate.split("/"):
            raise ArtifactAccessError("Path traversal is not allowed in artifact keys.")
        full = candidate if candidate.startswith(prefix + "/") else f"{prefix}/{candidate}"
        if not full.startswith(prefix + "/"):
            raise ArtifactAccessError(
                f"Requested key escapes the project's artifact prefix '{prefix}/'."
            )
        return full

    def get_artifact(self, artifact_prefix: str, key_or_relpath: str) -> dict:
        """Return ``{key, content}`` for a text artifact under the project prefix."""
        full_key = self.resolve_key(artifact_prefix, key_or_relpath)
        resp = self._s3().get_object(Bucket=self._bucket, Key=full_key)
        body = resp["Body"].read()
        try:
            content = body.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            return {"key": full_key, "content": None,
                    "note": "binary or non-UTF-8 artifact; not rendered as text"}
        return {"key": full_key, "content": content}
