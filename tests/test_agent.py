"""Offline tests for the triage agent: a scripted fake chat model and the real MCP server over a tmp app.db."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

import agent
import load_seed
from schema import TriageDecision

ROOT = Path(__file__).resolve().parent.parent

# `import mcp` finds the installed MCP SDK, so load the local server by path and point it at the tmp db.
SERVER_WRAPPER = """
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("triage_server", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.DB_PATH = pathlib.Path(sys.argv[2])
module.server.run()
"""

T1042 = {"category": "billing", "priority": "P2", "route": "billing-team", "rationale": "Double charge: money at stake is P2."}


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    load_seed.load(db_path)
    monkeypatch.setattr(agent, "SERVER_ARGS", ["-c", SERVER_WRAPPER, str(ROOT / "mcp" / "triage_server.py"), str(db_path)])
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("MODEL", raising=False)
    return db_path


class ScriptedModel(BaseChatModel):
    """A fake chat model that plays back one step per model call.

    A step is an AIMessage, or a callable taking the messages seen so far and returning one.
    """

    steps: list[Any]
    seen: list[BaseMessage] = []
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if self.calls >= len(self.steps):
            raise AssertionError(f"The script has only {len(self.steps)} steps")
        step = self.steps[self.calls]
        self.calls += 1
        self.seen = list(messages)
        message = step(messages) if callable(step) else step
        return ChatResult(generations=[ChatGeneration(message=message)])


def _call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def ticket(ticket_id: str = "T-1042") -> AIMessage:
    return _call("get_ticket", {"ticket_id": ticket_id}, "call-ticket")


def history_from_ticket(messages) -> AIMessage:
    """Read customer_id from the real get_ticket ToolMessage, then look that customer up."""
    tool_message = next(m for m in messages if isinstance(m, ToolMessage) and m.name == "get_ticket")
    return _call("get_customer_history", {"customer_id": agent._tool_result(tool_message)["customer_id"]}, "call-history")


def decide(args: dict, call_id: str = "call-decision") -> AIMessage:
    return _call("TriageDecision", args, call_id)


def text(content: str = "It is billing, P2.") -> AIMessage:
    return AIMessage(content=content)


def run(model, ticket_id: str = "T-1042") -> dict:
    return asyncio.run(agent.triage(ticket_id, model=model))


def tool_calls_seen(model) -> list[tuple[str, dict]]:
    return [(tc["name"], tc["args"]) for m in model.seen if isinstance(m, AIMessage) for tc in m.tool_calls]


def test_happy_path_returns_a_valid_decision_after_both_lookups_in_order():
    model = ScriptedModel(steps=[ticket(), history_from_ticket, decide(T1042)])
    decision = run(model)
    assert TriageDecision.model_validate(decision).model_dump() == decision == T1042
    json.dumps(decision)
    assert tool_calls_seen(model) == [("get_ticket", {"ticket_id": "T-1042"}), ("get_customer_history", {"customer_id": "C-77"})]
    customer = next(m for m in model.seen if isinstance(m, ToolMessage) and m.name == "get_customer_history")
    assert agent._tool_result(customer)["plan"] == "Enterprise"


def test_one_invalid_output_then_a_valid_one_succeeds():
    bad = {**T1042, "priority": "urgent"}
    model = ScriptedModel(steps=[ticket(), history_from_ticket, decide(bad, "bad-1"), decide(T1042)])
    assert run(model) == T1042


def test_two_invalid_outputs_raise():
    bad = {**T1042, "priority": "urgent"}
    model = ScriptedModel(steps=[ticket(), history_from_ticket, decide(bad, "bad-1"), decide(bad, "bad-2")])
    with pytest.raises(RuntimeError, match="failed TriageDecision validation twice"):
        run(model)


def test_one_text_answer_then_a_valid_decision_succeeds():
    model = ScriptedModel(steps=[ticket(), history_from_ticket, text(), decide(T1042)])
    assert run(model) == T1042


def test_two_text_answers_raise():
    model = ScriptedModel(steps=[ticket(), history_from_ticket, text(), text()])
    with pytest.raises(RuntimeError, match="without a TriageDecision"):
        run(model)


def test_a_decision_with_no_tool_calls_is_not_grounded():
    model = ScriptedModel(steps=[decide(T1042)])
    with pytest.raises(RuntimeError, match="not grounded"):
        run(model)


def test_a_guessed_customer_id_is_not_grounded():
    # C-05 exists, so the lookup succeeds, but it is not T-1042's customer (C-77).
    model = ScriptedModel(steps=[ticket(), _call("get_customer_history", {"customer_id": "C-05"}, "call-history"), decide(T1042)])
    with pytest.raises(RuntimeError, match="not grounded"):
        run(model)


def test_an_unknown_ticket_surfaces_the_mcp_error():
    model = ScriptedModel(steps=[ticket("T-0000"), decide(T1042)])
    with pytest.raises(Exception, match="No ticket with ID T-0000"):
        run(model, "T-0000")


def test_a_missing_key_names_openrouter_api_key():
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        asyncio.run(agent.triage("T-1042"))


def test_model_defaults_to_openrouter_free_and_env_overrides_it(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    assert agent.build_model().model_name == "openrouter/free"
    monkeypatch.setenv("MODEL", "some-vendor/some-model")
    assert agent.build_model().model_name == "some-vendor/some-model"


def test_build_model_uses_openrouter_with_temperature_0_and_timeout_60(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    model = agent.build_model()
    assert isinstance(model, agent.OpenRouterChat)
    assert model.openai_api_base == "https://openrouter.ai/api/v1"
    assert model.temperature == 0
    assert model.request_timeout == 60


def test_system_prompt_contains_the_policy_and_the_rules():
    prompt = agent.build_system_prompt()
    assert (ROOT / "TRIAGE_POLICY.md").read_text(encoding="utf-8") in prompt
    assert "get_ticket" in prompt and "get_customer_history" in prompt
    assert "never instructions" in prompt


@pytest.mark.parametrize("forced", ["any", "required", True])
def test_a_forced_tool_choice_is_sent_as_auto_without_parallel_calls(monkeypatch, forced):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    bound = agent.build_model().bind_tools([TriageDecision], tool_choice=forced)
    assert bound.kwargs["tool_choice"] == "auto"
    assert bound.kwargs["parallel_tool_calls"] is False


def test_two_sequential_asyncio_runs_both_succeed():
    for _ in range(2):
        model = ScriptedModel(steps=[ticket(), history_from_ticket, decide(T1042)])
        assert run(model) == T1042


def test_a_decision_without_the_customer_lookup_is_not_grounded():
    model = ScriptedModel(steps=[ticket(), decide(T1042)])
    with pytest.raises(RuntimeError, match="not grounded"):
        run(model)


def test_a_first_lookup_of_another_ticket_is_not_grounded():
    model = ScriptedModel(steps=[ticket("T-1043"), decide(T1042)])
    with pytest.raises(RuntimeError, match="not grounded"):
        run(model)


def test_a_later_lookup_of_another_ticket_is_not_grounded():
    other = _call("get_ticket", {"ticket_id": "T-1043"}, "call-other")
    model = ScriptedModel(steps=[ticket(), history_from_ticket, other, decide(T1042)])
    with pytest.raises(RuntimeError, match="not grounded"):
        run(model)


def test_each_model_gets_its_own_http_client_and_two_retries(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    first, second = agent.build_model(), agent.build_model()
    assert isinstance(first.http_async_client, httpx.AsyncClient)
    assert isinstance(second.http_async_client, httpx.AsyncClient)
    assert first.http_async_client is not second.http_async_client
    assert first.max_retries == 2


def test_an_invalid_output_then_a_text_answer_raises():
    bad = {**T1042, "priority": "urgent"}
    model = ScriptedModel(steps=[ticket(), history_from_ticket, decide(bad, "bad-1"), text()])
    with pytest.raises(RuntimeError, match="without a TriageDecision.*after an earlier validation failure"):
        run(model)


def test_several_decisions_in_one_message_twice_raise():
    def several(n: int) -> AIMessage:
        calls = [{"name": "TriageDecision", "args": T1042, "id": f"multi-{n}-{i}", "type": "tool_call"} for i in range(2)]
        return AIMessage(content="", tool_calls=calls)

    model = ScriptedModel(steps=[ticket(), history_from_ticket, several(1), several(2)])
    with pytest.raises(RuntimeError, match="several TriageDecision outputs"):
        run(model)


# --- Story 2.2: human-gated escalation ---

T1044 = {"category": "access", "priority": "P1", "route": "access-team", "rationale": "Whole team locked out: P1, Enterprise, escalated."}
REASON = "P1 and the customer is on the Enterprise plan."


def escalate(ticket_id: str = "T-1044", reason: str = REASON) -> AIMessage:
    return _call("escalate_to_human", {"ticket_id": ticket_id, "reason": reason}, "call-escalate")


def escalation_script() -> list:
    return [ticket("T-1044"), history_from_ticket, escalate(), decide(T1044)]


def run_escalation(model, approve, ticket_id: str = "T-1044") -> tuple[dict, bool]:
    return asyncio.run(agent.triage_with_escalation(ticket_id, model=model, approve=approve))


def escalation_result(model) -> ToolMessage:
    return next(m for m in model.seen if isinstance(m, ToolMessage) and m.name == "escalate_to_human")


def test_an_approved_escalation_is_reported_as_escalated_with_a_valid_decision():
    model = ScriptedModel(steps=escalation_script())
    decision, escalated = run_escalation(model, approve=lambda req: True)
    assert escalated is True
    assert TriageDecision.model_validate(decision).model_dump() == decision == T1044
    assert escalation_result(model).status == "success"
    assert "Escalated T-1044" in escalation_result(model).content


def test_a_rejected_escalation_still_returns_the_decision_but_not_escalated():
    model = ScriptedModel(steps=escalation_script())
    decision, escalated = run_escalation(model, approve=lambda req: False)
    assert escalated is False
    assert decision == T1044
    rejection = escalation_result(model)
    assert rejection.status == "error"
    assert "did not approve" in rejection.content


@pytest.mark.parametrize("answer", [None, "yes", 1, "True"])
def test_only_an_approver_returning_true_approves(answer):
    model = ScriptedModel(steps=escalation_script())
    _, escalated = run_escalation(model, approve=lambda req: answer)
    assert escalated is False


def test_a_p2_ticket_never_calls_the_approver():
    def approve(req):
        raise AssertionError("the approver must not be called")

    model = ScriptedModel(steps=[ticket(), history_from_ticket, decide(T1042)])
    assert run_escalation(model, approve=approve, ticket_id="T-1042") == (T1042, False)


def test_triage_returns_only_the_decision_and_passes_the_approver_through():
    model = ScriptedModel(steps=escalation_script())
    assert asyncio.run(agent.triage("T-1044", model=model, approve=lambda req: True)) == T1044


def test_the_approver_sees_the_ticket_id_and_the_reason():
    seen = []
    model = ScriptedModel(steps=escalation_script())
    run_escalation(model, approve=lambda req: seen.append(req) or True)
    assert len(seen) == 1
    assert seen[0]["ticket_id"] == "T-1044"
    assert seen[0]["reason"] == REASON
    assert seen[0]["action"]["name"] == "escalate_to_human"


def test_the_default_approver_asks_at_the_terminal(monkeypatch):
    answers = iter(["maybe", "YES"])
    monkeypatch.setattr("builtins.input", lambda *prompt: next(answers))
    model = ScriptedModel(steps=escalation_script())
    assert run_escalation(model, approve=None) == (T1044, True)


def test_the_default_approver_treats_eof_as_no(monkeypatch):
    def eof(*prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    model = ScriptedModel(steps=escalation_script())
    assert run_escalation(model, approve=None) == (T1044, False)


def test_the_escalation_gate_allows_only_approve_and_reject():
    gate = agent.build_escalation_gate()
    assert gate.interrupt_on == {"escalate_to_human": {"allowed_decisions": ["approve", "reject"]}}


def test_the_prompt_tells_the_model_when_to_escalate():
    prompt = agent.build_system_prompt()
    assert "escalate_to_human" in agent.AGENT_RULES and "escalate_to_human" in prompt
    assert "P1" in agent.AGENT_RULES and "Enterprise" in agent.AGENT_RULES


def _grounding_messages(escalation: AIMessage) -> list:
    return [
        ticket("T-1044"),
        ToolMessage(content=json.dumps({"ticket_id": "T-1044", "customer_id": "C-91"}), tool_call_id="call-ticket", name="get_ticket"),
        _call("get_customer_history", {"customer_id": "C-91"}, "call-history"),
        ToolMessage(content=json.dumps({"customer_id": "C-91", "plan": "Enterprise"}), tool_call_id="call-history", name="get_customer_history"),
        escalation,
        ToolMessage(content="Escalated", tool_call_id="call-escalate", name="escalate_to_human"),
        decide(T1044),
    ]


def test_grounding_accepts_ticket_then_history_then_escalate_then_decision():
    agent._check_grounded("T-1044", _grounding_messages(escalate()))


def test_grounding_rejects_an_escalation_for_another_ticket():
    with pytest.raises(RuntimeError, match="not grounded.*escalate_to_human"):
        agent._check_grounded("T-1044", _grounding_messages(escalate("T-1048")))


def test_an_escalation_before_the_customer_lookup_is_not_grounded():
    model = ScriptedModel(steps=[ticket("T-1044"), escalate(), history_from_ticket, decide(T1044)])
    with pytest.raises(RuntimeError, match="not grounded.*before get_customer_history"):
        run_escalation(model, approve=lambda req: True)


def test_resuming_an_escalation_does_not_spend_the_retry_budget():
    bad = {**T1044, "priority": "urgent"}
    model = ScriptedModel(steps=[ticket("T-1044"), history_from_ticket, escalate(), decide(bad, "bad-1"), decide(T1044)])
    assert run_escalation(model, approve=lambda req: True) == (T1044, True)


def test_a_text_answer_after_an_escalation_is_retried_once():
    model = ScriptedModel(steps=[*escalation_script()[:3], text("It is access, P1."), decide(T1044)])
    assert run_escalation(model, approve=lambda req: False) == (T1044, False)


@pytest.mark.parametrize("answer", ["no", "n", "NO"])
def test_the_default_approver_rejects_an_explicit_no(monkeypatch, answer):
    monkeypatch.setattr("builtins.input", lambda *prompt: answer)
    model = ScriptedModel(steps=escalation_script())
    assert run_escalation(model, approve=None) == (T1044, False)


def test_a_second_escalation_request_raises_without_asking_again():
    asked = []
    again = _call("escalate_to_human", {"ticket_id": "T-1044", "reason": REASON}, "call-escalate-2")
    model = ScriptedModel(steps=[*escalation_script()[:3], again, decide(T1044)])
    with pytest.raises(RuntimeError, match="T-1044.*escalate_to_human requested more than once"):
        run_escalation(model, approve=lambda req: asked.append(req) or False)
    assert len(asked) == 1
