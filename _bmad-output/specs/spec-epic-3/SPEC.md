---
id: SPEC-epic-3
companions: [../../../INTENT.md, ../spec-epic-2/SPEC.md, ../../../eval/labelled_tickets.csv, ../../../mcp/triage_server.py]
sources: []
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability — consult them only if you need narrative rationale or prose color this contract intentionally omits.

# Epic 3: measure the agent

## Why

Epic 2 gives the workshop a triage agent that decides; nothing yet says how well it decides, or what it costs. Epic 3 is the vision that closes the loop: an MLflow eval that runs the agent over 20 hand-labelled tickets, scores every run four ways in code plus once by an independent LLM judge, and reports the numbers — including token spend and how often the agent needed a human — so attendees leave Saturday with a measured baseline, not just a working agent.

## Capabilities

- **CAP-1**
  - **intent:** A person can run `uv run python eval/run_eval.py` to evaluate the Epic 2 agent over every ticket in `eval/labelled_tickets.csv` in one pass.
  - **success:** The script builds inputs from all 20 rows of `eval/labelled_tickets.csv`, drives the Epic 2 agent through `mlflow.genai.evaluate`, and logs exactly one MLflow run to `sqlite:///mlflow.db` under the `triage-agent` experiment.

- **CAP-2**
  - **intent:** Every ticket's triage output is checked against the Epic 1 schema.
  - **success:** `valid_schema` scores 1 when a ticket's output validates against the Epic 1 triage-decision schema, 0 otherwise, for all 20 tickets.

- **CAP-3**
  - **intent:** Every ticket's triaged category is checked against its label.
  - **success:** `category_match` scores 1 when the output's category equals `expected_category` from `eval/labelled_tickets.csv`, 0 otherwise.

- **CAP-4**
  - **intent:** Every ticket's triaged priority is checked against its label.
  - **success:** `priority_match` scores 1 when the output's priority equals `expected_priority` from `eval/labelled_tickets.csv`, 0 otherwise.

- **CAP-5**
  - **intent:** Every ticket's trace is checked for calling `get_ticket` before `get_customer_history`, matching Epic 2's CAP-3.
  - **success:** `tool_order` scores 1 when the ticket's MLflow trace shows a `get_ticket` span starting before the `get_customer_history` span, 0 otherwise.

- **CAP-6**
  - **intent:** A separately chosen model judges whether each ticket's rationale is sound, given that ticket's `judge_notes`.
  - **success:** `rationale_judge` calls OpenRouter with the model from `JUDGE_MODEL` (default `openai/gpt-oss-120b`) and the key from `OPENROUTER_API_KEY`, and returns `pass` or `fail` plus a one-line reason for each of the 20 tickets.

- **CAP-7**
  - **intent:** After the run, a person can see and reuse the eval's scores and cost without opening the MLflow UI.
  - **success:** The script prints the mean of each of the five scorers and the agent's total tokens for the run (read from the MLflow traces), and writes the same numbers to `eval/latest_report.json`.

- **CAP-8**
  - **intent:** A ticket that triggers Epic 2's human-in-the-loop escalation approval completes during the eval without a person present.
  - **success:** Every escalation raised while evaluating the 20 tickets (e.g. T-1044, T-1048, T-1057) is approved automatically, the run never blocks on terminal input, the agent's own escalation decision logic is unchanged, and the count of auto-approved escalations appears in both the printed output and `eval/latest_report.json`.

## Constraints

- The eval harness is built with `mlflow.genai.evaluate`, not a hand-rolled scoring loop.
- Read-only, unchanged: `eval/labelled_tickets.csv`, `TRIAGE_POLICY.md`, and the Epic 2 agent's behaviour — `run_eval.py` calls the existing agent as-is and never edits its decision logic, prompts, or policy handling.
- `rationale_judge` always uses `JUDGE_MODEL` on OpenRouter, independent of the agent's `MODEL`. It shares `OPENROUTER_API_KEY` with the agent.
- No network calls beyond model APIs: `valid_schema`, `category_match`, `priority_match`, and `tool_order` run locally against schema, label, and trace data; only the agent's OpenRouter call and `rationale_judge`'s OpenRouter call cross the network.
- `eval/latest_report.json` is the only new file this epic writes outside MLflow's own store.
- Unattended escalation approval applies only inside the eval run — a normal `uv run python run_agent.py` invocation still pauses for a person's yes/no, unchanged from Epic 2's CAP-5.
- Each ticket's prediction runs inside a single MLflow trace (e.g. `mlflow.trace` on the predict function). An approved escalation resumes the agent in a second call; without this, that second call would autolog as a separate trace that CAP-5's `tool_order` scorer can't see.

## Non-goals

- Dashboards, CI, and hosting.
- Tuning the agent to raise its score.

## Success signal

`uv run python eval/run_eval.py` runs start to finish with no person present, including for every escalating ticket. It produces exactly one MLflow run in the `triage-agent` experiment at `sqlite:///mlflow.db`, scored by all five scorers across all 20 labelled tickets, prints the five scorer means plus the agent's total tokens and the escalation count, and writes those same numbers to `eval/latest_report.json`.

## Assumptions

- Epic 1 and Epic 2 are specced but not yet built on this branch. This spec treats `INTENT.md` (the Epic 1 schema) and `spec-epic-2/SPEC.md` (the agent's `triage(ticket_id)` entry point and MLflow setup) as the ground truth to build against — the same relationship `spec-epic-2` had to the not-yet-built Epic 1 schema.
- The reported mean for `rationale_judge` treats `pass` as 1 and `fail` as 0 and reports the pass rate, consistent with how the four 0/1 code scorers are averaged. The brief specified "mean of each scorer" at the WHAT level without prescribing a scale for a pass/fail scorer.
- `TRIAGE_POLICY.md` is not a companion here: each ticket's `judge_notes` already encodes the policy reasoning `rationale_judge` checks against, so the judge does not need the policy text directly.
