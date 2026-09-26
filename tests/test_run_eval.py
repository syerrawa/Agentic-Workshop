"""Offline tests for eval/run_eval.py: the dataset, the four code scorers, predict and one full eval run."""

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import mlflow
import pytest
from test_agent import SERVER_WRAPPER, T1042, ScriptedModel, decide, history_from_ticket, ticket

import agent
import load_seed

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval)

GOOD = {"category": "billing", "priority": "P2", "route": "billing-team", "rationale": "Double charge."}
BILLING_P2 = {"expected_category": "billing", "expected_priority": "P2"}
SCORER_NAMES = ["valid_schema", "category_match", "priority_match", "tool_order"]


def score(scorer_obj, **kwargs):
    """Call the @scorer-wrapped scorer directly; for these code scorers that returns the function's raw 1/0."""
    return scorer_obj(**kwargs)


# ---- load_dataset ----------------------------------------------------------------------------------


def test_load_dataset_reads_all_20_rows_with_inputs_and_expectations():
    rows = run_eval.load_dataset()
    assert len(rows) == 20
    assert rows[0] == {
        "inputs": {"ticket_id": "T-1042"},
        "expectations": {
            "expected_category": "billing",
            "expected_priority": "P2",
            "expected_tools": "get_ticket,get_customer_history",
            "judge_notes": "Double charge is a money problem (P2). Enterprise with 2 open tickets is under the bump threshold.",
        },
    }
    assert len({r["inputs"]["ticket_id"] for r in rows}) == 20
    for row in rows:
        assert set(row["inputs"]) == {"ticket_id"}
        assert set(row["expectations"]) == {"expected_category", "expected_priority", "expected_tools", "judge_notes"}


# ---- the code scorers ------------------------------------------------------------------------------


def test_a_correct_output_scores_1_on_schema_category_and_priority():
    assert score(run_eval.valid_schema, outputs=GOOD) == 1
    assert score(run_eval.category_match, outputs=GOOD, expectations=BILLING_P2) == 1
    assert score(run_eval.priority_match, outputs=GOOD, expectations=BILLING_P2) == 1


def test_a_wrong_priority_scores_0_on_priority_only():
    wrong = {**GOOD, "priority": "P3"}
    assert score(run_eval.priority_match, outputs=wrong, expectations=BILLING_P2) == 0
    assert score(run_eval.category_match, outputs=wrong, expectations=BILLING_P2) == 1
    assert score(run_eval.valid_schema, outputs=wrong) == 1


def test_a_wrong_category_scores_0():
    assert score(run_eval.category_match, outputs={**GOOD, "category": "bug"}, expectations=BILLING_P2) == 0


@pytest.mark.parametrize(
    "outputs",
    [
        {**GOOD, "extra": "field"},
        {**GOOD, "priority": "P9"},
        {k: v for k, v in GOOD.items() if k != "route"},
        {**GOOD, "rationale": "   "},
        "not a dict",
    ],
)
def test_an_invalid_output_scores_0_on_schema(outputs):
    assert score(run_eval.valid_schema, outputs=outputs) == 0


@pytest.mark.parametrize("outputs", [None, {}, "text"])
def test_a_failed_prediction_scores_0_on_every_output_scorer(outputs):
    assert score(run_eval.valid_schema, outputs=outputs) == 0
    assert score(run_eval.category_match, outputs=outputs, expectations=BILLING_P2) == 0
    assert score(run_eval.priority_match, outputs=outputs, expectations=BILLING_P2) == 0


def test_missing_expectations_score_0():
    assert score(run_eval.category_match, outputs=GOOD, expectations=None) == 0
    assert score(run_eval.priority_match, outputs=GOOD, expectations={}) == 0


class FakeTrace:
    """Just enough of an mlflow Trace for tool_order: named spans with start times."""

    def __init__(self, *spans):
        self.spans = [SimpleNamespace(name=name, start_time_ns=start) for name, start in spans]

    def search_spans(self, name=None):
        return [s for s in self.spans if s.name == name]


