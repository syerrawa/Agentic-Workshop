"""Chat UI backend (web/server.py), fully offline: the triage function is a fake."""

import time

import pytest
from fastapi.testclient import TestClient

from web import server
from web.chat_agent import ChatReply

DECISION = {"category": "billing", "priority": "P3", "route": "billing-team", "rationale": "A refund question."}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "SETUP_MLFLOW", False)
    monkeypatch.setattr(server, "_mlflow_ready", False)
    monkeypatch.setattr(server, "_runs", {})
    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setattr(server, "chat_fn", unexpected_chat)
    with TestClient(server.app) as c:
        yield c


async def unexpected_chat(history, message):
    raise AssertionError(f"the chat assistant should not be called for {message!r}")


def scripted_chat(replies, calls):
    """A fake chat function that records `(history, message)` and plays back one ChatReply per call."""

    async def fake(history, message):
        calls.append((list(history), message))
        return replies[len(calls) - 1]

    return fake


def wait_for(client, run_id, statuses, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] in statuses:
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"run stayed {body['status']!r}, wanted one of {statuses}")
        time.sleep(0.01)


def use_fake(monkeypatch, fake):
    monkeypatch.setattr(server, "triage_fn", fake)


def simple_fake(calls=None):
    async def fake(ticket_id, approve=None):
        if calls is not None:
            calls.append(ticket_id)
        return dict(DECISION), False

    return fake


def escalating_fake(results):
    async def fake(ticket_id, approve=None):
        approved = approve({"ticket_id": ticket_id, "reason": "P1 for an Enterprise customer.", "action": {}})
        results.append(approved)
        return dict(DECISION, priority="P1"), approved is True

    return fake


def test_question_only_starts_no_runs(client, monkeypatch):
    calls, chats = [], []
    use_fake(monkeypatch, simple_fake(calls))
    monkeypatch.setattr(server, "chat_fn", scripted_chat([ChatReply(reply="Hooli has T-1050.", triage_ticket_ids=[])], chats))
    response = client.post("/api/chat", json={"message": "what tickets does Hooli have?"})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Hooli has T-1050."
    assert body["run_ids"] == [] and body["run_id"] is None
    assert body["session_id"]
    assert chats == [([], "what tickets does Hooli have?")]
    assert calls == []


def test_chat_with_ticket_id_starts_a_run(client, monkeypatch):
    calls = []
    use_fake(monkeypatch, simple_fake(calls))
    response = client.post("/api/chat", json={"message": "please triage T-1044 for me"})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"].startswith("Triaging T-1044")
    assert body["run_id"]
    wait_for(client, body["run_id"], {"done"})
    assert calls == ["T-1044"]


def test_lower_case_ticket_id_is_normalised(client, monkeypatch):
    calls = []
    use_fake(monkeypatch, simple_fake(calls))
    body = client.post("/api/chat", json={"message": "t-1042"}).json()
    run = wait_for(client, body["run_id"], {"done"})
    assert run["ticket_id"] == "T-1042"
    assert calls == ["T-1042"]


@pytest.mark.parametrize("message", ["", "   \n\t", "T-1042 " + "x" * 500])
def test_empty_or_oversized_message_is_422(client, monkeypatch, message):
    calls = []
    use_fake(monkeypatch, simple_fake(calls))
    response = client.post("/api/chat", json={"message": message})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert calls == []


def test_run_lifecycle_to_done(client, monkeypatch):
    use_fake(monkeypatch, simple_fake())
    run_id = client.post("/api/chat", json={"message": "T-1042"}).json()["run_id"]
    run = wait_for(client, run_id, {"done", "error"})
    assert run == {
        "run_id": run_id,
        "ticket_id": "T-1042",
        "status": "done",
        "approval": None,
        "decision": DECISION,
        "escalated": False,
        "error": None,
    }


