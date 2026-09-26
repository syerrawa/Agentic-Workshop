---
title: 'Story 3.1: The eval run and the four code scorers'
type: 'feature'
created: '2026-09-26'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '87be5113a6abafd4695ee7752c54435742b8eb6f'
context: ['{project-root}/_bmad-output/specs/spec-epic-3/SPEC.md', '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing measures the Epic 2 agent. There is no `eval/run_eval.py`, so there are no per-ticket scores for schema validity, category, priority or tool order, and no way to run all the labelled tickets without a person answering escalation prompts (Epic 3 CAP-1 to CAP-5, and CAP-8).

**Approach:** Add `eval/run_eval.py`. It reads all 20 rows of `eval/labelled_tickets.csv` into an `mlflow.genai.evaluate` dataset. It drives `agent.triage_with_escalation` through a traced predict function whose approver always approves. It scores each ticket with four `@scorer` functions (`valid_schema`, `category_match`, `priority_match`, `tool_order`) and logs exactly one run to the `triage-agent` experiment in `sqlite:///mlflow.db`.

## Boundaries & Constraints

**Always:**
- Use `mlflow.genai.evaluate`, with tracking URI `sqlite:///mlflow.db`, experiment `triage-agent`, and `mlflow.langchain.autolog()`.
- Each ticket's prediction is one trace: the predict function is wrapped with `mlflow.trace`.
- The approver passed to the agent always returns True, so the run never reads stdin.
- Each scorer scores 1 or 0:
  - `valid_schema`: `TriageDecision.model_validate` passes.
  - `category_match`: the output category equals `expected_category`.
  - `priority_match`: the output priority equals `expected_priority`.
  - `tool_order`: a `get_ticket` span starts before a `get_customer_history` span in the ticket's trace.
- The four scorers run locally, with no network.
- Tests run offline.

**Never:**
- Modify `eval/labelled_tickets.csv`, `TRIAGE_POLICY.md`, `agent.py`, `schema.py`, `mcp/`, `seed/` or `run_agent.py`.
- Change the agent's decision logic, prompts or escalation rule.
- Add `rationale_judge`, the printed means, token totals, the escalation count or `eval/latest_report.json` (Story 3.2).
- Use LangSmith or Databricks.

**Decisions:**
- A ticket whose agent run raises still counts as one of the 20: all four scorers give it 0, and the eval run continues.
- Each prediction's trace is tagged `escalated=true|false`, so Story 3.2 can count auto-approved escalations.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Full run | 20 CSV rows, a key, `app.db` loaded | One MLflow run in `triage-agent`, with 20 rows each scored by the 4 scorers | N/A |
| Escalating ticket | `T-1044` resolves to P1 + Enterprise | Auto-approved with no stdin read, and the trace is tagged `escalated=true` | N/A |
| Correct output | `{"category":"billing","priority":"P2",…}` against billing/P2 | `valid_schema`, `category_match` and `priority_match` are all 1 | N/A |
| Wrong priority | Output P3 against expected P2 | `priority_match` 0, `category_match` 1 | N/A |
| Agent raises | `triage_with_escalation` raises for one ticket | That row scores 0 on all four, and the run completes | The error is recorded by MLflow, not raised |
| Tool order | A trace with `get_customer_history` before `get_ticket`, or one missing | `tool_order` 0 | N/A |

</frozen-after-approval>

## Code Map

- `agent.py` -- `async triage_with_escalation(ticket_id, model=None, approve=None) -> (decision, escalated)`. `approve(request) -> bool` gets `{"ticket_id","reason","action"}`. `triage` wraps it. Call it and never modify it.
- `schema.py` -- `TriageDecision`, for `valid_schema`.
- `eval/labelled_tickets.csv` -- The columns are `ticket_id, expected_category, expected_priority, expected_tools, judge_notes`, with 20 rows. Read it with `csv.DictReader` (utf-8-sig, like `load_seed.py`). `expected_tools` and `judge_notes` go into expectations untouched (for Story 3.2).
- `run_agent.py` -- The MLflow setup to mirror: `load_dotenv()`, `set_tracking_uri("sqlite:///mlflow.db")`, `set_experiment("triage-agent")`, `mlflow.langchain.autolog()`.
- `mlflow.genai.evaluate(data, scorers, predict_fn)` -- Data rows look like `{"inputs": {"ticket_id": …}, "expectations": {…}}`. `predict_fn(ticket_id)` is called with the inputs as kwargs, in a thread pool (`MLFLOW_GENAI_EVAL_MAX_WORKERS`, default 10). It reuses or starts one run.
- `mlflow.genai.scorers.scorer` -- A `@scorer` function can take `outputs`, `expectations` and `trace`. `trace.search_spans(name=…)` returns spans with `start_time_ns`. Autolog names the tool spans `get_ticket` and `get_customer_history` (verified in a Story 2.1 trace).
- Deferred work -- `uv run python eval/run_eval.py` puts `eval/` on `sys.path`, not the repo root. `run_eval.py` must insert the repo root before importing `agent` and `schema`.

## Tasks & Acceptance

**Execution:**
- [x] `eval/run_eval.py` -- Create these pieces:
  - A repo-root `sys.path` insert.
  - `load_dataset(path) -> list[dict]`.
  - `auto_approve(request) -> True`.
  - An `@mlflow.trace`d `predict(ticket_id) -> dict`. It runs `asyncio.run(triage_with_escalation(ticket_id, approve=auto_approve))`, tags the current trace `escalated`, and returns the decision.
  - The four `@scorer`s, returning int 1/0. They are None-safe for a failed prediction: missing or `None` outputs score 0.
  - A `main()` that does the MLflow setup, sets `MLFLOW_GENAI_EVAL_MAX_WORKERS` to 4 when unset, calls `mlflow.genai.evaluate` once with all four scorers, and prints the run ID.
- [x] `tests/test_run_eval.py` -- Offline tests:
  - `load_dataset` returns 20 rows with the right inputs and expectations.
  - Each scorer handles the matrix rows, using a fake or constructed trace for `tool_order`.
  - `predict` passes an approver that returns True, and tags `escalated` (with `triage_with_escalation` monkeypatched).
  - An end-to-end `main`-style call against a tmp sqlite tracking URI, with a fake `triage_with_escalation` (one row raising), logs exactly one run with all four scorers and 20 rows.

**Acceptance Criteria:**
- Given `app.db` is loaded and `OPENROUTER_API_KEY` is set, when `uv run python eval/run_eval.py` runs, then it finishes with no terminal input and the `triage-agent` experiment gains exactly one new run holding 20 evaluated rows with `valid_schema`, `category_match`, `priority_match` and `tool_order`.
- Given that run, when its traces are inspected, then escalating tickets (e.g. T-1044, T-1048, T-1057) carry `escalated=true`, and each trace contains both tool spans.
- Given no network, when `uv run pytest` runs, then all tests pass.

## Implementation Notes

- MLflow 3.16.1 catches a raising `predict_fn` per row (`error_message`), then still runs the scorers with `outputs=None`, so `predict` does not catch.
- `main()` also sets `MLFLOW_GENAI_EVAL_SKIP_TRACE_VALIDATION=true` when unset. Without it, MLflow calls `predict` once more on the first row (tracing off) to check it is traced, which would triage T-1042 an extra time. `predict` is already `@mlflow.trace`d, so the check is not needed.
- Live run on 2026-09-26 (`openrouter/free`): run `cabd9d640a424e64955ba0c108722d7f`, 20 traces, all four means 1.0, T-1044/T-1048/T-1057 tagged `escalated=true` with both tool spans and `escalate_to_human`.
- Review patches (triage rows 1 to 7) applied, and `uv run pytest` gives 146 passed offline.
- Live re-run after the patches, run `a743e789fc0641199b69b1692ccd9a56`:
  - Unattended, 20 traces.
  - T-1044, T-1048 and T-1057 tagged `escalated=true`, the other 17 `false`.
  - Means: valid_schema 1.0, tool_order 1.0, category_match 0.9, priority_match 0.9.

## Spec Change Log

## Review Triage Log

| # | Source | Finding | Verdict | Evidence / route |
|---|--------|---------|---------|------------------|
| 1 | edge (+claim) | `tool_order` scores 1 for a prediction that raised after both lookups | medium | It reads only the trace, so a grounding or validation failure after both tools gives 1, which contradicts the frozen decision "all four scorers give it 0". patch: `tool_order` also takes `outputs` and scores 0 unless it is a dict |
| 2 | edge, blind (+claim) | A failed prediction's trace has no `escalated` tag | medium | The tag is set only after `asyncio.run` returns, and the e2e test even expects 18 false. The frozen decision says each prediction's trace is tagged. patch: tag `false` in a `finally` when not set to true |
| 3 | blind, edge | A missing key or `app.db` logs a real-looking all-zero run | medium | Every row raises inside the agent and evaluate continues, so a regression-looking run is created. patch: `main()` exits with a clear message before evaluate when `OPENROUTER_API_KEY` is empty or `app.db` is missing |
| 4 | verif-gap | `tool_order` is never tested against real autolog spans | medium | Pre-verified: the tests use a FakeTrace and fake spans, so deleting `autolog()` passes the suite. patch: a test with the scripted model and real MCP tools under autolog, asserting both spans and `tool_order == 1` |
| 5 | edge | The e2e test calls `main()`, which runs the real `load_dotenv` and a global `set_tracking_uri` without restoring them | low | Later tests inherit the `.env` key and the relative tracking URI, and the fix is a direct monkeypatch and restore. patch |
| 6 | blind | The e2e test never checks that the raised error was recorded | low | The matrix row says it is recorded by MLflow, and the fix is one assertion on the trace state or error column. patch |
| 7 | blind | The `score()` test helper's docstring is wrong and its parameter shadows `scorer` | low | A direct correction. patch |
| 8 | blind | Always exits 0 and doesn't report failed rows | low | Reporting belongs to Story 3.2, and the fix adds branches. Rejected |
| 9 | blind | The tracking URI is relative to the working directory | low | The repo convention everywhere is `sqlite:///mlflow.db` (AGENTS.md, `run_agent.py`). Rejected |
| 10 | blind | Scorers return a bare int with no rationale | low | An enhancement. Rejected |
| 11 | blind | `valid_schema` can't score 0 for a completed prediction because the agent already validates | low | True by design (the agent validates, CAP-2 as specced), and it is not a code defect. Rejected |
| 12 | blind, edge | `tool_order` ignores span status and ticket ID, and doesn't filter by span type | low | The spec defines `tool_order` as span start order. The MCP server is a subprocess, so no same-named non-tool spans exist. Rejected |
| 13 | edge | Equal `start_time_ns` scores 0 | false | The tools run sequentially (parallel calls are off), and nanosecond timestamps can't tie |
| 14 | edge | No per-ticket timeout | low | The model calls are capped at 60s with 2 retries (Story 2.1). Rejected |
| 15 | edge, blind | No CSV header or uniqueness validation | low | The file is read-only and fixed. Rejected |
| 16 | edge | A user-set `SKIP_TRACE_VALIDATION=false` is respected | false | Intended: a user setting wins |
| 17 | blind | Env precedence and the `start_time_ns is None` branch are untested | low | Unlikely regressions. Rejected |

## Design Notes

- **The worker cap of 4** keeps the MCP subprocesses and the OpenRouter concurrency modest. A user-set `MLFLOW_GENAI_EVAL_MAX_WORKERS` wins.
- **Failed predictions:** don't catch inside `predict`. Let MLflow record the prediction error on that row, and make each scorer return 0 when `outputs` is missing. If the installed MLflow turns out to abort the whole evaluation on a raise, catch in `predict` instead and return `None`, keeping the error in the trace.
- **`tool_order`** compares the earliest `get_ticket` span start with the earliest `get_customer_history` span start. It is 0 if either span is missing.

## Verification

**Commands:**
- `uv run pytest` -- expected: all tests pass offline.
- `uv run python load_seed.py && uv run python eval/run_eval.py` -- expected: it completes unattended and prints the run ID. One new run appears in `triage-agent`.

**Manual checks:**
- In the MLflow UI, the new run's evaluation shows 20 rows × 4 scorers. The T-1044 trace has both tool spans, `escalate_to_human`, and the tag `escalated=true`.
