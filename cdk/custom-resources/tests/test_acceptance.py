"""Unit tests for the acceptance validator layer (lenient v1, task 5).

Covers each individual validator, rule parsing, the extensible registry, the
unknown-validator behaviour, and the lenient-warn vs strict-block modes.

Validates: Requirements 8.1, 8.2, 8.3, 8.4
"""

import os
import sys

import pytest

_CUSTOM_RESOURCES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _CUSTOM_RESOURCES_DIR not in sys.path:
    sys.path.insert(0, _CUSTOM_RESOURCES_DIR)

import acceptance  # noqa: E402
from acceptance import (  # noqa: E402
    MODE_LENIENT,
    MODE_STRICT,
    AcceptanceResult,
    RuleResult,
    evaluate,
    get_validator,
    parse_rule,
    register_validator,
    registered_validators,
    resolve_mode,
)


# ---------------------------------------------------------------------------
# parse_rule
# ---------------------------------------------------------------------------


def test_parse_rule_no_param():
    assert parse_rule("nonempty") == ("nonempty", None)


def test_parse_rule_with_param():
    assert parse_rule("min_words:300") == ("min_words", "300")


def test_parse_rule_param_may_contain_colons_and_commas():
    # only the first colon splits; the rest is the param verbatim
    assert parse_rule("has_sections:Functional,NonFunctional") == (
        "has_sections",
        "Functional,NonFunctional",
    )
    name, param = parse_rule("weird:a:b:c")
    assert name == "weird"
    assert param == "a:b:c"


def test_parse_rule_strips_name_whitespace():
    assert parse_rule("  nonempty  ") == ("nonempty", None)


# ---------------------------------------------------------------------------
# nonempty
# ---------------------------------------------------------------------------


def test_nonempty_passes_with_content():
    v = get_validator("nonempty")
    ok, _ = v("hello", None)
    assert ok is True


@pytest.mark.parametrize("content", ["", "   ", "\n\t  \n"])
def test_nonempty_fails_when_blank(content):
    v = get_validator("nonempty")
    ok, msg = v(content, None)
    assert ok is False
    assert "empty" in msg.lower()


# ---------------------------------------------------------------------------
# has_sections
# ---------------------------------------------------------------------------


def test_has_sections_pass_all_present():
    v = get_validator("has_sections")
    content = "# Title\n## Functional\ntext\n## NonFunctional\ntext"
    ok, _ = v(content, "Functional,NonFunctional")
    assert ok is True


def test_has_sections_case_insensitive_and_substring():
    v = get_validator("has_sections")
    # heading text contains the required name -> present
    content = "## Functional Requirements\n### non functional overview"
    ok, _ = v(content, "Functional,non functional")
    assert ok is True


def test_has_sections_reports_missing():
    v = get_validator("has_sections")
    content = "# Title\n## Functional\ntext"
    ok, msg = v(content, "Functional,NonFunctional")
    assert ok is False
    assert "NonFunctional" in msg
    assert "Functional," not in msg  # only the missing one is reported


def test_has_sections_ignores_non_heading_lines():
    v = get_validator("has_sections")
    # the word appears only in body text, not as a heading -> missing
    content = "# Title\nThis mentions Functional in prose only."
    ok, msg = v(content, "Functional")
    assert ok is False


def test_has_sections_no_names_declared_fails():
    v = get_validator("has_sections")
    ok, msg = v("# anything", "")
    assert ok is False


# ---------------------------------------------------------------------------
# min_words
# ---------------------------------------------------------------------------


def test_min_words_pass_at_threshold():
    v = get_validator("min_words")
    content = " ".join(["word"] * 10)
    ok, _ = v(content, "10")
    assert ok is True


def test_min_words_fail_below_threshold():
    v = get_validator("min_words")
    ok, msg = v("only three words here", "10")
    assert ok is False
    assert "below minimum" in msg


def test_min_words_non_integer_param_fails():
    v = get_validator("min_words")
    ok, msg = v("plenty of words in here", "abc")
    assert ok is False
    assert "integer" in msg


def test_min_words_negative_param_fails():
    v = get_validator("min_words")
    ok, msg = v("words", "-5")
    assert ok is False


# ---------------------------------------------------------------------------
# valid_markdown
# ---------------------------------------------------------------------------


def test_valid_markdown_balanced_fences_pass():
    v = get_validator("valid_markdown")
    content = "# Title\n\n```python\nprint('hi')\n```\ndone"
    ok, _ = v(content, None)
    assert ok is True


def test_valid_markdown_unbalanced_fences_fail():
    v = get_validator("valid_markdown")
    content = "# Title\n\n```python\nprint('hi')\nno closing fence"
    ok, msg = v(content, None)
    assert ok is False
    assert "unbalanced" in msg


def test_valid_markdown_empty_fails():
    v = get_validator("valid_markdown")
    ok, _ = v("   ", None)
    assert ok is False


# ---------------------------------------------------------------------------
# resolve_mode
# ---------------------------------------------------------------------------


def test_resolve_mode_default_is_lenient(monkeypatch):
    monkeypatch.delenv("ACCEPTANCE_MODE", raising=False)
    assert resolve_mode() == MODE_LENIENT


