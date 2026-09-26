from typing import get_args

import pytest
from pydantic import ValidationError

from schema import Category, Priority, Route, TriageDecision

VALID = {
    "category": "billing",
    "priority": "P2",
    "route": "billing-team",
    "rationale": "The customer was charged twice for one invoice.",
}


def rejected_fields(data: dict) -> set[str]:
    with pytest.raises(ValidationError) as excinfo:
        TriageDecision.model_validate(data)
    return {str(error["loc"][0]) for error in excinfo.value.errors()}


def test_valid_decision_is_accepted():
    decision = TriageDecision.model_validate(VALID)
    assert decision.model_dump() == VALID


ALLOWED = {
    "category": ["billing", "bug", "access", "performance", "how-to"],
    "priority": ["P1", "P2", "P3", "P4"],
    "route": ["billing-team", "bug-team", "access-team", "performance-team", "how-to-team"],
}


@pytest.mark.parametrize(
    "field, value", [(field, value) for field, values in ALLOWED.items() for value in values]
)
def test_every_allowed_value_is_accepted(field, value):
    TriageDecision.model_validate({**VALID, field: value})


def test_value_sets_are_exactly_the_spec():
    assert set(get_args(Category)) == set(ALLOWED["category"])
    assert set(get_args(Priority)) == set(ALLOWED["priority"])
    assert set(get_args(Route)) == set(ALLOWED["route"])


@pytest.mark.parametrize(
    "field, value",
    [
        ("category", "sales"),
        ("category", "Billing"),
        ("priority", "P0"),
        ("priority", "P5"),
        ("priority", 2),
        ("route", "sales-team"),
    ],
)
def test_out_of_set_value_is_rejected_by_field(field, value):
    assert rejected_fields({**VALID, field: value}) == {field}


@pytest.mark.parametrize("field", ["category", "priority", "route", "rationale"])
def test_missing_field_is_rejected_by_field(field):
    data = {key: value for key, value in VALID.items() if key != field}
    assert rejected_fields(data) == {field}


def test_extra_field_is_rejected_by_field():
    assert rejected_fields({**VALID, "confidence": 0.9}) == {"confidence"}


@pytest.mark.parametrize("rationale", ["", "   ", "\n"])
def test_empty_rationale_is_rejected_by_field(rationale):
    assert rejected_fields({**VALID, "rationale": rationale}) == {"rationale"}


def test_valid_rationale_is_kept_as_given():
    rationale = "  Charged twice.  "
    decision = TriageDecision.model_validate({**VALID, "rationale": rationale})
    assert decision.rationale == rationale


@pytest.mark.parametrize("rationale", [None, b"Charged twice.", 42])
def test_non_string_rationale_is_rejected_by_field(rationale):
    assert rejected_fields({**VALID, "rationale": rationale}) == {"rationale"}


def test_error_message_names_the_field():
    with pytest.raises(ValidationError, match="priority"):
        TriageDecision.model_validate({**VALID, "priority": "urgent"})


def test_decision_validates_from_json():
    decision = TriageDecision.model_validate_json(
        '{"category": "bug", "priority": "P4", "route": "bug-team", "rationale": "A typo on the login page."}'
    )
    assert decision.category == "bug"


def test_invalid_json_is_rejected_by_field():
    with pytest.raises(ValidationError) as excinfo:
        TriageDecision.model_validate_json(
            '{"category": "bug", "priority": "P9", "route": "bug-team", "rationale": "A typo."}'
        )
    assert {str(error["loc"][0]) for error in excinfo.value.errors()} == {"priority"}


def test_every_offending_field_is_named():
    data = {"category": "sales", "priority": "P9", "rationale": "", "confidence": 0.9}
    assert rejected_fields(data) == {"category", "priority", "route", "rationale", "confidence"}


def test_validated_decision_cannot_be_changed():
    decision = TriageDecision.model_validate(VALID)
    with pytest.raises(ValidationError) as excinfo:
        decision.priority = "P1"
    assert excinfo.value.errors()[0]["type"] == "frozen_instance"


def test_json_schema_is_closed_and_requires_all_fields():
    json_schema = TriageDecision.model_json_schema()
    assert json_schema["additionalProperties"] is False
    assert set(json_schema["required"]) == {"category", "priority", "route", "rationale"}
    assert json_schema["properties"]["rationale"]["minLength"] == 1
    assert json_schema["properties"]["rationale"]["pattern"] == r"\S"
