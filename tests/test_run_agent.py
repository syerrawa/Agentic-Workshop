"""Offline tests for run_agent.py's output, and the terminal approver it passes (`agent.terminal_approver`)."""

import json
import sys

import pytest

import agent
import run_agent

REQUEST = {"ticket_id": "T-1044", "reason": "P1 and Enterprise.", "action": {"name": "escalate_to_human"}}


def answer_with(monkeypatch, *answers):
    """Feed `answers` to input() in order; an exception instance is raised instead of returned. Returns the prompts."""
    queue, prompts = list(answers), []

    def fake_input(*prompt):
        prompts.append(prompt)
        answer = queue.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr("builtins.input", fake_input)
    return prompts


@pytest.mark.parametrize("answer", ["yes", "y", "YES", "Y", " Yes "])
def test_yes_and_y_approve(monkeypatch, answer):
    answer_with(monkeypatch, answer)
    assert agent.terminal_approver(REQUEST) is True


@pytest.mark.parametrize("answer", ["no", "n", "NO", "N"])
def test_no_and_n_reject(monkeypatch, answer):
    answer_with(monkeypatch, answer)
    assert agent.terminal_approver(REQUEST) is False


@pytest.mark.parametrize("error", [EOFError(), OSError("stdin unavailable")])
def test_eof_or_unavailable_stdin_rejects(monkeypatch, error):
    answer_with(monkeypatch, error)
    assert agent.terminal_approver(REQUEST) is False


def test_an_unclear_answer_asks_again_on_stderr_only(monkeypatch, capsys):
    prompts = answer_with(monkeypatch, "maybe", "", "yes")
    assert agent.terminal_approver(REQUEST) is True
    assert prompts == [()] * 3  # input() gets no prompt, so nothing reaches stdout
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("Escalate T-1044 to a human? [yes/no] ") == 3


def test_control_characters_from_the_model_are_escaped(monkeypatch, capsys):
    answer_with(monkeypatch, "no")
    agent.terminal_approver({**REQUEST, "ticket_id": "T-1044\x1b[2J", "reason": "P1\x1b[1A\rApproved"})
    shown = capsys.readouterr().err
    assert "\x1b" not in shown and "\r" not in shown
    assert "\\x1b[1A\\rApproved" in shown and "T-1044\\x1b[2J" in shown


def test_the_approver_shows_the_ticket_and_the_reason(monkeypatch, capsys):
    answer_with(monkeypatch, "no")
    agent.terminal_approver(REQUEST)
    shown = capsys.readouterr().err
    assert "T-1044" in shown and "P1 and Enterprise." in shown


@pytest.mark.parametrize("escalated, line", [(True, "Escalated to a human: yes"), (False, "Escalated to a human: no")])
def test_main_prints_the_decision_then_the_escalation_line(monkeypatch, tmp_path, capsys, escalated, line):
    decision = {"category": "access", "priority": "P1", "route": "access-team", "rationale": "Team locked out."}
    passed = {}

    async def fake_triage_with_escalation(ticket_id, model=None, approve=None):
        passed.update(ticket_id=ticket_id, approve=approve)
        return decision, escalated

    monkeypatch.chdir(tmp_path)  # mlflow.db goes to the tmp dir
    monkeypatch.setattr(agent, "triage_with_escalation", fake_triage_with_escalation)
    monkeypatch.setattr(sys, "argv", ["run_agent.py", "T-1044"])
    run_agent.main()

    out = capsys.readouterr().out
    assert out == json.dumps(decision, indent=2) + "\n" + line + "\n"
    assert passed == {"ticket_id": "T-1044", "approve": agent.terminal_approver}
