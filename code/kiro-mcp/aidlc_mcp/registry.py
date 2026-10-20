"""Read-only workflow-registry mirror for the MCP server (design Property 6).

Mirrors ``workflow_registry.json`` (the single source of truth) the same way the
frontend's ``workflowRegistry.ts`` does. It is used only for **presentational**
derivations on the Kiro side — ownership grouping for ``list_projects`` and the
``latest_change_request`` feedback for ``get_project``. It never decides
transition legality; the Project API + shared registry remain authoritative.

The registry is loaded from (in order): ``AIDLC_WORKFLOW_REGISTRY`` (an explicit
path, useful in-repo to point at the canonical copy and avoid drift), then the
bundled copy shipped alongside this module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

_BUNDLED = Path(__file__).with_name("workflow_registry.json")


class WorkflowRegistry:
    def __init__(self, data: dict):
        self._data = data
        self._states = data.get("states", {})
        self._request_changes = data.get("request_changes", {})
        self._personas = data.get("personas", {})

    # -- lookups ----------------------------------------------------------
    def initial_state(self) -> str:
        return self._data.get("initial_state", "draft")

    def owning_role(self, state: str) -> Optional[str]:
        spec = self._states.get(state)
        return spec.get("owning_role") if spec else None

    def forward(self, state: str) -> Optional[str]:
        spec = self._states.get(state)
        return spec.get("forward") if spec else None

    def request_changes(self, state: str) -> Optional[dict]:
        return self._request_changes.get(state)

    def is_request_changes(self, from_state: str, to_state: str) -> bool:
        rc = self._request_changes.get(from_state)
        return bool(rc and rc.get("target") == to_state)

    def display_label(self, role: Optional[str]) -> Optional[str]:
        if role is None:
            return None
        persona = self._personas.get(role)
        return persona.get("display") if persona else role

    # -- presentational derivations --------------------------------------
    def ownership(self, owning_role: str, caller_groups: List[str], current_state: str) -> str:
        """Classify a project relative to the caller (design 2f / Data Models)."""
        if owning_role in caller_groups:
            return "mine"
        nxt = self.forward(current_state)
        next_role = self.owning_role(nxt) if nxt else None
        if next_role and next_role in caller_groups:
            return "awaiting_me"
        if nxt:
            return "awaiting_other"
        return "owned_by_other"

    def latest_change_request(self, assignment_history: List[dict]) -> Optional[dict]:
        """Return the most recent request-changes bounce, if any (Req 4.8).

        ``{requester_role, requester_display, requester_actor, note, timestamp,
        from, to}`` — the requester is the owner of the ``from`` state (the
        reviewer who sent it back).
        """
        for entry in reversed(assignment_history or []):
            frm, to = entry.get("from"), entry.get("to")
            if frm and to and self.is_request_changes(frm, to):
                requester_role = self.owning_role(frm)
                return {
                    "requester_role": requester_role,
                    "requester_display": self.display_label(requester_role),
                    "requester_actor": entry.get("actor"),
                    "note": entry.get("note"),
                    "timestamp": entry.get("timestamp"),
                    "from": frm,
                    "to": to,
                }
        return None


def load_registry(path: Optional[str] = None) -> WorkflowRegistry:
    src = path or os.environ.get("AIDLC_WORKFLOW_REGISTRY") or str(_BUNDLED)
    with open(src, encoding="utf-8") as f:
        return WorkflowRegistry(json.load(f))
