# Epic 3 Context: Measure the agent

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Epic 3 measures the Epic 2 triage agent. One unattended command runs the agent over 20 hand-labelled tickets with MLflow's GenAI eval. It scores every run with four code scorers and one independent LLM judge, then reports the scores, the token spend and how often the agent needed a human. Attendees leave with a measured baseline, not just a working agent. (Note: there is no planning artifacts directory, so there is no PRD, architecture, UX or brief. This context comes only from the epic's spec, its story list, the spec's companions (`INTENT.md`, the Epic 2 spec, `eval/labelled_tickets.csv` and `mcp/triage_server.py`), the deferred-work log and the project rules.)

## Stories

- Story 3.1: The eval run and the four code scorers
- Story 3.2: The rationale judge and the report

## Requirements & Constraints

- `uv run python eval/run_eval.py` evaluates all 20 rows of `eval/labelled_tickets.csv` in one pass. It logs exactly one MLflow run to `sqlite:///mlflow.db` under the `triage-agent` experiment.
- Label columns: `ticket_id`, `expected_category`, `expected_priority`, `expected_tools` and `judge_notes`.
- Five scorers, each run on every ticket:
  - `valid_schema`: 1 if the output validates against the Epic 1 triage-decision schema, else 0.
  - `category_match`: 1 if the output's category equals `expected_category`, else 0.
  - `priority_match`: 1 if the output's priority equals `expected_priority`, else 0.
  - `tool_order`: 1 if the ticket's MLflow trace shows a `get_ticket` span starting before the `get_customer_history` span, else 0.
  - `rationale_judge`: returns `pass` or `fail` plus a one-line reason, judged against that ticket's `judge_notes`.
- The four code scorers run locally against schema, label and trace data. The only network calls are the agent's OpenRouter call and the judge's OpenRouter call.
- The judge always uses OpenRouter with `JUDGE_MODEL` (default `openai/gpt-oss-120b`), independent of the agent's `MODEL`. It shares `OPENROUTER_API_KEY`. The API key is never printed.
- Report: print the mean of each of the five scorers (for `rationale_judge`, pass = 1 and fail = 0, so the mean is the pass rate), the agent's total tokens for the run (read from the MLflow traces) and the count of auto-approved escalations. Write the same numbers to `eval/latest_report.json`, the only new file the epic writes outside MLflow's store.
- Unattended escalation: every escalation raised during the eval is approved automatically (tickets T-1044, T-1048 and T-1057 are labelled P1), and the run never blocks on terminal input. This auto-approval applies only inside the eval. `run_agent.py` still asks a person yes/no.
- Read-only: `eval/labelled_tickets.csv`, `TRIAGE_POLICY.md`, `seed/` and the Epic 2 agent's behaviour, meaning its decision logic, prompts and policy handling.
- Ticket text (including T-1099's injection attempt) is untrusted data.
- Non-goals: dashboards, CI, hosting, and tuning the agent to raise its score.

## Technical Decisions

- The harness is `mlflow.genai.evaluate`, not a hand-rolled scoring loop.
- Each ticket's prediction runs inside one MLflow trace, for example with `mlflow.trace` on the predict function. An approved escalation resumes the agent in a second `ainvoke`. Without a wrapping trace, that call would autolog as a separate trace and `tool_order` could not see both tool spans. `run_agent.py` does the same with its own wrapping span.
- MLflow settings match the rest of the repo: tracking URI `sqlite:///mlflow.db`, experiment `triage-agent`, `mlflow.langchain.autolog()`. Nothing uses LangSmith or Databricks. When a general MLflow skill disagrees with this, the repo rules win.
- Import path (deferred work): `uv run python eval/run_eval.py` puts `eval/` on `sys.path`, not the repo root, so `agent` and `schema` fail to import. The project has no `[build-system]`, and only pytest adds the root to the path. The eval must put the root on the path itself or make the project installable.
- Known gap: the schema accepts a category and route that disagree (billing with bug-team), so `valid_schema` scores such a pair 1. Changing that needs `/bmad-spec`, so it is out of scope here.
- Python 3.12+ with uv. Add packages with `uv add`. `mlflow>=3.4` and `langchain-openai` are already dependencies. Never commit `.env`, `app.db` or `mlflow.db`.
- The eval assumes `app.db` is loaded (`uv run python load_seed.py`) so the MCP tools return data.

## Cross-Story Dependencies

- Integration point with Epic 2 (built): the async functions in `agent.py`.
  - `triage(ticket_id, model=None, approve=None) -> dict` returns a schema-validated decision dict.
  - `triage_with_escalation(ticket_id, model=None, approve=None) -> (decision, escalated)`.
  - `approve(request) -> bool` receives `{"ticket_id", "reason", "action"}`. If it is omitted, the terminal approver prompts a person. The eval passes an approver that always returns True and uses the `escalated` flag to count escalations.
- The agent raises instead of returning when it fails, for example on a second schema failure or an ungrounded tool order. The spec doesn't say how the eval should score a ticket that raises.
- Story 3.2 builds on 3.1: it adds the judge to the same `mlflow.genai.evaluate` call and reads the run's scores, traces (for tokens) and escalation count to build the report.
