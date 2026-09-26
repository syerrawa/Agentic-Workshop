# Deferred Work

- source_spec: `_bmad-output/specs/spec-epic-1/stories/1-triage-decision-schema.md`
  summary: Root modules (`schema`, `agent`) are not importable from `eval/run_eval.py`, because the project is not installed and `uv run python eval/run_eval.py` puts `eval/` on `sys.path`, not the repo root.
  evidence: `pyproject.toml` has no `[build-system]`; only pytest gets the root via `pythonpath = ["."]`. Epic 3 must add the root to the path or make the project installable. It hits the same problem importing `agent`, so this is not caused by the schema.
- source_spec: `_bmad-output/specs/spec-epic-1/stories/1-triage-decision-schema.md`
  summary: The schema accepts a category and a route that disagree (e.g. billing with bug-team), although `TRIAGE_POLICY.md` maps each category to exactly one route.
  evidence: Epic 1 CAP-1 validates each field on its own set only, so Epic 3's `valid_schema` would score a mismatched pair as 1. Adding the check changes the spec, so it has to go through `/bmad-spec`.
