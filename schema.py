"""The triage-decision schema every agent output must match (Epic 1, CAP-1).

Usage: TriageDecision.model_validate(data) returns a decision or raises a
pydantic ValidationError whose `loc` names the offending field.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

Category = Literal["billing", "bug", "access", "performance", "how-to"]
Priority = Literal["P1", "P2", "P3", "P4"]
Route = Literal["billing-team", "bug-team", "access-team", "performance-team", "how-to-team"]


class TriageDecision(BaseModel):
    """One triage decision: exactly these four fields, nothing else."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: Category
    priority: Priority
    route: Route
    # `\S` rejects whitespace-only text and shows up in the JSON schema the LLM sees.
    rationale: StrictStr = Field(min_length=1, pattern=r"\S")
