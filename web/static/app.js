// Ticket triage chat front end. Plain JS, no dependencies.
// All dynamic text is rendered with textContent / createElement, never innerHTML.
"use strict";

(() => {
  const POLL_MS = 1000;
  const MAX_POLL_FAILURES = 5;

  const transcript = document.getElementById("transcript");
  const scroller = document.querySelector(".chat");
  const form = document.getElementById("composer");
  const input = document.getElementById("message");
  const sendBtn = document.getElementById("send");

  const SESSION_KEY = "triage-chat-session-id";
  const MAX_RUNS = 3;

  let busy = false;
  let sessionId = loadSessionId();

  // ---------- Session (memory first; sessionStorage is optional) ----------
  function loadSessionId() {
    try {
      const v = window.sessionStorage.getItem(SESSION_KEY);
      return typeof v === "string" && v ? v : null;
    } catch {
      return null;
    }
  }

  function saveSessionId(value) {
    if (typeof value !== "string" || !value || value === sessionId) return;
    sessionId = value;
    try { window.sessionStorage.setItem(SESSION_KEY, value); } catch { /* storage blocked: memory only */ }
  }

  // ---------- DOM helpers ----------
  function el(tag, opts = {}, children = []) {
    const node = document.createElement(tag);
    if (opts.className) node.className = opts.className;
    if (opts.text != null) node.textContent = String(opts.text);
    if (opts.attrs) {
      for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, v);
    }
    for (const child of children) node.appendChild(child);
    return node;
  }

  function scrollToEnd() {
    scroller.scrollTop = scroller.scrollHeight;
  }

  // Appends a message row and returns its bubble element.
  function addMessage(who, content, extraClass = "") {
    const bubble = el("div", { className: "bubble" });
    if (typeof content === "string") {
      bubble.appendChild(el("p", { text: content }));
    } else if (content) {
      bubble.appendChild(content);
    }
    const row = el("div", { className: `msg msg-${who} ${extraClass}`.trim() }, [bubble]);
    transcript.appendChild(row);
    scrollToEnd();
    return bubble;
  }

  function addError(text) {
    return addMessage("bot", text, "msg-error");
  }

  // Returns {row, setLabel} so callers can relabel or remove the indicator.
  function addTyping(label) {
    const dots = el("span", { className: "dots", attrs: { "aria-hidden": "true" } },
      [el("span"), el("span"), el("span")]);
    const text = el("span", { text: label });
    const wrap = el("span", { className: "typing" }, [dots, text]);
    const bubble = addMessage("bot", wrap);
    return {
      row: bubble.parentElement,
      setLabel: (value) => { if (text.textContent !== value) text.textContent = value; },
    };
  }

  function setBusy(value) {
    busy = value;
    input.disabled = value;
    sendBtn.disabled = value;
    for (const chip of document.querySelectorAll(".chip")) chip.disabled = value;
    if (!value) input.focus();
  }

  // ---------- HTTP ----------
  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.status = status;
    }
  }

  // FastAPI returns {"detail": str} or {"detail": [{msg: ...}, ...]} for 422.
  function detailText(body) {
    if (!body || body.detail == null) return "";
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((d) => (d && d.msg) || "").filter(Boolean).join("; ");
    }
    return "";
  }

  function friendlyStatus(status, detail) {
    switch (status) {
      case 404: return "That run could not be found. The server may have restarted; please send your request again.";
      case 409: return "That question has already been answered.";
      case 422: return detail || "That message could not be accepted. Please send a short, non-empty message.";
      default:  return detail || `The server returned an error (HTTP ${status}).`;
    }
  }

  async function api(method, path, body) {
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch {
      throw new ApiError("Could not reach the server. Check that it is running and try again.", 0);
    }
    let data = null;
    try { data = await res.json(); } catch { data = null; }
    if (!res.ok) throw new ApiError(friendlyStatus(res.status, detailText(data)), res.status);
    if (data === null) throw new ApiError("The server sent an unreadable response.", res.status);
    return data;
  }

  // ---------- Run lifecycle ----------
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function renderDecision(ticketId, decision, escalated) {
    const d = decision || {};
    const card = document.createDocumentFragment();
    card.appendChild(el("h2", { className: "card-title", text: `Triage result for ${ticketId || "ticket"}` }));

    const priority = String(d.priority ?? "").toUpperCase();
    const badgeClass = /^P[1-4]$/.test(priority) ? ` badge-${priority}` : "";
    const badge = el("span", { className: `badge${badgeClass}`, text: priority || "n/a" });

    const rows = [
      ["Category", el("span", { text: d.category ?? "n/a" })],
      ["Priority", badge],
      ["Route", el("span", { text: d.route ?? "n/a" })],
      ["Rationale", el("span", { text: d.rationale ?? "" })],
      ["Escalated to a human", el("span", { text: escalated ? "yes" : "no" })],
    ];
    const dl = el("dl", { className: "fields" });
    for (const [label, value] of rows) {
      dl.appendChild(el("dt", { text: label }));
      dl.appendChild(el("dd", {}, [value]));
    }
    card.appendChild(dl);
    addMessage("bot", card, "msg-card");
  }

  // Shows the approval prompt. Returns a controller used to lock the buttons.
  function renderApproval(runId, approval, onAnswered) {
    const frag = document.createDocumentFragment();
    const who = approval.ticket_id ? `Ticket ${approval.ticket_id}` : "This ticket";
    frag.appendChild(el("p", { text: `${who} needs a human. Escalate it?` }));
    frag.appendChild(el("p", { text: `Reason: ${approval.reason || "(no reason given)"}` }));

    const yes = el("button", { className: "btn-primary", text: "Yes, escalate", attrs: { type: "button" } });
    const no = el("button", { text: "No", attrs: { type: "button" } });
    const actions = el("div", { className: "actions", attrs: { role: "group", "aria-label": "Approve escalation" } }, [yes, no]);
    frag.appendChild(actions);
    const bubble = addMessage("bot", frag);

    const lock = (chosen) => {
      yes.disabled = true;
      no.disabled = true;
      if (chosen) chosen.classList.add("btn-chosen");
    };

    const answer = async (approve, btn) => {
      lock(btn);
      let status = null;
      try {
        const res = await api("POST", `/api/runs/${encodeURIComponent(runId)}/approval`, { approve });
        status = res.status;
        bubble.appendChild(el("p", { text: approve ? "You approved the escalation." : "You declined the escalation." }));
      } catch (err) {
        addError(err.message);
      }
      scrollToEnd();
      // Hand focus to the next unanswered prompt, if another run is waiting.
      const next = transcript.querySelector(".actions .btn-primary:not(:disabled)");
      if (next) next.focus();
      onAnswered(status);
    };

    yes.addEventListener("click", () => answer(true, yes), { once: true });
    no.addEventListener("click", () => answer(false, no), { once: true });
    // Several runs may ask at once: only take focus if no other prompt holds it.
    const active = document.activeElement;
    const otherPromptFocused = active && active !== input && active.closest &&
      active.closest(".actions") && !active.disabled;
    if (!otherPromptFocused) yes.focus();
    return { lock: () => lock(null) };
  }

  const triagingLabel = (id) => (id ? `Triaging ${id}…` : "Triaging…");

  // Polls one run until it is terminal. Never throws; each run reports its own errors.
  async function pollRun(runId, ticketId) {
    let typing = null;
    let prompt = null;       // current approval prompt, if any
    let settling = false;    // answered, but server not yet seen leaving awaiting_approval
    let failures = 0;

    const showTyping = () => {
      if (!typing) typing = addTyping(triagingLabel(ticketId));
      else typing.setLabel(triagingLabel(ticketId));
    };
    const hideTyping = () => { if (typing) { typing.row.remove(); typing = null; } };
    const fail = (text) => {
      hideTyping();
      if (prompt) prompt.lock();
      addError(ticketId ? `${ticketId}: ${text}` : text);
    };

    showTyping();
    for (;;) {
      let run;
      try {
        run = await api("GET", `/api/runs/${encodeURIComponent(runId)}`);
        failures = 0;
      } catch (err) {
        failures += 1;
        if (err.status === 404 || failures >= MAX_POLL_FAILURES) {
          fail(err.message);
          return;
        }
        await sleep(POLL_MS);
        continue;
      }

      if (run && typeof run.ticket_id === "string" && run.ticket_id) ticketId = run.ticket_id;

      switch (run && run.status) {
        case "running":
          if (prompt) { prompt.lock(); prompt = null; } // e.g. approval timed out
          settling = false;
          showTyping();
          break;
        case "awaiting_approval":
          hideTyping();
          if (!prompt && !settling && run.approval) {
            prompt = renderApproval(runId, run.approval, (status) => {
              prompt = null;
              // Only wait for a "running" poll if the server did not confirm it moved on.
              settling = status === "awaiting_approval";
            });
          }
          break;
        case "done":
          hideTyping();
          if (prompt) prompt.lock();
          renderDecision(ticketId, run.decision, run.escalated === true);
          return;
        case "error":
          fail(run.error || "The triage run failed.");
          return;
        default:
          fail("The server reported an unknown run status.");
          return;
      }
      await sleep(POLL_MS);
    }
  }

  // ---------- Composer ----------
  // Prefer run_ids (v2); fall back to the single run_id (v1). Dedupe, cap, strings only.
  function runIdsFrom(res) {
    let ids = Array.isArray(res.run_ids) ? res.run_ids : (res.run_id ? [res.run_id] : []);
    ids = ids.filter((id) => typeof id === "string" && id);
    return [...new Set(ids)].slice(0, MAX_RUNS);
  }

  async function send(text) {
    const message = text.trim();
    if (busy || !message) return;
    if (message.length > 500) {
      addError("That message is too long. Please keep it under 500 characters.");
      return;
    }
    addMessage("user", message);
    input.value = "";
    setBusy(true);
    try {
      const res = await api("POST", "/api/chat", { message, session_id: sessionId });
      saveSessionId(res.session_id);
      if (res.reply) addMessage("bot", String(res.reply));
      const runIds = runIdsFrom(res);
      // Best-effort ticket labels before the first poll: explicit IDs in the message, in order.
      const typed = [...new Set((message.match(/T-\d+/gi) || []).map((t) => t.toUpperCase()))];
      const labels = runIds.length === typed.length ? typed : [];
      await Promise.all(runIds.map((id, i) => pollRun(id, labels[i] || null)));
    } catch (err) {
      addError(err.message);
    } finally {
      setBusy(false);
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    send(input.value);
  });

  // Chips live inside the transcript; delegate so they work via click or keyboard.
  transcript.addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (chip && !chip.disabled) send(chip.dataset.prompt || chip.dataset.ticket || chip.textContent || "");
  });

  input.focus();
})();
