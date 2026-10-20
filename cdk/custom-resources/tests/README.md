# Custom-resource Lambda tests

Unit tests for the Python Lambda handlers under `cdk/custom-resources/`:

- **Project API** (`project_api.py`, spec task 2.7) —
  `test_project_api.py`. Drives the handler with synthetic API Gateway proxy
  events, asserting on HTTP status codes and the bytes actually persisted to an
  in-memory fake S3 (`fake_s3.FakeS3Client`). The fake raises the real
  `botocore.exceptions.ClientError`, so the handler's 404 / 409
  (PreconditionFailed) branches run exactly as in production.
- **Post-confirmation role-assign trigger**
  (`post_confirmation_role_assign.py`, spec task 4.5) —
  `test_post_confirmation_role_assign.py`. Drives the handler with synthetic
  Cognito post-confirmation events. The lazy `_cognito_idp()` client factory is
  monkeypatched to a tiny fake that records `admin_add_user_to_group` calls, so
  the test asserts on the exact call arguments (Req 1.2) and the real stdout
  rejection log (Req 1.3). No AWS calls are made.

## Layout

- `test_project_api.py` — Project API tests (pytest).
- `test_post_confirmation_role_assign.py` — post-confirmation trigger tests (pytest).
- `fake_s3.py` — in-memory S3 fake (get/put with `IfMatch`, list paginator,
  delete, presigned URL) plus a one-shot `on_next_get` hook to simulate a
  concurrent writer for the ETag-mismatch (409) case.

## Running

`boto3`/`botocore` and `pytest` are required (the handlers import `boto3` and
catch `botocore.exceptions.ClientError`, so `botocore` must be importable).
Using [`uv`](https://docs.astral.sh/uv/):

```bash
cd cdk/custom-resources
uv venv .venv-project-api --python 3.13
uv pip install --python .venv-project-api boto3 pytest
.venv-project-api/bin/python -m pytest tests/ -v
```

Or with a standard venv:

```bash
cd cdk/custom-resources
python3 -m venv .venv && source .venv/bin/activate
pip install boto3 pytest
pytest tests/ -v
```
