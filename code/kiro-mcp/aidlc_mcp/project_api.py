"""Project API client (design Section 2c).

A thin HTTP client mirroring the frontend ``projectService.ts``. It attaches the
Cognito **ID token** as ``Authorization: Bearer`` on every call (the Project API
authorizer reads ``cognito:groups`` from it), and maps non-2xx responses to
:class:`ProjectApiError` carrying the API's structured ``{error, code}``.

The client never re-decides transition legality and never retries a 403/422 with
a different action to force it through (Requirement 4.4 / 4.5) — the Project API
and shared ``workflow_registry`` remain the sole authority.

HTTP is performed through an injectable ``transport`` callable so the client is
unit-testable without network; the default uses stdlib :mod:`urllib`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Optional, Tuple
from urllib.parse import quote

from .auth import CredentialProvider
from .errors import ProjectApiError

# transport(method, url, headers, body_bytes) -> (status_code, response_bytes)
Transport = Callable[[str, str, dict, Optional[bytes]], Tuple[int, bytes]]


def _urllib_transport(method: str, url: str, headers: dict, body: Optional[bytes]) -> Tuple[int, bytes]:
    req = urllib.request.Request(url=url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 - https deployment URL
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as e:
        # Non-2xx: read the body so the caller can surface {error, code}.
        return e.code, e.read()


class ProjectApiClient:
    """HTTP client for the Project API."""

    def __init__(
        self,
        base_url: str,
        credentials: CredentialProvider,
        transport: Optional[Transport] = None,
    ):
        self._base = base_url.rstrip("/")
        self._creds = credentials
        self._transport = transport or _urllib_transport

    # -- core request -----------------------------------------------------
    def request(self, method: str, path: str, body: Optional[dict] = None) -> Optional[dict]:
        url = f"{self._base}{path}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._creds.get_id_token()}",
        }
        payload: Optional[bytes] = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode("utf-8")

        status, raw = self._transport(method, url, headers, payload)

        if status == 204 or not raw:
            if 200 <= status < 300:
                return None
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError):
            parsed = None

        if not (200 <= status < 300):
            code = message = None
            if isinstance(parsed, dict):
                message = parsed.get("error")
                code = parsed.get("code")
            raise ProjectApiError(
                status, message or f"Project API request failed (HTTP {status})", code
            )
        return parsed

    # -- endpoint wrappers ------------------------------------------------
    @staticmethod
    def _uuid(project_uuid: str) -> str:
        return quote(project_uuid, safe="")

    def list_projects(self) -> list:
        result = self.request("GET", "/projects") or {}
        return result.get("projects", []) if isinstance(result, dict) else []

    def get_project(self, project_uuid: str) -> dict:
        return self.request("GET", f"/projects/{self._uuid(project_uuid)}")

    def create_project(self, name: str, description: str = "", type_: str = "development",
                       project_uuid: Optional[str] = None) -> dict:
        body = {"name": name, "description": description, "type": type_}
        if project_uuid:
            body["project_uuid"] = project_uuid
        return self.request("POST", "/projects", body)

    def delete_project(self, project_uuid: str) -> Optional[dict]:
        return self.request("DELETE", f"/projects/{self._uuid(project_uuid)}")

    def transition(self, project_uuid: str, action: Optional[str] = None,
                   note: Optional[str] = None, target_state: Optional[str] = None) -> dict:
        body: dict = {}
        if action is not None:
            body["action"] = action
        if target_state is not None:
            body["target_state"] = target_state
        if note is not None:
            body["note"] = note
        return self.request("POST", f"/projects/{self._uuid(project_uuid)}/transition", body)

    def link_repo(self, project_uuid: str, repo_url: str, branch: Optional[str] = None,
                 git_token: Optional[str] = None) -> dict:
        body = {"repo_url": repo_url}
        if branch:
            body["branch"] = branch
        if git_token:
            body["git_token"] = git_token
        return self.request("POST", f"/projects/{self._uuid(project_uuid)}/link-repo", body)

    def run_tests(self, project_uuid: str) -> dict:
        return self.request("POST", f"/projects/{self._uuid(project_uuid)}/run-tests")

    def upload_url(self, project_uuid: str, filename: str) -> dict:
        return self.request(
            "POST", f"/projects/{self._uuid(project_uuid)}/upload-url", {"filename": filename}
        )

    # -- Kiro-native generation flow (stage brief -> submit -> complete) -------
    def stage_brief(self, project_uuid: str) -> dict:
        """Fetch the active stage's contract (StageBrief) for a project."""
        return self.request("GET", f"/projects/{self._uuid(project_uuid)}/stage-brief")

    def stage_artifact_url(self, project_uuid: str, path: str) -> dict:
        """Mint an owner-gated, ``produces``-constrained presigned PUT for a stage artifact.

        Returns ``{upload_url, key, expires_in}``; the API rejects an off-contract
        ``path`` with HTTP 400 and a non-owning caller with HTTP 403.
        """
        return self.request(
            "POST", f"/projects/{self._uuid(project_uuid)}/stage-artifact-url", {"path": path}
        )

    def complete_stage(self, project_uuid: str, note: Optional[str] = None) -> dict:
        """Validate acceptance (lenient) and advance the stage cursor / apply the handoff.

        Returns ``{status, warnings[], current_stage, handoff_state, ...}``.
        """
        body: dict = {}
        if note is not None:
            body["note"] = note
        return self.request(
            "POST", f"/projects/{self._uuid(project_uuid)}/complete-stage", body
        )
