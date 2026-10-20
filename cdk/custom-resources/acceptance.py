"""Acceptance validation — lenient v1 (design Section 7).

Pure, server-side quality gate for Kiro-generated stage artifacts. The workflow
registry declares, per produced artifact, a list of ``acceptance`` rule strings
(e.g. ``"has_sections:Functional,NonFunctional"``, ``"min_words:300"``,
``"nonempty"``, ``"valid_markdown"``). This module evaluates those rules against
an artifact's content and reports pass/fail with a human-readable message.

Two modes (env ``ACCEPTANCE_MODE``, default ``lenient``):
  * **lenient** (v1 default): rule failures become *warnings* — advancement
    proceeds; the caller records the warnings in history for observability.
  * **strict**: any rule failure *blocks* advancement.

An **unknown validator** always fails: it surfaces a warning in lenient mode and
is fail-closed (blocks) under strict mode (Req 8.3).

This module is the pure validator layer only. Wiring it into ``complete-stage``
(HeadObject the required ``produces`` for the missing/empty hard block, record
warnings, advance) is handled elsewhere; the missing-required-artifact hard
block is NOT decided here — this module only evaluates the declared rules over
content that is already in hand.

Validates: Requirements 8.1, 8.2, 8.3, 8.4
"""

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

MODE_LENIENT = "lenient"
MODE_STRICT = "strict"
_DEFAULT_MODE = MODE_LENIENT

# A validator takes the artifact content and an optional parameter string
# (the part after the first ``:`` in the rule) and returns ``(passed, message)``.
Validator = Callable[[str, Optional[str]], Tuple[bool, str]]


def resolve_mode(mode: Optional[str] = None) -> str:
    """Return the effective acceptance mode.

    ``mode`` overrides the environment when given; otherwise ``ACCEPTANCE_MODE``
    is read (default ``lenient``). Any value other than ``strict`` (case- and
    whitespace-insensitive) resolves to ``lenient`` so an unknown/typo mode never
    silently starts blocking advancement.
    """
    raw = mode if mode is not None else os.environ.get("ACCEPTANCE_MODE", _DEFAULT_MODE)
    normalized = (raw or "").strip().lower()
    return MODE_STRICT if normalized == MODE_STRICT else MODE_LENIENT


# ---------------------------------------------------------------------------
# Individual validators (pure functions)
# ---------------------------------------------------------------------------


def _v_nonempty(content: str, param: Optional[str]) -> Tuple[bool, str]:
    """Pass when ``content`` has non-whitespace text."""
    if content is not None and content.strip():
        return True, "content is non-empty"
    return False, "artifact is empty (no non-whitespace content)"


def _iter_heading_texts(content: str) -> List[str]:
    """Return the text of every ATX markdown heading (lines starting with ``#``)."""
    headings: List[str] = []
    for line in (content or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            # drop leading #'s and any trailing #'s, normalise whitespace
            text = stripped.lstrip("#").strip().rstrip("#").strip()
            if text:
                headings.append(text)
    return headings


def _v_has_sections(content: str, param: Optional[str]) -> Tuple[bool, str]:
    """Pass when every named section appears as a markdown heading.

    ``param`` is a comma-separated list of required section names
    (e.g. ``Functional,NonFunctional``). Matching is case-insensitive and a
    section is considered present when a heading's text *contains* the required
    name, so ``## Functional Requirements`` satisfies ``Functional``.
    """
    required = [s.strip() for s in (param or "").split(",") if s.strip()]
    if not required:
        return False, "has_sections rule declared no section names"
    headings = [h.lower() for h in _iter_heading_texts(content)]
    missing = [name for name in required if not any(name.lower() in h for h in headings)]
    if missing:
        return False, f"missing required section(s): {', '.join(missing)}"
    return True, f"all required sections present: {', '.join(required)}"


def _v_min_words(content: str, param: Optional[str]) -> Tuple[bool, str]:
    """Pass when ``content`` has at least ``param`` whitespace-delimited words."""
    try:
        threshold = int((param or "").strip())
    except ValueError:
        return False, f"min_words parameter is not an integer: {param!r}"
    if threshold < 0:
        return False, f"min_words parameter must be non-negative: {threshold}"
    count = len((content or "").split())
    if count >= threshold:
        return True, f"word count {count} >= {threshold}"
    return False, f"word count {count} is below minimum {threshold}"


def _v_valid_markdown(content: str, param: Optional[str]) -> Tuple[bool, str]:
    """Lenient structural markdown check.

    Markdown is intentionally permissive, so this checks the one structural error
    that reliably indicates malformed content: an unterminated fenced code block
    (an odd number of ```` ``` ```` fences). Empty content also fails.
    """
    if content is None or not content.strip():
        return False, "markdown is empty"
    fences = len(re.findall(r"^\s*```", content, flags=re.MULTILINE))
    if fences % 2 != 0:
        return False, f"unbalanced fenced code blocks (found {fences} ``` markers)"
    return True, "markdown is structurally valid"


# ---------------------------------------------------------------------------
# Validator registry (extensible)
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, Validator] = {
    "nonempty": _v_nonempty,
    "has_sections": _v_has_sections,
    "min_words": _v_min_words,
    "valid_markdown": _v_valid_markdown,
}


