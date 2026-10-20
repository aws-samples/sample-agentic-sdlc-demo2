"""Platform MCP tool implementations (design Sections 2f, 3).

``PlatformTools`` composes the Project API client, the S3 artifact reader, the
credential provider (for the caller's groups), the session state store, and the
registry mirror. Each method returns a JSON-serializable dict with a short
human-readable ``summary`` plus structured data. The MCP server wiring
(``server.py``) exposes these as MCP tools.

Transitions are thin wrappers over the authoritative Project API routes; this
layer never re-decides legality (Req 4.4) and never retries a 403/422 with a
different action (Req 4.5). ``return_for_changes`` is a plain request-changes
bounce and is never gated by the enforcement hooks.

Kiro-native generation flow: Kiro produces every deliverable locally,
so this layer exposes ``get_stage_brief`` (fetch the active stage contract),
``submit_artifact`` (owner-gated, ``produces``-constrained presigned PUT to
shared storage), and ``complete_stage`` (server-side acceptance validation +
cursor/handoff advance). There is no AgentCore Runtime WebSocket bridge.
"""

from __future__ import annotations

import os
import urllib.request
import uuid as uuid_lib
from typing import Callable, Optional

from .errors import ProjectApiError

_SUPPORTED_UPLOAD_EXTS = (".md", ".txt", ".pdf", ".docx")
_STORED_ARTIFACT_PREFIX = "aidlc-docs/"      # stable stored key (never rewritten)
_DISPLAY_ARTIFACT_PREFIX = "agentic-sdlc/"   # presentation label only
_MAX_HANDOFF_STEPS = 6


def _default_raw_put(url: str, data: bytes) -> int:
    req = urllib.request.Request(url=url, data=data, method="PUT")
    with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 - presigned S3 URL
        return resp.getcode()


def _display_key(key: Optional[str]) -> Optional[str]:
    """Presentation-only relabel of the stored ``aidlc-docs/`` prefix.

    Never used as an S3 key — the stored key is always preserved (Req 9.3).
    """
    if not key:
        return key
    return key.replace(_STORED_ARTIFACT_PREFIX, _DISPLAY_ARTIFACT_PREFIX)


