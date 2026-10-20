"""Persona/Workflow Registry — shared single source of truth.

Loads ``workflow_registry.json`` (sitting alongside this module) once at import
time and exposes a set of pure helper functions over the registry. Every
persona-, segment-, and state-specific behavior in the platform (handoff state
machine, RBAC, workflow segmentation, session entry, status labels) derives
from this registry instead of hardcoding persona names (design Section 3a).

The same module + JSON pair is bundled into both runtimes:
  * the AgentCore Runtime image (``icode-agent/``), and
  * the Project API Lambda asset (``cdk/custom-resources/``).

Both copies must stay byte-identical; ``scripts/package-code.sh`` syncs the
JSON, and a CDK assertion test verifies the copies do not drift.

The :class:`WorkflowRegistry` class wraps a registry dict so callers (and
tests) can validate or query arbitrary registries; the module-level helpers
(``owning_role``, ``segment_of`` …) operate on the default registry loaded
from disk.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RegistryError(Exception):
    """Base class for registry problems."""


class RegistryValidationError(RegistryError):
    """Raised by :meth:`WorkflowRegistry.validate` when closure is violated."""


# ---------------------------------------------------------------------------
# Registry wrapper
# ---------------------------------------------------------------------------


class WorkflowRegistry:
    """A loaded Persona/Workflow Registry with pure lookup helpers.

    The wrapped ``data`` mirrors the structure of ``workflow_registry.json``:
    ``initial_state``, ``personas``, ``segments``, ``states`` and an optional
    ``request_changes`` block (design Section 3a).
    """

    def __init__(self, data: dict):
        if not isinstance(data, dict):
            raise RegistryError("registry data must be a dict")
        self._data = data
        self._personas: Dict[str, dict] = data.get("personas", {})
        self._segments: Dict[str, dict] = data.get("segments", {})
        self._states: Dict[str, dict] = data.get("states", {})
        self._request_changes: Dict[str, dict] = data.get("request_changes", {})
        # Kiro-native stage contracts: per-stage generator + produces /
        # consumes / acceptance / skill / advances_state. Optional and additive —
        # stages without a contract keep their existing behaviour.
        self._stage_contracts: Dict[str, dict] = data.get("stage_contracts", {})

    # -- raw accessors ------------------------------------------------------

    @property
    def data(self) -> dict:
        """Return the underlying registry dict."""
        return self._data

    def initial_state(self) -> str:
        """Return the registry's ``initial_state``."""
        return self._data["initial_state"]

    # -- state lookups ------------------------------------------------------

    def owning_role(self, state: str) -> str:
        """Return the Cognito group name that owns ``state``."""
        return self._states[state]["owning_role"]

    def segment_of(self, state: str) -> str:
        """Return the workflow segment that ``state`` belongs to."""
        return self._states[state]["segment"]

    def forward(self, state: str) -> Optional[str]:
        """Return the single forward successor of ``state`` (``None`` if terminal)."""
        return self._states[state]["forward"]

    def request_changes(self, state: str) -> Optional[dict]:
        """Return the request-changes descriptor for ``state`` (``{target,label}``) or ``None``."""
        return self._request_changes.get(state)

    def validate_transition(self, current: str, target: str) -> bool:
        """Return ``True`` only when moving ``current`` -> ``target`` is permitted.

        A transition is permitted (design Section 3b, Req 4.3) when either:
          * ``target`` is ``current``'s immediate forward successor
            (``target == forward(current)``), or
          * a request-changes reassignment is defined for ``current`` and
            ``target`` is its declared target
            (``target == request_changes(current).target``).

        Every other ``target`` is rejected (returns ``False``), which the
        Project API surfaces as a validation error leaving the record unchanged
        (Req 4.4). This is a pure function over the registry data; adding a
        persona extends the data, not this logic.
        """
        spec = self._states.get(current)
        if spec is None:
            return False

        if spec.get("forward") == target:
            return True

        rc = self._request_changes.get(current)
        if rc is not None and rc.get("target") == target:
            return True

        return False

    # -- segment lookups ----------------------------------------------------

    def segment_stages(self, seg: str) -> List[str]:
        """Return the ordered list of workflow stages owned by segment ``seg``."""
        return self._segments[seg]["stages"]

    def entry_node(self, seg: str) -> str:
        """Return the graph entry node to use when a session enters at segment ``seg``."""
        return self._segments[seg]["entry_node"]

    def exit_hub(self, seg: str) -> str:
        """Return the hub node whose outbound edge is segment ``seg``'s handoff boundary.

        The orchestrator rewires this hub's forward edge (e.g.
        ``hub_post_user_stories -> workflow_planning``) so that, in the owning
        persona's session, the hub routes into ``handoff_gate__{seg}`` instead
        of into the next segment's first stage (design Section 5b).
        """
        return self._segments[seg]["exit_hub"]

    def segment_owning_role(self, seg: str) -> str:
        """Return the Cognito group name that owns segment ``seg``."""
        return self._segments[seg]["owning_role"]

    def upstream_segments(self, seg: str) -> List[str]:
        """Return every segment whose owning persona ranks earlier than ``seg``'s.

        A segment is "upstream" when its owning role's ``persona.order`` is
        strictly lower than the order of ``seg``'s owning role. Used to build
        ``skip_completed`` so a persona never re-runs earlier personas' work
        (design Section 3a-i / 5c). Returns segment names ordered by persona
        order.
        """
        target_order = self._segment_order(seg)
        upstream = [
            name
            for name in self.ordered_segments()
            if name != seg and self._segment_order(name) < target_order
        ]
        return upstream

    def ordered_segments(self) -> List[str]:
        """Return segment names sorted by their owning role's ``persona.order``."""
        return sorted(self._segments, key=self._segment_order)

    def successor_segment(self, seg: str) -> Optional[str]:
        """Return the segment immediately following ``seg`` in persona order.

        Returns ``None`` when ``seg`` is the last (terminal) segment. Used by
        the orchestrator to decide which segments need a handoff gate: every
        segment with a successor gets a ``handoff_gate__{seg}`` node on its
        ``exit_hub`` boundary (design Section 5b).
        """
        ordered = self.ordered_segments()
        try:
            idx = ordered.index(seg)
        except ValueError:
            return None
        if idx + 1 < len(ordered):
            return ordered[idx + 1]
        return None

    def segments(self) -> "Dict[str, List[str]]":
        """Return an ordered ``{segment: [stage names]}`` partition.

        Segments are ordered by their owning role's ``persona.order`` (same
        order as :meth:`ordered_segments`), and each value is the segment's
        ordered list of workflow stages (:meth:`segment_stages`). This is the
        registry-derived stage partition the orchestrator builds the graph from
        (design Section 5a): because it is derived, a third segment in the
        registry automatically yields a third partition with no orchestrator
        edit. Returns a fresh dict with copied stage lists so callers cannot
        mutate the loaded registry.
        """
        return {seg: list(self.segment_stages(seg)) for seg in self.ordered_segments()}

    # -- stage-contract lookups (Kiro-native) -------------------------------

    def stage_contract(self, stage: str) -> Optional[dict]:
        """Return the stage contract for ``stage`` or ``None``.

        The contract carries ``generator`` (``kiro`` | ``agentcore``),
        ``produces`` (``[{path, required, acceptance[]}]``), ``consumes``
        (``[path]``), ``skill``, and optional ``advances_state``.
        """
        return self._stage_contracts.get(stage)

    def stage_segment(self, stage: str) -> Optional[str]:
        """Return the segment whose ``stages`` list contains ``stage``, or ``None``."""
        for seg, spec in self._segments.items():
            if stage in (spec.get("stages") or []):
                return seg
        return None

    def next_stage(self, stage: str) -> Optional[str]:
        """Return the stage that follows ``stage`` within its segment, or ``None``.

        Resolves ``stage``'s segment, then returns the next entry in that
        segment's ordered ``stages`` list. Returns ``None`` when ``stage`` is
        the segment-terminal stage (no next stage within the segment) or is not
        listed in any segment. The cross-persona handoff out of a completed
        segment is a separate, explicit action (design Section 3.11) — this
        helper only sequences the intra-segment cursor.
        """
        seg = self.stage_segment(stage)
        if seg is None:
            return None
        stages = self.segment_stages(seg)
        try:
            idx = stages.index(stage)
        except ValueError:
            return None
        if idx + 1 < len(stages):
            return stages[idx + 1]
        return None

    def stage_advances_state(self, stage: str) -> Optional[str]:
        """Return the coarse ``handoff_state`` a stage advances to on completion.

        Reads the optional ``advances_state`` from ``stage``'s contract (design
        Section 3.11): when a stage completes, ``complete_stage`` also advances
        the coarse ``handoff_state`` within the same owner via
        ``validate_transition``. Returns ``None`` when the stage has no contract
        or only moves the cursor (no ``advances_state`` declared).
        """
        contract = self.stage_contract(stage)
        if not contract:
            return None
        return contract.get("advances_state")

    # -- persona lookups ----------------------------------------------------

    def display_label(self, role: str) -> str:
        """Return the human-readable display label for a Cognito group ``role``."""
        return self._personas[role]["display"]

    # -- internals ----------------------------------------------------------

    def _segment_order(self, seg: str) -> int:
        """Return the persona order of ``seg``'s owning role (used for sorting)."""
        owning = self._segments[seg]["owning_role"]
        return self._personas[owning]["order"]

    # -- validation ---------------------------------------------------------

    def validate(self) -> None:
        """Enforce registry closure; raise :class:`RegistryValidationError` on violation.

        Closure rules (design Section 3a / task 1.2):
          * every ``owning_role`` (on states and segments) exists in ``personas``;
          * every state's ``segment`` exists in ``segments``;
          * every ``forward`` target and ``request_changes`` source/target is a
            defined state (``forward`` may be ``None`` for a terminal state);
          * following ``forward`` from ``initial_state`` reaches a terminal
            state without revisiting any state (no cycle).
        """
        # initial_state must be defined and a known state
        initial = self._data.get("initial_state")
        if not initial:
            raise RegistryValidationError("registry is missing 'initial_state'")
        if initial not in self._states:
            raise RegistryValidationError(
                f"initial_state '{initial}' is not a defined state"
            )

        # every state references a defined persona (owning_role) and segment
        for state, spec in self._states.items():
            role = spec.get("owning_role")
            if role not in self._personas:
                raise RegistryValidationError(
                    f"state '{state}' owning_role '{role}' is not a defined persona"
                )
            seg = spec.get("segment")
            if seg not in self._segments:
                raise RegistryValidationError(
                    f"state '{state}' segment '{seg}' is not a defined segment"
                )
            fwd = spec.get("forward")
            if fwd is not None and fwd not in self._states:
                raise RegistryValidationError(
                    f"state '{state}' forward target '{fwd}' is not a defined state"
                )

        # every segment references a defined persona (owning_role)
        for seg, spec in self._segments.items():
            role = spec.get("owning_role")
            if role not in self._personas:
                raise RegistryValidationError(
                    f"segment '{seg}' owning_role '{role}' is not a defined persona"
                )

        # request_changes sources and targets must be defined states
        for source, spec in self._request_changes.items():
            if source not in self._states:
                raise RegistryValidationError(
                    f"request_changes source '{source}' is not a defined state"
                )
            target = spec.get("target")
            if target not in self._states:
                raise RegistryValidationError(
                    f"request_changes target '{target}' (from '{source}') "
                    "is not a defined state"
                )

        # stage_contracts (Kiro-native): each contract must reference a
        # real stage; generator must be known; produces/consumes/acceptance must
        # be well-formed; a kiro stage must name a skill; and any advances_state
        # must be a defined state owned by the stage's segment role.
        _valid_generators = {"kiro", "agentcore"}
        for stage, contract in self._stage_contracts.items():
            if not isinstance(contract, dict):
                raise RegistryValidationError(
                    f"stage_contract '{stage}' must be an object"
                )
            seg = self.stage_segment(stage)
            if seg is None:
                raise RegistryValidationError(
                    f"stage_contract '{stage}' is not listed in any segment's stages"
                )
            gen = contract.get("generator", "kiro")
            if gen not in _valid_generators:
                raise RegistryValidationError(
                    f"stage_contract '{stage}' generator '{gen}' is invalid "
                    f"(expected one of {sorted(_valid_generators)})"
                )
            produces = contract.get("produces", [])
            if not isinstance(produces, list):
                raise RegistryValidationError(
                    f"stage_contract '{stage}' produces must be a list"
                )
            for i, art in enumerate(produces):
                if (not isinstance(art, dict)
                        or not isinstance(art.get("path"), str)
                        or not art.get("path")):
                    raise RegistryValidationError(
                        f"stage_contract '{stage}' produces[{i}] must have a "
                        "non-empty string 'path'"
                    )
                acc = art.get("acceptance", [])
                if not isinstance(acc, list) or not all(isinstance(r, str) for r in acc):
                    raise RegistryValidationError(
                        f"stage_contract '{stage}' produces[{i}] acceptance must "
                        "be a list of strings"
                    )
            consumes = contract.get("consumes", [])
            if not isinstance(consumes, list) or not all(isinstance(c, str) for c in consumes):
                raise RegistryValidationError(
                    f"stage_contract '{stage}' consumes must be a list of strings"
                )
            skill = contract.get("skill")
            if gen == "kiro" and (not isinstance(skill, str) or not skill):
                raise RegistryValidationError(
                    f"stage_contract '{stage}' (generator=kiro) must declare a "
                    "non-empty 'skill'"
                )
            adv = contract.get("advances_state")
            if adv is not None:
                if adv not in self._states:
                    raise RegistryValidationError(
                        f"stage_contract '{stage}' advances_state '{adv}' is not "
                        "a defined state"
                    )
                seg_role = self._segments[seg].get("owning_role")
                if self._states[adv].get("owning_role") != seg_role:
                    raise RegistryValidationError(
                        f"stage_contract '{stage}' advances_state '{adv}' is not "
                        f"owned by the stage's segment role '{seg_role}'"
                    )

        # following forward from initial_state must terminate without a cycle
        seen = set()
        cursor = initial
        while cursor is not None:
            if cursor in seen:
                raise RegistryValidationError(
                    f"cycle detected following forward edges at state '{cursor}'"
                )
            seen.add(cursor)
            cursor = self._states[cursor]["forward"]