@pytest.mark.parametrize(
    "trace, expected",
    [
        (FakeTrace(("get_ticket", 10), ("get_customer_history", 20)), 1),
        (FakeTrace(("get_customer_history", 10), ("get_ticket", 20)), 0),
        (FakeTrace(("get_ticket", 10)), 0),
        (FakeTrace(("get_customer_history", 10)), 0),
        (FakeTrace(), 0),
        (None, 0),
        # The earliest of each span is compared.
        (FakeTrace(("get_ticket", 30), ("get_customer_history", 20), ("get_ticket", 10)), 1),
        (FakeTrace(("get_ticket", 20), ("get_customer_history", 10), ("get_customer_history", 30)), 0),
    ],
)
def test_tool_order(trace, expected):
    assert score(run_eval.tool_order, trace=trace, outputs=GOOD) == expected


@pytest.mark.parametrize("outputs", [None, "text"])
def test_tool_order_scores_0_for_a_failed_prediction_even_with_both_lookups(outputs):
    trace = FakeTrace(("get_ticket", 10), ("get_customer_history", 20))
    assert score(run_eval.tool_order, trace=trace, outputs=outputs) == 0


# ---- predict ---------------------------------------------------------------------------------------


@pytest.fixture
def tracking(monkeypatch, tmp_path):
    """A tmp sqlite tracking store (mlflow.db in tmp_path) and a passing preflight; global state is restored afterwards."""
    previous_uri = mlflow.get_tracking_uri()
    monkeypatch.chdir(tmp_path)
    for name in ("MLFLOW_GENAI_EVAL_MAX_WORKERS", "MLFLOW_GENAI_EVAL_SKIP_TRACE_VALIDATION"):
        monkeypatch.setenv(name, "x")  # records the original value, so teardown restores it
        monkeypatch.delenv(name)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    (tmp_path / "app.db").touch()
    monkeypatch.setattr(run_eval, "APP_DB", tmp_path / "app.db")
    monkeypatch.setattr(run_eval, "load_dotenv", lambda *a, **k: None)
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    mlflow.set_experiment("triage-agent")
    yield tmp_path
    mlflow.set_tracking_uri(previous_uri)


@pytest.mark.parametrize("escalated, tag", [(True, "true"), (False, "false")])
def test_predict_auto_approves_and_tags_escalated(monkeypatch, tracking, escalated, tag):
    passed = {}

    async def fake_triage_with_escalation(ticket_id, model=None, approve=None):
        passed.update(ticket_id=ticket_id, approved=approve({"ticket_id": ticket_id, "reason": "P1", "action": {}}))
        return GOOD, escalated

    monkeypatch.setattr(agent, "triage_with_escalation", fake_triage_with_escalation)
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("the eval must never read stdin"))

    assert run_eval.predict("T-1044") == GOOD
    assert passed == {"ticket_id": "T-1044", "approved": True}
    trace = mlflow.get_trace(mlflow.get_last_active_trace_id(), flush=True)
    assert trace.info.tags["escalated"] == tag
    assert run_eval.auto_approve({}) is True


def test_a_raising_prediction_is_still_tagged_not_escalated(monkeypatch, tracking):
    async def fake_triage_with_escalation(ticket_id, model=None, approve=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent, "triage_with_escalation", fake_triage_with_escalation)
    with pytest.raises(RuntimeError, match="boom"):
        run_eval.predict("T-1043")
    trace = mlflow.get_trace(mlflow.get_last_active_trace_id(), flush=True)
    assert trace.info.tags["escalated"] == "false"
    assert trace.info.state == "ERROR"


def test_predict_on_the_real_agent_graph_traces_both_tools_in_order(monkeypatch, tracking):
    db_path = tracking / "seed.db"
    load_seed.load(db_path)
    monkeypatch.setattr(agent, "SERVER_ARGS", ["-c", SERVER_WRAPPER, str(ROOT / "mcp" / "triage_server.py"), str(db_path)])
    real = agent.triage_with_escalation
    scripted = ScriptedModel(steps=[ticket(), history_from_ticket, decide(T1042)])

    async def with_scripted_model(ticket_id, model=None, approve=None):
        return await real(ticket_id, model=scripted, approve=approve)

    monkeypatch.setattr(agent, "triage_with_escalation", with_scripted_model)
    mlflow.langchain.autolog()

    decision = run_eval.predict("T-1042")

    assert decision == T1042
    trace = mlflow.get_trace(mlflow.get_last_active_trace_id(), flush=True)
    names = [s.name for s in trace.data.spans]
    assert "get_ticket" in names and "get_customer_history" in names
    assert trace.info.tags["escalated"] == "false"
    assert score(run_eval.tool_order, trace=trace, outputs=decision) == 1