class PlatformTools:
    def __init__(self, api, s3, credentials, state, registry,
                 raw_put: Callable[[str, bytes], int] = _default_raw_put,
                 uuid_factory: Callable[[], str] = lambda: str(uuid_lib.uuid4())):
        self._api = api
        self._s3 = s3
        self._creds = credentials
        self._state = state
        self._reg = registry
        self._raw_put = raw_put
        self._uuid = uuid_factory

    # -- helpers ----------------------------------------------------------
    def _enrich(self, rec: dict) -> dict:
        state = rec.get("handoff_state")
        owning = rec.get("owning_role")
        rec = dict(rec)
        rec["state_label"] = state
        rec["owning_role_display"] = self._reg.display_label(owning)
        rec["ownership"] = self._reg.ownership(owning, self._creds.get_groups(), state)
        return rec

    def _sync_state(self, rec: dict) -> None:
        self._state.record_seen(
            rec.get("project_uuid"), rec.get("handoff_state"), rec.get("owning_role")
        )

    # -- read tools (Wave 2) ---------------------------------------------
    def list_projects(self) -> dict:
        records = self._api.list_projects()
        enriched = []
        for rec in records:
            e = self._enrich(rec)
            self._sync_state(rec)
            enriched.append(e)
        mine = [r for r in enriched if r["ownership"] == "mine"]
        summary = (
            f"{len(enriched)} project(s); {len(mine)} assigned to you now."
        )
        return {"projects": enriched, "summary": summary}

    def get_project(self, project_uuid: str) -> dict:
        rec = self._api.get_project(project_uuid)
        self._sync_state(rec)
        enriched = self._enrich(rec)
        lcr = self._reg.latest_change_request(rec.get("assignment_history", []))
        stage = rec.get("current_stage")
        summary = f"'{rec.get('name')}' is at {rec.get('handoff_state')} " \
                  f"(owned by {enriched['owning_role_display']})."
        if stage:
            summary += f" Current stage: {stage}."
        if lcr:
            summary += f" Returned by {lcr['requester_display']} for changes: {lcr.get('note')}"
        return {"project": enriched, "latest_change_request": lcr, "summary": summary}

    def _artifact_prefix(self, project_uuid: str) -> str:
        rec = self._api.get_project(project_uuid)
        self._sync_state(rec)
        return rec.get("artifact_prefix") or rec.get("project_id")

    def list_artifacts(self, project_uuid: str) -> dict:
        prefix = self._artifact_prefix(project_uuid)
        items = self._s3.list_artifacts(prefix)
        for it in items:
            it["display_path"] = _display_key(it.get("key"))
        # Viewing the artifact list counts as reviewing (feeds the review gate).
        self._state.record_review(project_uuid)
        return {"artifacts": items, "summary": f"{len(items)} artifact(s) under {prefix}/."}

    def get_artifact(self, project_uuid: str, path: str) -> dict:
        prefix = self._artifact_prefix(project_uuid)
        out = self._s3.get_artifact(prefix, path)
        out["display_path"] = _display_key(out.get("key"))
        self._state.record_review(project_uuid)
        out["summary"] = f"Read {out.get('key')}."
        return out

    # -- write / transition tools (Wave 3) -------------------------------
    def create_project(self, name: str, description: str = "", type: str = "development") -> dict:
        rec = self._api.create_project(name, description, type, project_uuid=self._uuid())
        self._sync_state(rec)
        return {"project": self._enrich(rec),
                "summary": f"Created '{name}' ({rec.get('project_uuid')})."}

    def delete_project(self, project_uuid: str) -> dict:
        self._api.delete_project(project_uuid)
        return {"deleted": True, "project_uuid": project_uuid,
                "summary": f"Deleted project {project_uuid}."}

    def handoff_project(self, project_uuid: str, note: Optional[str] = None) -> dict:
        groups = self._creds.get_groups()
        rec = self._api.get_project(project_uuid)
        if rec.get("owning_role") not in groups:
            return {"handed_off": False, "project": self._enrich(rec),
                    "summary": "You don't currently own this project; nothing to hand off."}
        steps = 0
        first = True
        while rec.get("owning_role") in groups and steps < _MAX_HANDOFF_STEPS:
            if self._reg.forward(rec.get("handoff_state")) is None:
                break  # terminal; nothing further
            rec = self._api.transition(project_uuid, action="advance",
                                       note=note if first else None)
            first = False
            steps += 1
        self._sync_state(rec)
        e = self._enrich(rec)
        handed = rec.get("owning_role") not in groups
        return {"handed_off": handed, "steps": steps, "project": e,
                "summary": (f"Handed off to {e['owning_role_display']} "
                            f"(now {rec.get('handoff_state')})." if handed
                            else f"Advanced to {rec.get('handoff_state')}.")}

    def return_for_changes(self, project_uuid: str, comments: str) -> dict:
        if not (comments or "").strip():
            raise ValueError("return_for_changes requires non-empty comments for the receiving persona.")
        rec = self._api.transition(project_uuid, action="request-changes", note=comments)
        self._sync_state(rec)
        e = self._enrich(rec)
        return {"project": e,
                "summary": f"Returned for changes to {e['owning_role_display']} "
                           f"(now {rec.get('handoff_state')}) with your comments."}

    def link_repo(self, project_uuid: str, repo_url: str, branch: Optional[str] = None,
                  git_token: Optional[str] = None) -> dict:
        rec = self._api.link_repo(project_uuid, repo_url, branch=branch, git_token=git_token)
        self._sync_state(rec)
        # Never echo git_token.
        return {"project": self._enrich(rec),
                "summary": f"Linked {rec.get('repo_url')} (branch {rec.get('repo_branch')})."}

    def submit_implementation(self, project_uuid: str, note: Optional[str] = None) -> dict:
        rec = self._api.transition(project_uuid, action="submit", note=note)
        self._sync_state(rec)
        return {"project": self._enrich(rec),
                "summary": f"Submitted implementation; now {rec.get('handoff_state')}."}

    def run_tests(self, project_uuid: str) -> dict:
        rec = self._api.run_tests(project_uuid)
        self._sync_state(rec)
        return {"project": self._enrich(rec),
                "summary": "Test run started; results will appear via get_project "
                           "(qa_test_status/qa_test_summary)."}

    def upload_requirements(self, project_uuid: str, file_path: str) -> dict:
        filename = os.path.basename(file_path)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in _SUPPORTED_UPLOAD_EXTS:
            raise ValueError(
                f"Unsupported upload format '{ext}'. Allowed: {', '.join(_SUPPORTED_UPLOAD_EXTS)}."
            )
        with open(file_path, "rb") as f:
            data = f.read()
        presign = self._api.upload_url(project_uuid, filename)
        status = self._raw_put(presign["upload_url"], data)
        if not (200 <= status < 300):
            raise ProjectApiError(status, f"Upload PUT failed (HTTP {status})", "UPLOAD_FAILED")
        return {"key": presign.get("key"), "filename": filename,
                "summary": f"Uploaded {filename} to {presign.get('key')}."}

    # -- Kiro-native generation flow (brief -> submit -> complete) -------------
    def get_stage_brief(self, project_uuid: str) -> dict:
        """Fetch the active stage's contract (StageBrief) so Kiro can generate the
        deliverable locally: what it ``produces`` (with required flags + acceptance
        rules), what it ``consumes``, and the guiding ``skill``. Owner-gated by the
        backend (403 if it is not your turn)."""
        brief = self._api.stage_brief(project_uuid)
        produces = brief.get("produces", []) or []
        for art in produces:
            art["display_path"] = _display_key(art.get("path"))
        for art in brief.get("consumes", []) or []:
            art["display_path"] = _display_key(art.get("path"))
        required = [a.get("path") for a in produces if a.get("required")]
        summary = (
            f"Stage '{brief.get('stage')}' ({brief.get('segment')}) — "
            f"generate with skill '{brief.get('skill')}'; "
            f"produces {len(produces)} artifact(s)"
        )
        if required:
            summary += f" ({len(required)} required)"
        summary += "."
        return {"stage_brief": brief, "summary": summary}

    def submit_artifact(self, project_uuid: str, path: str, content: str) -> dict:
        """Submit a Kiro-generated artifact for the active stage. Obtains an
        owner-gated, ``produces``-constrained presigned PUT from the backend, then
        uploads the content directly to shared storage. The backend rejects an
        off-contract ``path`` (400) or a non-owning caller (403); the stored key is
        always the stable ``aidlc-docs/`` prefix."""
        presign = self._api.stage_artifact_url(project_uuid, path)
        data = content.encode("utf-8") if isinstance(content, str) else content
        status = self._raw_put(presign["upload_url"], data)
        if not (200 <= status < 300):
            raise ProjectApiError(status, f"Artifact PUT failed (HTTP {status})", "UPLOAD_FAILED")
        key = presign.get("key")
        return {"key": key, "display_path": _display_key(key), "path": path,
                "bytes": len(data),
                "summary": f"Submitted artifact to {key} ({len(data)} bytes)."}

    def complete_stage(self, project_uuid: str, note: Optional[str] = None) -> dict:
        """Mark the active stage complete. The backend validates acceptance
        (lenient: warnings don't block; a missing required artifact does), then
        advances the stage cursor within the segment and, where the stage declares
        it, the coarse owned state. Returns the new ``current_stage`` /
        ``handoff_state`` plus any acceptance ``warnings``. Cross-persona handoff is
        a separate, explicit ``handoff_project`` action."""
        result = self._api.complete_stage(project_uuid, note=note)
        result = dict(result or {})
        status = result.get("status")
        warnings = result.get("warnings", []) or []
        cur = result.get("current_stage")
        # Keep the local session-state marker in step with the backend so the
        # enforcement hooks observe the advanced state (owner unchanged within a
        # segment; a cross-persona move happens via handoff_project).
        handoff_state = result.get("handoff_state")
        if handoff_state:
            self._state.record_seen(
                project_uuid, handoff_state, self._reg.owning_role(handoff_state)
            )

        if status == "blocked":
            missing = result.get("missing", []) or []
            summary = ("Stage not complete — required artifact(s) missing or empty: "
                       f"{', '.join(missing) or 'see missing[]'}.")
        else:
            summary = f"Stage complete; now at stage '{cur}' (state {handoff_state})."
            if warnings:
                summary += f" {len(warnings)} acceptance warning(s) recorded."
        result["summary"] = summary
        return result