@pytest.mark.parametrize("approve, escalated", [(True, True), (False, False)])
def test_escalation_waits_for_approval(client, monkeypatch, approve, escalated):
    results = []
    use_fake(monkeypatch, escalating_fake(results))
    run_id = client.post("/api/chat", json={"message": "T-1044"}).json()["run_id"]

    waiting = wait_for(client, run_id, {"awaiting_approval", "done", "error"})
    assert waiting["status"] == "awaiting_approval"
    assert waiting["approval"] == {"ticket_id": "T-1044", "reason": "P1 for an Enterprise customer."}
    assert waiting["decision"] is None and waiting["escalated"] is None

    response = client.post(f"/api/runs/{run_id}/approval", json={"approve": approve})
    assert response.status_code == 200
    assert response.json()["status"] in {"running", "done"}

    run = wait_for(client, run_id, {"done", "error"})
    assert run["status"] == "done"
    assert run["escalated"] is escalated
    assert run["approval"] is None
    assert results == [approve]


def test_non_boolean_approval_is_rejected(client, monkeypatch):
    results = []
    use_fake(monkeypatch, escalating_fake(results))
    run_id = client.post("/api/chat", json={"message": "T-1044"}).json()["run_id"]
    wait_for(client, run_id, {"awaiting_approval"})
    assert client.post(f"/api/runs/{run_id}/approval", json={"approve": "yes"}).status_code == 422
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "awaiting_approval"
    client.post(f"/api/runs/{run_id}/approval", json={"approve": False})
    wait_for(client, run_id, {"done"})


def test_approval_times_out_as_rejection(client, monkeypatch):
    monkeypatch.setattr(server, "APPROVAL_TIMEOUT_SECONDS", 0.05)
    results = []
    use_fake(monkeypatch, escalating_fake(results))
    run_id = client.post("/api/chat", json={"message": "T-1044"}).json()["run_id"]
    run = wait_for(client, run_id, {"done", "error"})
    assert run["status"] == "done" and run["escalated"] is False
    assert results == [False]


def test_approval_when_not_awaiting_is_409(client, monkeypatch):
    use_fake(monkeypatch, simple_fake())
    run_id = client.post("/api/chat", json={"message": "T-1042"}).json()["run_id"]
    wait_for(client, run_id, {"done"})
    response = client.post(f"/api/runs/{run_id}/approval", json={"approve": True})
    assert response.status_code == 409


def test_unknown_run_is_404(client):
    assert client.get("/api/runs/no-such-run").status_code == 404
    assert client.post("/api/runs/no-such-run/approval", json={"approve": True}).status_code == 404