@pytest.mark.parametrize("key", [None, "", "   "])
def test_main_stops_before_any_run_without_a_key(monkeypatch, tracking, key):
    if key is None:
        monkeypatch.delenv("OPENROUTER_API_KEY")
    else:
        monkeypatch.setenv("OPENROUTER_API_KEY", key)
    monkeypatch.setattr(mlflow.genai, "evaluate", lambda **k: pytest.fail("evaluate must not run"))
    with pytest.raises(SystemExit, match="OPENROUTER_API_KEY is not set"):
        run_eval.main()


def test_main_stops_before_any_run_without_app_db(monkeypatch, tracking):
    monkeypatch.setattr(run_eval, "APP_DB", tracking / "missing.db")
    monkeypatch.setattr(mlflow.genai, "evaluate", lambda **k: pytest.fail("evaluate must not run"))
    with pytest.raises(SystemExit, match=r"not found.*uv run python load_seed.py"):
        run_eval.main()


# ---- one full eval run -----------------------------------------------------------------------------


def test_main_logs_one_run_with_20_rows_and_4_scorers(monkeypatch, tracking, capsys):
    labels = {r["inputs"]["ticket_id"]: r["expectations"] for r in run_eval.load_dataset()}
    failing, escalating = "T-1043", "T-1044"
    calls = []

    async def fake_triage_with_escalation(ticket_id, model=None, approve=None):
        calls.append(ticket_id)
        with mlflow.start_span(name="get_ticket", span_type="TOOL"):
            pass
        with mlflow.start_span(name="get_customer_history", span_type="TOOL"):
            pass
        if ticket_id == failing:  # both lookups ran, then the agent failed: still 0 on all four
            raise RuntimeError("The agent failed TriageDecision validation twice")
        escalated = ticket_id == escalating and approve({"ticket_id": ticket_id, "reason": "P1", "action": {}})
        category = labels[ticket_id]["expected_category"]
        route = f"{category}-team"
        return {"category": category, "priority": labels[ticket_id]["expected_priority"], "route": route, "rationale": "ok"}, escalated

    monkeypatch.setattr(agent, "triage_with_escalation", fake_triage_with_escalation)
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("the eval must never read stdin"))

    result = run_eval.main()

    assert os.environ["MLFLOW_GENAI_EVAL_MAX_WORKERS"] == "4"
    assert sorted(calls) == sorted(labels)  # each ticket once, no extra validation call
    assert f"MLflow run ID: {result.run_id}" in capsys.readouterr().out

    experiment = mlflow.get_experiment_by_name("triage-agent")
    runs = mlflow.search_runs(experiment_ids=[experiment.experiment_id], output_format="list")
    assert [r.info.run_id for r in runs] == [result.run_id]

    df = result.result_df
    assert len(df) == 20
    for name in SCORER_NAMES:
        assert f"{name}/value" in df.columns
        assert result.metrics[f"{name}/mean"] == pytest.approx(19 / 20)

    by_ticket = {row["request"]["ticket_id"]: row for _, row in df.iterrows()}
    for name in SCORER_NAMES:
        assert by_ticket[failing][f"{name}/value"] == 0
        assert by_ticket[escalating][f"{name}/value"] == 1

    traces = mlflow.search_traces(locations=[experiment.experiment_id], run_id=result.run_id, return_type="list")
    assert len(traces) == 20
    escalated_tags = [t.info.tags.get("escalated") for t in traces]
    assert escalated_tags.count("true") == 1
    assert escalated_tags.count("false") == 19

    failed = [t for t in traces if t.data.spans[0].inputs == {"ticket_id": failing}]
    assert len(failed) == 1 and failed[0].info.state == "ERROR"
    root = failed[0].data.spans[0]
    assert "validation twice" in root.status.description
