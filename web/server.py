"""Chat UI backend for the triage agent (workshop prototype).

A small FastAPI app. A chat message with explicit ticket IDs starts a triage run
for each; any other message goes to the natural-language chat assistant
(`web.chat_agent.chat_turn`), which finds existing tickets and may name tickets to
triage. Each triage runs `agent.triage_with_escalation` in a background thread,
and an escalation waits for a Yes/No answer posted from the page.

Start: uv run uvicorn web.server:app --host 127.0.0.1 --port 8000
"""

import asyncio
import contextlib
import os
import re
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictBool

STATIC_DIR = Path(__file__).resolve().parent / "static"
TICKET_ID_RE = re.compile(r"T-\d+", re.IGNORECASE)
MAX_MESSAGE_CHARS = 500
APPROVAL_TIMEOUT_SECONDS = 600.0
MAX_ERROR_CHARS = 200
MAX_TRIAGE_IDS = 3
MAX_HISTORY_MESSAGES = 20
MAX_SESSIONS = 500
HELP_REPLY = "Ask me about a ticket (for example \"triage the SSO lockout ticket\") or send a ticket ID like T-1042."

# The triage function a run calls: `async (ticket_id, approve=...) -> (decision, escalated)`.
# None means `agent.triage_with_escalation`, imported on first use. Tests monkeypatch this.
triage_fn = None

# The chat function for natural-language messages: `async (history, message) -> ChatReply`.
# None means `web.chat_agent.chat_turn`, imported on first use. Tests monkeypatch this.
chat_fn = None

# MLflow is set up at startup unless this is false (tests turn it off, or set TRIAGE_WEB_MLFLOW=0).
SETUP_MLFLOW = os.environ.get("TRIAGE_WEB_MLFLOW", "1") != "0"
_mlflow_ready = False


def setup_mlflow() -> None:
    """Mirror run_agent.py: load .env, trace to sqlite:///mlflow.db under the triage-agent experiment."""
    global _mlflow_ready
    import mlflow
    from dotenv import load_dotenv

    load_dotenv()
    mlflow.set_tracking_uri("sqlite:///mlflow.db")
    mlflow.set_experiment("triage-agent")
    mlflow.langchain.autolog()
    _mlflow_ready = True


def _get_triage_fn():
    if triage_fn is not None:
        return triage_fn
    from agent import triage_with_escalation

    return triage_with_escalation


def _get_chat_fn():
    if chat_fn is not None:
        return chat_fn
    from web.chat_agent import chat_turn

    return chat_turn


def _short_error(exc: BaseException) -> str:
    """A one-line, truncated message for the page: no stack trace, never the API key."""
    text = str(exc).strip().splitlines()[0].strip() if str(exc).strip() else ""
    text = text or type(exc).__name__
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if key:
        text = text.replace(key, "[redacted]")
    if len(text) > MAX_ERROR_CHARS:
        text = text[: MAX_ERROR_CHARS - 1] + "…"
    return text


@dataclass
class Run:
    run_id: str
    ticket_id: str
    status: str = "running"  # running | awaiting_approval | done | error
    approval: dict | None = None
    decision: dict | None = None
    escalated: bool | None = None
    error: str | None = None
    answer: bool | None = None
    answered: threading.Event = field(default_factory=threading.Event)

    def view(self) -> dict:
        return {
            "run_id": self.run_id,
            "ticket_id": self.ticket_id,
            "status": self.status,
            "approval": dict(self.approval) if self.approval else None,
            "decision": dict(self.decision) if self.decision else None,
            "escalated": self.escalated,
            "error": self.error,
        }


_runs: dict[str, Run] = {}
_lock = threading.Lock()

# Chat history per session: session_id -> [{"role": "user"|"assistant", "content": str}], in memory only.
_sessions: dict[str, list[dict]] = {}


def _make_approver(run: Run):
    """An approver that pauses the run until the page posts an answer (or the timeout rejects)."""

    def approve(request: dict) -> bool:
        with _lock:
            run.answer = None
            run.answered.clear()
            run.approval = {"ticket_id": str(request.get("ticket_id") or run.ticket_id), "reason": str(request.get("reason") or "")}
            run.status = "awaiting_approval"
        run.answered.wait(timeout=APPROVAL_TIMEOUT_SECONDS)
        with _lock:
            # Only an explicit True that was actually posted approves; a timeout rejects.
            approved = run.answered.is_set() and run.answer is True
            run.approval = None
            run.status = "running"
        return approved

    return approve