def test_failing_triage_reports_a_short_error(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-value")

    async def fake(ticket_id, approve=None):
        raise RuntimeError(f"No ticket with ID {ticket_id} (key sk-or-secret-value)\n" + "trace line\n" * 50)

    use_fake(monkeypatch, fake)
    run_id = client.post("/api/chat", json={"message": "T-0000"}).json()["run_id"]
    run = wait_for(client, run_id, {"error", "done"})
    assert run["status"] == "error"
    assert run["error"].startswith("No ticket with ID T-0000")
    assert "sk-or-secret-value" not in run["error"]
    assert "\n" not in run["error"] and len(run["error"]) <= server.MAX_ERROR_CHARS
    assert run["decision"] is None and run["escalated"] is None


def test_natural_message_starts_runs_from_the_chat_reply(client, monkeypatch):
    calls, chats = [], []
    use_fake(monkeypatch, simple_fake(calls))
    reply = ChatReply(reply="Starting triage of the SSO lockout ticket.", triage_ticket_ids=["t-1044", "T-1044", "bogus"])
    monkeypatch.setattr(server, "chat_fn", scripted_chat([reply], chats))
    body = client.post("/api/chat", json={"message": "triage the SSO lockout ticket"}).json()
    assert body["reply"] == "Starting triage of the SSO lockout ticket."
    assert len(body["run_ids"]) == 1 and body["run_id"] == body["run_ids"][0]
    run = wait_for(client, body["run_id"], {"done"})
    assert run["ticket_id"] == "T-1044"
    assert calls == ["T-1044"]


def test_session_is_created_then_reused_with_history(client, monkeypatch):
    chats = []
    replies = [ChatReply(reply="Globex has T-1044.", triage_ticket_ids=[]), ChatReply(reply="It is about SSO.", triage_ticket_ids=[])]
    monkeypatch.setattr(server, "chat_fn", scripted_chat(replies, chats))
    first = client.post("/api/chat", json={"message": "what does Globex have?", "session_id": None}).json()
    session_id = first["session_id"]
    second = client.post("/api/chat", json={"message": "what is it about?", "session_id": session_id}).json()
    assert second["session_id"] == session_id
    assert chats[1] == (
        [{"role": "user", "content": "what does Globex have?"}, {"role": "assistant", "content": "Globex has T-1044."}],
        "what is it about?",
    )


def test_unknown_session_id_gets_a_new_session(client, monkeypatch):
    chats = []
    monkeypatch.setattr(server, "chat_fn", scripted_chat([ChatReply(reply="Hi.", triage_ticket_ids=[])], chats))
    body = client.post("/api/chat", json={"message": "hello", "session_id": "no-such-session"}).json()
    assert body["session_id"] and body["session_id"] != "no-such-session"
    assert chats == [([], "hello")]


def test_history_keeps_the_last_messages_only(client, monkeypatch):
    replies = [ChatReply(reply=f"answer {i}", triage_ticket_ids=[]) for i in range(15)]
    chats = []
    monkeypatch.setattr(server, "chat_fn", scripted_chat(replies, chats))
    session_id = None
    for i in range(15):
        session_id = client.post("/api/chat", json={"message": f"question {i}", "session_id": session_id}).json()["session_id"]
    history = chats[-1][0]
    assert len(history) == server.MAX_HISTORY_MESSAGES
    assert history[-1] == {"role": "assistant", "content": "answer 13"}


def test_fast_path_with_two_ids_starts_two_runs(client, monkeypatch):
    calls = []
    use_fake(monkeypatch, simple_fake(calls))
    body = client.post("/api/chat", json={"message": "triage T-1042 and t-1044, then T-1042 again"}).json()
    assert body["reply"].startswith("Triaging T-1042")
    assert "T-1044" in body["reply"]
    assert len(body["run_ids"]) == 2 and body["run_id"] == body["run_ids"][0]
    runs = [wait_for(client, run_id, {"done"}) for run_id in body["run_ids"]]
    assert [r["ticket_id"] for r in runs] == ["T-1042", "T-1044"]
    assert sorted(calls) == ["T-1042", "T-1044"]


def test_fast_path_caps_runs_at_three(client, monkeypatch):
    use_fake(monkeypatch, simple_fake())
    body = client.post("/api/chat", json={"message": "T-1 T-2 T-3 T-4 T-5"}).json()
    assert len(body["run_ids"]) == 3


def test_fast_path_is_remembered_in_the_session(client, monkeypatch):
    use_fake(monkeypatch, simple_fake())
    chats = []
    session_id = client.post("/api/chat", json={"message": "T-1044"}).json()["session_id"]
    monkeypatch.setattr(server, "chat_fn", scripted_chat([ChatReply(reply="It is Globex.", triage_ticket_ids=[])], chats))
    client.post("/api/chat", json={"message": "whose ticket is that?", "session_id": session_id})
    assert chats[0][0][0] == {"role": "user", "content": "T-1044"}


def test_chat_error_is_a_200_apology_without_the_key(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-value")
    calls = []
    use_fake(monkeypatch, simple_fake(calls))

    async def failing(history, message):
        raise RuntimeError("Upstream said no (key sk-or-secret-value)\n" + "trace line\n" * 50)

    monkeypatch.setattr(server, "chat_fn", failing)
    response = client.post("/api/chat", json={"message": "anything urgent from Northwind?"})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"].startswith("Sorry")
    assert "Upstream said no" in body["reply"]
    assert "sk-or-secret-value" not in body["reply"] and "trace line" not in body["reply"]
    assert body["run_ids"] == [] and body["run_id"] is None and body["session_id"]
    assert calls == []
