"""Session state store (design Section 2e).

A small local, git-ignored store the MCP server maintains so the Enforcement
Hooks can consult preconditions (the review-before-approve gate). One JSON file
per project:

    ${AIDLC_STATE_DIR}/projects/<project_uuid>.json
    { "handoff_state", "owning_role", "artifacts_reviewed_at", "last_seen_at" }

It contains no secrets or tokens (Requirement 16.7 / 14.4). When no state dir is
configured a default under the OS temp dir is used, keyed so different workspaces
don't collide.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

_SAFE = re.compile(r"[^A-Za-z0-9_-]")


class StateStore:
    def __init__(self, state_dir: Optional[str] = None, now_fn: Callable[[], float] = time.time):
        base = state_dir or os.path.join(tempfile.gettempdir(), "aidlc-mcp-state")
        self._root = Path(base) / "projects"
        self._now = now_fn

    def _path(self, project_uuid: str) -> Path:
        safe = _SAFE.sub("_", project_uuid or "unknown")
        return self._root / f"{safe}.json"

    def read(self, project_uuid: str) -> dict:
        try:
            return json.loads(self._path(project_uuid).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return {}

    def _write(self, project_uuid: str, data: dict) -> None:
        path = self._path(project_uuid)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)

    def _now_iso(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._now()))

    def record_seen(self, project_uuid: str, handoff_state: str, owning_role: str) -> None:
        """Update the observed state/owner on a project/list read."""
        data = self.read(project_uuid)
        prev_state = data.get("handoff_state")
        data["handoff_state"] = handoff_state
        data["owning_role"] = owning_role
        data["last_seen_at"] = self._now_iso()
        # A state change invalidates a prior review marker so the gate re-fires
        # after the project moves on.
        if prev_state is not None and prev_state != handoff_state:
            data.pop("artifacts_reviewed_at", None)
        self._write(project_uuid, data)

    def record_review(self, project_uuid: str) -> None:
        """Mark that the persona reviewed artifacts for this project."""
        data = self.read(project_uuid)
        data["artifacts_reviewed_at"] = self._now_iso()
        data["last_seen_at"] = self._now_iso()
        self._write(project_uuid, data)