def _execute(run: Run) -> None:
    """Worker thread body: one triage in its own event loop, wrapped in one MLflow span."""
    try:
        fn = _get_triage_fn()
        if _mlflow_ready:
            import mlflow

            span_cm = mlflow.start_span(name="triage", span_type="AGENT")
        else:
            span_cm = contextlib.nullcontext()
        with span_cm as span:
            if span is not None:
                span.set_inputs({"ticket_id": run.ticket_id})
            decision, escalated = asyncio.run(fn(run.ticket_id, approve=_make_approver(run)))
            if span is not None:
                span.set_outputs(decision)
        with _lock:
            run.decision = dict(decision)
            run.escalated = bool(escalated)
            run.approval = None
            run.status = "done"
    except BaseException as exc:  # noqa: BLE001 - every failure becomes a short message on the page
        with _lock:
            run.error = _short_error(exc)
            run.approval = None
            run.status = "error"


def start_run(ticket_id: str) -> Run:
    run = Run(run_id=str(uuid.uuid4()), ticket_id=ticket_id)
    with _lock:
        _runs[run.run_id] = run
    threading.Thread(target=_execute, args=(run,), name=f"triage-{run.run_id}", daemon=True).start()
    return run


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if SETUP_MLFLOW:
        setup_mlflow()
    yield


app = FastAPI(title="Triage chat", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ApprovalRequest(BaseModel):
    approve: StrictBool  # "yes", 1 or "true" are rejected; only a JSON true approves


@app.get("/")
def index() -> FileResponse:
    page = STATIC_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="The chat page is missing.")
    return FileResponse(page)


def _session(session_id: str | None) -> tuple[str, list[dict]]:
    """The session's id and a copy of its history; a missing or unknown id gets a new session."""
    with _lock:
        if session_id and session_id in _sessions:
            return session_id, list(_sessions[session_id])
        new_id = str(uuid.uuid4())
        _sessions[new_id] = []
        while len(_sessions) > MAX_SESSIONS:  # drop the oldest sessions
            del _sessions[next(iter(_sessions))]
        return new_id, []


def _remember(session_id: str, message: str, reply: str) -> None:
    with _lock:
        history = _sessions.setdefault(session_id, [])
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": reply})
        del history[:-MAX_HISTORY_MESSAGES]


def _ticket_ids(ids) -> list[str]:
    """Upper-cased, deduped T-<digits> IDs in order, at most MAX_TRIAGE_IDS."""
    kept = []
    for raw in ids or []:
        tid = str(raw).strip().upper()
        if TICKET_ID_RE.fullmatch(tid) and tid not in kept:
            kept.append(tid)
    return kept[:MAX_TRIAGE_IDS]


def _ask_chat(history: list[dict], message: str) -> tuple[str, list[str]]:
    """Run the chat assistant in its own event loop (this runs in FastAPI's worker threadpool)."""
    result = asyncio.run(_get_chat_fn()(history, message))
    if isinstance(result, dict):
        reply, ids = result.get("reply"), result.get("triage_ticket_ids")
    else:
        reply, ids = getattr(result, "reply", None), getattr(result, "triage_ticket_ids", None)
    return (str(reply or "").strip() or HELP_REPLY), _ticket_ids(ids)


# A sync endpoint: FastAPI runs it in a worker thread, so the chat agent's asyncio.run never blocks the event loop.
@app.post("/api/chat")
def chat(body: ChatRequest) -> dict:
    message = body.message
    if not message.strip():
        raise HTTPException(status_code=422, detail="The message is empty.")
    if len(message) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=422, detail=f"The message is longer than {MAX_MESSAGE_CHARS} characters.")
    session_id, history = _session(body.session_id)

    explicit = _ticket_ids(TICKET_ID_RE.findall(message))
    if explicit:
        # Fast path: explicit ticket IDs skip the chat assistant.
        ticket_ids = explicit
        reply = " ".join(f"Triaging {tid}…" for tid in ticket_ids)
    else:
        try:
            reply, ticket_ids = _ask_chat(history, message)
        except Exception as exc:  # noqa: BLE001 - a failed chat turn is an apologetic reply, not a 500
            return {
                "reply": f"Sorry, I could not answer that: {_short_error(exc)}",
                "session_id": session_id,
                "run_ids": [],
                "run_id": None,
            }

    run_ids = [start_run(tid).run_id for tid in ticket_ids]
    _remember(session_id, message, reply)
    return {"reply": reply, "session_id": session_id, "run_ids": run_ids, "run_id": run_ids[0] if run_ids else None}


def _find(run_id: str) -> Run:
    run = _runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown run.")
    return run


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    with _lock:
        return _find(run_id).view()


@app.post("/api/runs/{run_id}/approval")
def post_approval(run_id: str, body: ApprovalRequest) -> dict:
    with _lock:
        run = _find(run_id)
        if run.status != "awaiting_approval":
            raise HTTPException(status_code=409, detail="This run is not waiting for approval.")
        run.answer = body.approve is True
        run.approval = None
        run.status = "running"
        run.answered.set()
        return {"status": run.status}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