def test_resolve_mode_reads_env(monkeypatch):
    monkeypatch.setenv("ACCEPTANCE_MODE", "strict")
    assert resolve_mode() == MODE_STRICT


def test_resolve_mode_env_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("ACCEPTANCE_MODE", "  STRICT ")
    assert resolve_mode() == MODE_STRICT


def test_resolve_mode_unknown_env_falls_back_to_lenient(monkeypatch):
    monkeypatch.setenv("ACCEPTANCE_MODE", "banana")
    assert resolve_mode() == MODE_LENIENT


def test_resolve_mode_explicit_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("ACCEPTANCE_MODE", "strict")
    assert resolve_mode("lenient") == MODE_LENIENT


# ---------------------------------------------------------------------------
# registry extensibility
# ---------------------------------------------------------------------------


def test_registry_lists_builtins():
    names = registered_validators()
    for expected in ("nonempty", "has_sections", "min_words", "valid_markdown"):
        assert expected in names


def test_register_custom_validator_is_used_by_evaluate():
    register_validator("always_fail", lambda content, param: (False, "nope"))
    try:
        res = evaluate("anything", ["always_fail"], mode=MODE_LENIENT)
        assert res.passed is False
        assert res.warnings[0]["rule"] == "always_fail"
    finally:
        # keep the shared registry clean for other tests
        acceptance._REGISTRY.pop("always_fail", None)


def test_register_validator_rejects_bad_input():
    with pytest.raises(ValueError):
        register_validator("", lambda c, p: (True, ""))
    with pytest.raises(ValueError):
        register_validator("x", "not callable")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# evaluate — lenient vs strict
# ---------------------------------------------------------------------------


def test_evaluate_all_pass():
    content = "# Requirements\n## Functional\n" + " ".join(["word"] * 20)
    res = evaluate(
        content,
        ["nonempty", "has_sections:Functional", "min_words:5", "valid_markdown"],
        mode=MODE_LENIENT,
    )
    assert res.passed is True
    assert res.warnings == []
    assert res.blocked is False


def test_evaluate_empty_rules_passes():
    res = evaluate("anything", [], mode=MODE_LENIENT)
    assert res.passed is True
    assert res.blocked is False


def test_evaluate_none_rules_passes():
    res = evaluate("anything", None, mode=MODE_STRICT)
    assert res.passed is True
    assert res.blocked is False


def test_lenient_failures_warn_but_do_not_block():
    # too few words + missing section -> two failures
    res = evaluate("short text", ["min_words:100", "has_sections:Design"], mode=MODE_LENIENT)
    assert res.passed is False
    assert res.blocked is False  # lenient never blocks
    assert len(res.warnings) == 2
    rules = {w["rule"] for w in res.warnings}
    assert rules == {"min_words:100", "has_sections:Design"}


def test_strict_failures_block():
    res = evaluate("short text", ["min_words:100"], mode=MODE_STRICT)
    assert res.passed is False
    assert res.blocked is True
    assert len(res.warnings) == 1


def test_strict_all_pass_does_not_block():
    res = evaluate(" ".join(["word"] * 50), ["min_words:10"], mode=MODE_STRICT)
    assert res.passed is True
    assert res.blocked is False


# ---------------------------------------------------------------------------
# evaluate — unknown validator
# ---------------------------------------------------------------------------


def test_unknown_validator_warns_in_lenient():
    res = evaluate("content", ["does_not_exist:42"], mode=MODE_LENIENT)
    assert res.passed is False
    assert res.blocked is False
    assert res.results[0].unknown is True
    assert "unknown" in res.warnings[0]["message"].lower()


def test_unknown_validator_blocks_in_strict():
    res = evaluate("content", ["does_not_exist"], mode=MODE_STRICT)
    assert res.passed is False
    assert res.blocked is True
    assert res.results[0].unknown is True


def test_evaluate_uses_env_mode_when_mode_arg_omitted(monkeypatch):
    monkeypatch.setenv("ACCEPTANCE_MODE", "strict")
    res = evaluate("short", ["min_words:1000"])
    assert res.mode == MODE_STRICT
    assert res.blocked is True


def test_validator_exception_is_captured_as_failure():
    def boom(content, param):
        raise RuntimeError("kaboom")

    register_validator("boom", boom)
    try:
        res = evaluate("x", ["boom"], mode=MODE_STRICT)
        assert res.passed is False
        assert res.blocked is True
        assert "kaboom" in res.results[0].message
    finally:
        acceptance._REGISTRY.pop("boom", None)


# ---------------------------------------------------------------------------
# result shape
# ---------------------------------------------------------------------------


def test_result_warning_shape():
    res = evaluate("", ["nonempty"], mode=MODE_LENIENT)
    assert isinstance(res, AcceptanceResult)
    warning = res.warnings[0]
    assert set(warning.keys()) == {"rule", "message"}
    assert warning["rule"] == "nonempty"


def test_rule_result_as_warning():
    rr = RuleResult(rule="min_words:5", passed=False, message="too short")
    assert rr.as_warning() == {"rule": "min_words:5", "message": "too short"}
