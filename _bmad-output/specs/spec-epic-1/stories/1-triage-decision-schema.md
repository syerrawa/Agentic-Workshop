---
title: 'Story 1.1: Triage decision schema'
type: 'feature'
created: '2026-09-26'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: ['{project-root}/_bmad-output/specs/spec-epic-1/SPEC.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Epics 2 and 3 are specced against an Epic 1 triage-decision schema that does not exist yet: the agent must return decisions in it and the eval's `valid_schema` scorer checks outputs against it (CAP-1).

**Approach:** Add one importable pydantic model, `TriageDecision` in `schema.py` at the repo root, with exactly `category`, `priority`, `route` and `rationale`. Fixed value sets are `Literal` types, extra fields are forbidden, and `rationale` must be a non-empty string. Pydantic's `ValidationError` names the offending field in its `loc`. Offline pytest tests in `tests/test_schema.py` show a valid decision accepted and each kind of invalid decision (out-of-set value, missing field, extra field, empty rationale) rejected with the field named.

</frozen-after-approval>

## Implementation Notes

- Value sets: category {billing, bug, access, performance, how-to}; priority {P1, P2, P3, P4}; route {billing-team, bug-team, access-team, performance-team, how-to-team}.
- Decision (unnoticeable to the user): a whitespace-only rationale counts as empty and is rejected; the stored rationale is kept as given (no stripping of valid text). One sentence is style, not enforced.
- Decision: `Literal` fields in pydantic already refuse coercion from other types, so no extra strict mode is needed.
- Decision: add `pythonpath = ["."]` to `[tool.pytest.ini_options]` in `pyproject.toml` so tests under `tests/` can import root modules (`schema`, and later `load_seed`). No new dependencies.
- Do not touch `run_agent.py`, `mcp/`, `seed/`, `eval/` or `TRIAGE_POLICY.md`.
- Files changed: `schema.py` (new: `TriageDecision` plus the `Category`/`Priority`/`Route` literal aliases), `tests/test_schema.py` (new, 21 offline tests), `pyproject.toml` (pytest `pythonpath`).
- Epic 2's `run_agent.py` stub prints `json.dumps(decision)`, so the agent will need `TriageDecision.model_dump()` to get a dict. Nothing to change here; noted for Epic 2.
- Also written by step 1: `_bmad-output/implementation-artifacts/epic-1-context.md`.
- After review: model is `frozen=True`; `rationale` has `Field(min_length=1)` so the JSON schema the LLM sees carries `minLength` (the validator still rejects whitespace-only). Tests now 36, all passing offline (`uv run pytest`).

## Review Triage Log

- medium, defer: `schema` is importable only under pytest; `eval/run_eval.py` won't see the repo root. Real, but Epic 3 has the same problem with `agent`, and it isn't caused by this change. Logged in deferred-work.md.
- maybe-false, rejected: the `schema` name clashes with the PyPI package. That package is not installed or a dependency, and `find_spec('schema')` resolves to our file. Renaming is a naming choice, not a bug.
- medium, defer: category and route can disagree. The spec doesn't require the check; it needs `/bmad-spec`. Logged in deferred-work.md.
- low, patch: a validated decision can be mutated into an invalid one. Fixed with `frozen=True`; tested.
- low, patch: the JSON schema had no `minLength` on rationale. Fixed with `Field(min_length=1)`; tested. Field descriptions are left to Epic 2's prompt design.
- low, rejected: a zero-width-only or very long rationale is accepted. Rare in normal use, and the fix adds more than a simple correction.
- low, rejected: the tests' value lists aren't tied to `TRIAGE_POLICY.md`. Parsing the policy markdown in tests adds more complexity than it saves.
- low, patch: allowed values were looped inside one test. Now parametrized per value.
- low, patch: missing tests for several bad fields at once, invalid JSON input and the closed JSON schema. Added.
- false: the story file had no verification. Status `in-progress` was correct mid-workflow, and verification is recorded above.
- false/low, rejected: `epic-1-context.md` wording. "Regenerate with compile-epic-context" comes from the mandated template, and the other points are cosmetic.

### Review Findings

Code review (2026-09-26), `main` vs working tree: Blind Hunter, Edge Case Hunter, Verification Gap and Acceptance Auditor. There were 20 raw findings: 5 are patches, 0 need a decision, 0 are deferred and 15 were rejected.

- [x] [Review][Patch] The immutability test assigns an invalid value, so it would also pass on a mutable model with `validate_assignment=True`. Assign `"P1"` and assert the error type is `frozen_instance`. [tests/test_schema.py:150]
- [x] [Review][Patch] Nothing pins the `Literal` sets to the spec. Adding a value (e.g. `"sales"` to `Category`) passes every test. Assert that `get_args(Category/Priority/Route)` equals `ALLOWED`. [tests/test_schema.py:77]
- [x] [Review][Patch] Nothing tests that "rationale kept as given" holds. `"  Charged twice.  "` should round-trip unchanged. [tests/test_schema.py:116]
- [x] [Review][Patch] The JSON schema the LLM sees says only `minLength: 1`, but the runtime rejects whitespace-only rationales, and `""` and `"   "` give different error messages. `Field(min_length=1, pattern=r"\S")` makes the schema and runtime agree and replaces the custom validator. [schema.py:39]
- [x] [Review][Patch] `rationale` accepts `bytes` in lax mode (`b"abc"` becomes `"abc"`), which goes against the story's "no coercion" decision. Use `StrictStr`. [schema.py:39]

#### Rejected

- false: "wrong types are barely tested". `priority=2` already exercises `Literal` refusing non-string input, and `category` and `route` use the same mechanism.
- false: "accept-each-value test has no assertion". A `Literal` either raises or stores the input unchanged, so the field cannot come back different.
- false: "diff omits deferred-work.md". It exists and records both deferrals.
- false: "test helper IndexError on non-dict input". No test passes non-dict input.
- rejected (already deferred): `schema` importable only under pytest. It is in deferred-work.md; flagged by the Blind Hunter and the Acceptance Auditor.
- rejected (fix edits the spec): category and route can disagree. It is in deferred-work.md and needs `/bmad-spec`; flagged by the Blind Hunter and the Edge Case Hunter.
- rejected (fix edits the spec): no maximum length on rationale. The spec only requires it to be non-empty.
- rejected (fix edits the story): Implementation Notes say "21 offline tests" but there are 36. Line 31 is already correct.
- rejected (fix edits the story): status was `done` while the review was running. The status is updated by this workflow.
- low, rejected: a zero-width-only rationale is accepted. LLM output rarely looks like that, and the guard adds complexity. Also rejected in the earlier triage.
- low, rejected: non-dict input gives an empty `loc`. No caller exists yet; Epic 3's scorer can handle it.
- low, rejected: `model_copy(update=...)` skips validation. This is documented pydantic behaviour and nothing calls it; the missing test for it is rejected on the same grounds.
- low, rejected: the docstring doesn't say how Epics 2 and 3 import `schema`. Cosmetic, and already covered by the deferred item.