# ---------------------------------------------------------------------------
# Default registry — loaded once at import from the JSON beside this module
# ---------------------------------------------------------------------------


def _registry_path() -> Path:
    """Return the path to ``workflow_registry.json`` next to this module."""
    return Path(__file__).parent / "workflow_registry.json"


def _load_registry() -> WorkflowRegistry:
    """Read and parse the registry JSON sitting beside this module."""
    path = _registry_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise RegistryError(f"workflow_registry.json not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RegistryError(f"workflow_registry.json is not valid JSON: {exc}") from exc
    return WorkflowRegistry(data)


# The single default registry instance shared by the module-level helpers.
REGISTRY: WorkflowRegistry = _load_registry()


# ---------------------------------------------------------------------------
# Module-level pure helpers (bound to the default registry)
# ---------------------------------------------------------------------------


def initial_state() -> str:
    """Return the default registry's ``initial_state``."""
    return REGISTRY.initial_state()


def owning_role(state: str) -> str:
    """Return the Cognito group name that owns ``state``."""
    return REGISTRY.owning_role(state)


def segment_of(state: str) -> str:
    """Return the workflow segment that ``state`` belongs to."""
    return REGISTRY.segment_of(state)


def forward(state: str) -> Optional[str]:
    """Return the single forward successor of ``state`` (``None`` if terminal)."""
    return REGISTRY.forward(state)


def request_changes(state: str) -> Optional[dict]:
    """Return the request-changes descriptor for ``state`` or ``None``."""
    return REGISTRY.request_changes(state)


def validate_transition(current: str, target: str) -> bool:
    """Return ``True`` only when ``current`` -> ``target`` is a permitted transition.

    Permitted when ``target`` is ``current``'s immediate forward successor, or
    when a request-changes reassignment is defined for ``current`` and
    ``target`` is its declared target (design Section 3b, Req 4.3/4.4). Bound
    to the default registry.
    """
    return REGISTRY.validate_transition(current, target)


def segment_stages(seg: str) -> List[str]:
    """Return the ordered list of workflow stages owned by segment ``seg``."""
    return REGISTRY.segment_stages(seg)


def entry_node(seg: str) -> str:
    """Return the graph entry node for segment ``seg``."""
    return REGISTRY.entry_node(seg)


def exit_hub(seg: str) -> str:
    """Return the hub node whose outbound edge is segment ``seg``'s handoff boundary."""
    return REGISTRY.exit_hub(seg)


def segment_owning_role(seg: str) -> str:
    """Return the Cognito group name that owns segment ``seg``."""
    return REGISTRY.segment_owning_role(seg)


def successor_segment(seg: str) -> Optional[str]:
    """Return the segment immediately following ``seg`` in persona order, or ``None``.

    Bound to the default registry; ``None`` when ``seg`` is the last segment.
    """
    return REGISTRY.successor_segment(seg)


def upstream_segments(seg: str) -> List[str]:
    """Return segments whose owning persona ranks earlier than ``seg``'s."""
    return REGISTRY.upstream_segments(seg)


def ordered_segments() -> List[str]:
    """Return segment names sorted by their owning role's ``persona.order``."""
    return REGISTRY.ordered_segments()


def segments() -> "Dict[str, List[str]]":
    """Return the ordered ``{segment: [stage names]}`` partition.

    Ordered by persona order; bound to the default registry. This is the
    registry-derived partition the orchestrator consumes (design Section 5a).
    """
    return REGISTRY.segments()


def display_label(role: str) -> str:
    """Return the human-readable display label for a Cognito group ``role``."""
    return REGISTRY.display_label(role)


def stage_contract(stage: str):
    """Return the stage contract for ``stage`` or ``None`` (default registry)."""
    return REGISTRY.stage_contract(stage)


def stage_segment(stage: str):
    """Return the segment containing ``stage`` or ``None`` (default registry)."""
    return REGISTRY.stage_segment(stage)


def next_stage(stage: str):
    """Return the stage following ``stage`` within its segment, or ``None``.

    Bound to the default registry; ``None`` when ``stage`` is the
    segment-terminal stage (design Section 3.11).
    """
    return REGISTRY.next_stage(stage)


def stage_advances_state(stage: str):
    """Return the coarse ``handoff_state`` ``stage`` advances to, or ``None``.

    Bound to the default registry (design Section 3.11).
    """
    return REGISTRY.stage_advances_state(stage)


def validate_registry(registry=None) -> None:
    """Validate registry closure; raise on violation (design Section 3a).

    Args:
        registry: a :class:`WorkflowRegistry`, a raw registry ``dict``, or
            ``None`` to validate the default registry loaded from disk.
    """
    if registry is None:
        target = REGISTRY
    elif isinstance(registry, WorkflowRegistry):
        target = registry
    elif isinstance(registry, dict):
        target = WorkflowRegistry(registry)
    else:
        raise RegistryError(
            "validate_registry expects a WorkflowRegistry, dict, or None"
        )
    target.validate()
