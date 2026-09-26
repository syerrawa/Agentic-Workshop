"""Evaluate the triage agent over the labelled tickets (Epic 3, Story 3.1).

Runs `agent.triage_with_escalation` on every row of `eval/labelled_tickets.csv`
through `mlflow.genai.evaluate`, approving every escalation automatically so the
run never waits for a person, and scores each ticket with four local code scorers:
`valid_schema`, `category_match`, `priority_match` and `tool_order`. It logs one
run to the `triage-agent` experiment in `sqlite:///mlflow.db`.

Usage: uv run python eval/run_eval.py
"""

import asyncio
import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# `python eval/run_eval.py` puts eval/ on sys.path, not the repo root, so agent and schema would not import.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mlflow  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from mlflow.genai.scorers import scorer  # noqa: E402
from pydantic import ValidationError  # noqa: E402

import agent  # noqa: E402
from schema import TriageDecision  # noqa: E402

LABELS_PATH = ROOT / "eval" / "labelled_tickets.csv"
APP_DB = ROOT / "app.db"  # what mcp/triage_server.py reads; main checks it exists
TRACKING_URI = "sqlite:///mlflow.db"
EXPERIMENT = "triage-agent"
DEFAULT_MAX_WORKERS = "4"


def load_dataset(path: Path = LABELS_PATH) -> list[dict]:
    """One `mlflow.genai.evaluate` row per labelled ticket: the ticket ID as input, the labels as expectations."""
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        return [
            {
                "inputs": {"ticket_id": row["ticket_id"]},
                "expectations": {
                    "expected_category": row["expected_category"],
                    "expected_priority": row["expected_priority"],
                    "expected_tools": row["expected_tools"],
                    "judge_notes": row["judge_notes"],
                },
            }
            for row in csv.DictReader(f)
        ]


def auto_approve(request: dict) -> bool:
    """The eval's approver: every escalation is approved, so the run never reads stdin."""
    return True


@mlflow.trace(name="predict", span_type="AGENT")
def predict(ticket_id: str) -> dict:
    """Triage one ticket in a single trace (the escalation resume included), tagged `escalated=true|false`.

    A failing agent run raises (tagged `escalated=false`); MLflow records the error on that row and the eval continues.
    """
    escalated = False
    try:
        decision, escalated = asyncio.run(agent.triage_with_escalation(ticket_id, approve=auto_approve))
    finally:
        mlflow.update_current_trace(tags={"escalated": "true" if escalated is True else "false"})
    return decision


def _field(outputs, name: str):
    """`outputs[name]`, or None when the prediction failed or returned something other than a dict."""
    return outputs.get(name) if isinstance(outputs, dict) else None


@scorer
def valid_schema(outputs) -> int:
    """1 when the output validates as a TriageDecision, else 0."""
    if not isinstance(outputs, dict):
        return 0
    try:
        TriageDecision.model_validate(outputs)
    except ValidationError:
        return 0
    return 1


@scorer
def category_match(outputs, expectations) -> int:
    """1 when the output's category equals `expected_category`, else 0."""
    category = _field(outputs, "category")
    return int(category is not None and category == (expectations or {}).get("expected_category"))


@scorer
def priority_match(outputs, expectations) -> int:
    """1 when the output's priority equals `expected_priority`, else 0."""
    priority = _field(outputs, "priority")
    return int(priority is not None and priority == (expectations or {}).get("expected_priority"))


def _earliest_start(trace, name: str) -> int | None:
    starts = [s.start_time_ns for s in trace.search_spans(name=name) if s.start_time_ns is not None]
    return min(starts) if starts else None


@scorer
def tool_order(trace, outputs) -> int:
    """1 when the trace's earliest get_ticket span starts before its earliest get_customer_history span, else 0.

    A failed prediction (no dict output) scores 0 even if both lookups ran before it raised.
    """
    if trace is None or not isinstance(outputs, dict):
        return 0
    ticket_start = _earliest_start(trace, "get_ticket")
    history_start = _earliest_start(trace, "get_customer_history")
    return int(ticket_start is not None and history_start is not None and ticket_start < history_start)


SCORERS = [valid_schema, category_match, priority_match, tool_order]


def main():
    """Run the eval once and print the MLflow run ID. Returns the evaluation result."""
    load_dotenv()
    # Without these every row would raise and the run would look like a real all-zero score.
    if not (os.environ.get("OPENROUTER_API_KEY") or "").strip():
        raise SystemExit("OPENROUTER_API_KEY is not set. Add it to .env or the environment.")
    if not APP_DB.exists():
        raise SystemExit(f"{APP_DB.name} not found. Load the data first: uv run python load_seed.py")
    mlflow.set_tracking_uri(TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT)
    mlflow.langchain.autolog()
    # A modest worker cap keeps the MCP subprocesses and OpenRouter concurrency low; a user setting wins.
    os.environ.setdefault("MLFLOW_GENAI_EVAL_MAX_WORKERS", DEFAULT_MAX_WORKERS)
    # predict is already traced, so skip MLflow's trace check, which would triage the first ticket an extra time.
    os.environ.setdefault("MLFLOW_GENAI_EVAL_SKIP_TRACE_VALIDATION", "true")

    result = mlflow.genai.evaluate(data=load_dataset(), scorers=SCORERS, predict_fn=predict)
    print(f"MLflow run ID: {result.run_id}")
    return result


if __name__ == "__main__":
    main()
