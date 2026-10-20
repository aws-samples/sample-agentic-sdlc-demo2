import pytest
from aidlc_mcp.s3_artifacts import S3ArtifactReader, ArtifactAccessError
from conftest import FakeS3


def reader(objs):
    return S3ArtifactReader("bucket", "us-east-1", s3_client=FakeS3(objs))


def test_list_scoped_to_prefix():
    r = reader({"proj/aidlc-docs/a.md": "A", "proj/aidlc-docs/b.md": "B", "other/x.md": "X"})
    keys = [i["key"] for i in r.list_artifacts("proj")]
    assert keys == ["proj/aidlc-docs/a.md", "proj/aidlc-docs/b.md"]


def test_get_by_relative_path_joins_prefix_and_keeps_aidlc_docs():
    r = reader({"proj/aidlc-docs/requirements.md": "hello"})
    out = r.get_artifact("proj", "aidlc-docs/requirements.md")
    assert out["key"] == "proj/aidlc-docs/requirements.md"
    assert out["content"] == "hello"


def test_get_by_full_key():
    r = reader({"proj/aidlc-docs/x.md": "y"})
    assert r.get_artifact("proj", "proj/aidlc-docs/x.md")["content"] == "y"


def test_path_traversal_blocked():
    r = reader({"proj/aidlc-docs/x.md": "y"})
    with pytest.raises(ArtifactAccessError):
        r.resolve_key("proj", "../secret/other.md")


def test_foreign_key_is_safely_nested_under_prefix():
    # A path that looks like another project's key is nested under this
    # project's prefix (cannot escape it) rather than reaching foreign data.
    r = reader({})
    resolved = r.resolve_key("proj", "otherproj/aidlc-docs/x.md")
    assert resolved == "proj/otherproj/aidlc-docs/x.md"
    assert resolved.startswith("proj/")
