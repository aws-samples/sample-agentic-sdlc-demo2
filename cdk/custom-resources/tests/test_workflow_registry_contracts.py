"""Unit tests for Kiro-native stage contracts (kiro-native-platform task 1).

Covers the new ``stage_contracts`` schema and the extended ``validate()``
closure: the on-disk default registry validates and exposes contracts, and
malformed contracts raise ``RegistryValidationError``.

Validates: Requirements 1.4, 4.1, 4.5, 6.1
"""

import os
import sys

import pytest

_CUSTOM_RESOURCES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _CUSTOM_RESOURCES_DIR not in sys.path:
    sys.path.insert(0, _CUSTOM_RESOURCES_DIR)

import workflow_registry as reg  # noqa: E402
from workflow_registry import WorkflowRegistry, RegistryValidationError  # noqa: E402


def _base() -> dict:
    """A minimal valid registry (with one stage contract) to mutate per case."""
    return {
        "initial_state": "draft",
        "personas": {
            "ba": {"display": "BA", "order": 1},
            "sa": {"display": "SA", "order": 2},
        },
        "segments": {
            "ba_segment": {"owning_role": "ba", "stages": ["reqs", "stories"],
                           "entry_node": "reqs", "exit_hub": "h1"},
            "sa_segment": {"owning_role": "sa", "stages": ["design"],
                           "entry_node": "design", "exit_hub": "h2"},
        },
        "states": {
            "draft": {"owning_role": "ba", "segment": "ba_segment", "forward": "requirements_ready"},
            "requirements_ready": {"owning_role": "ba", "segment": "ba_segment", "forward": "assigned_to_sa"},
            "assigned_to_sa": {"owning_role": "sa", "segment": "sa_segment", "forward": None},
        },
        "request_changes": {},
        "stage_contracts": {
            "stories": {
                "generator": "kiro",
                "skill": "user-stories",
                "advances_state": "requirements_ready",
                "consumes": ["a/reqs.md"],
                "produces": [{"path": "a/stories.md", "required": True, "acceptance": ["nonempty"]}],
            },
        },
    }


def test_default_registry_validates_and_exposes_contracts():
    reg.validate_registry()  # the real on-disk registry must be valid
    c = reg.stage_contract("requirements_analysis")
    assert c is not None and c["generator"] == "kiro"
    assert c["produces"][0]["path"].endswith("requirements.md")
    assert reg.stage_contract("user_stories")["advances_state"] == "requirements_ready"
    assert reg.stage_segment("user_stories") == "ba_segment"
    assert reg.stage_contract("no_such_stage") is None


def test_base_fixture_is_valid():
    WorkflowRegistry(_base()).validate()


def test_contract_stage_must_exist_in_a_segment():
    d = _base()
    d["stage_contracts"]["ghost"] = {"generator": "kiro", "skill": "x", "produces": []}
    with pytest.raises(RegistryValidationError):
        WorkflowRegistry(d).validate()


def test_invalid_generator_rejected():
    d = _base()
    d["stage_contracts"]["stories"]["generator"] = "magic"
    with pytest.raises(RegistryValidationError):
        WorkflowRegistry(d).validate()


def test_produces_requires_path():
    d = _base()
    d["stage_contracts"]["stories"]["produces"] = [{"required": True}]
    with pytest.raises(RegistryValidationError):
        WorkflowRegistry(d).validate()


def test_kiro_stage_requires_skill():
    d = _base()
    del d["stage_contracts"]["stories"]["skill"]
    with pytest.raises(RegistryValidationError):
        WorkflowRegistry(d).validate()


def test_advances_state_must_be_defined_state():
    d = _base()
    d["stage_contracts"]["stories"]["advances_state"] = "nope"
    with pytest.raises(RegistryValidationError):
        WorkflowRegistry(d).validate()


def test_advances_state_must_be_owned_by_segment_role():
    d = _base()
    # assigned_to_sa is owned by 'sa'; 'stories' lives in ba_segment (owned by 'ba').
    d["stage_contracts"]["stories"]["advances_state"] = "assigned_to_sa"
    with pytest.raises(RegistryValidationError):
        WorkflowRegistry(d).validate()


def test_acceptance_must_be_list_of_strings():
    d = _base()
    d["stage_contracts"]["stories"]["produces"][0]["acceptance"] = [123]
    with pytest.raises(RegistryValidationError):
        WorkflowRegistry(d).validate()