def register_validator(name: str, fn: Validator) -> None:
    """Register (or override) a validator under ``name`` (extensibility, Req 8.3)."""
    if not name or not isinstance(name, str):
        raise ValueError("validator name must be a non-empty string")
    if not callable(fn):
        raise ValueError("validator must be callable")
    _REGISTRY[name] = fn


def get_validator(name: str) -> Optional[Validator]:
    """Return the validator registered under ``name``, or ``None`` if unknown."""
    return _REGISTRY.get(name)


def registered_validators() -> List[str]:
    """Return the sorted list of currently registered validator names."""
    return sorted(_REGISTRY)


def parse_rule(rule: str) -> Tuple[str, Optional[str]]:
    """Split a rule string into ``(name, param)`` on the first ``:``.

    ``"min_words:300"`` -> ``("min_words", "300")``; ``"nonempty"`` ->
    ``("nonempty", None)``. Leading/trailing whitespace on the name is stripped.
    """
    if ":" in rule:
        name, param = rule.split(":", 1)
        return name.strip(), param
    return rule.strip(), None


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleResult:
    """The outcome of evaluating one acceptance rule string."""

    rule: str
    passed: bool
    message: str
    unknown: bool = False

    def as_warning(self) -> dict:
        """Return the ``{rule, message}`` warning shape used in the API/history."""
        return {"rule": self.rule, "message": self.message}


@dataclass
class AcceptanceResult:
    """Aggregate outcome of evaluating a list of acceptance rules over content."""

    mode: str
    results: List[RuleResult] = field(default_factory=list)

    @property
    def failures(self) -> List[RuleResult]:
        """Every rule that did not pass."""
        return [r for r in self.results if not r.passed]

    @property
    def passed(self) -> bool:
        """True when no rule failed."""
        return not self.failures

    @property
    def warnings(self) -> List[dict]:
        """Failed rules as ``{rule, message}`` dicts (recorded in both modes)."""
        return [r.as_warning() for r in self.failures]

    @property
    def blocked(self) -> bool:
        """True only under strict mode when at least one rule failed."""
        return self.mode == MODE_STRICT and bool(self.failures)


# ---------------------------------------------------------------------------
# Top-level evaluation
# ---------------------------------------------------------------------------


def evaluate(
    content: str,
    rules: Optional[List[str]],
    mode: Optional[str] = None,
) -> AcceptanceResult:
    """Evaluate ``rules`` against ``content`` and return an :class:`AcceptanceResult`.

    Args:
        content: the artifact content already in hand (this module does not read
            storage; the missing/empty *required* artifact hard block is decided
            by the caller — design Section 7).
        rules: the stage contract's ``acceptance`` rule strings (may be empty or
            ``None``).
        mode: ``"lenient"`` | ``"strict"``; when ``None`` the ``ACCEPTANCE_MODE``
            environment variable is used (default lenient).

    Behaviour (Req 8.2/8.3):
        * Each rule is parsed into ``name`` + optional ``param`` and dispatched to
          its registered validator.
        * An **unknown validator** yields a failed :class:`RuleResult` marked
          ``unknown`` — a warning in lenient mode, fail-closed (blocking) in
          strict mode.
        * In **lenient** mode failures are surfaced as warnings and never block
          (``blocked`` is False). In **strict** mode any failure sets
          ``blocked`` True.
    """
    effective_mode = resolve_mode(mode)
    result = AcceptanceResult(mode=effective_mode)

    for rule in rules or []:
        name, param = parse_rule(rule)
        validator = get_validator(name)
        if validator is None:
            result.results.append(
                RuleResult(
                    rule=rule,
                    passed=False,
                    message=f"unknown acceptance validator: {name!r}",
                    unknown=True,
                )
            )
            continue
        try:
            ok, message = validator(content, param)
        except Exception as exc:  # a validator must never crash advancement
            result.results.append(
                RuleResult(
                    rule=rule,
                    passed=False,
                    message=f"validator {name!r} raised: {exc}",
                )
            )
            continue
        result.results.append(RuleResult(rule=rule, passed=bool(ok), message=message))

    return result
