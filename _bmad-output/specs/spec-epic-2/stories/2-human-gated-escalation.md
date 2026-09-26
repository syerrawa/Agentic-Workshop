---
title: 'Story 2.2: Human-gated escalation'
type: 'feature'
created: '2026-09-26'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '4e7fb11e56b5c3c61e561f81b5e369ad01aee020'
context: ['{project-root}/_bmad-output/specs/spec-epic-2/SPEC.md', '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md', '{project-root}/_bmad-output/specs/spec-epic-2/stories/1-the-triage-agent.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `TRIAGE_POLICY.md` says to escalate a ticket to a person when the final priority is P1 and the customer is Enterprise, with nothing escalated without a person's approval. The Story 2.1 agent has no `escalate_to_human` tool and no approval gate (Epic 2 CAP-5).

**Approach:** Add a local `escalate_to_human` tool to the agent, and gate it with LangChain's `HumanInTheLoopMiddleware`. The run pauses on every escalation call, and only an explicit "yes" lets it through. `triage()` takes an approver callable. `run_agent.py` passes a terminal yes/no prompt, and Epic 3 can pass its own approver.

**Decisions (from the human):**
- **Output:** `run_agent.py` prints the decision JSON unchanged, then the line `Escalated to a human: yes` or `Escalated to a human: no`.
  - `agent.triage_with_escalation(ticket_id, model=None, approve=None)` returns `(decision, escalated)`.
  - `triage(ticket_id, model=None, approve=None)` keeps returning only the decision dict.
  - `escalated` is True only when an `escalate_to_human` call was approved.
- **Rule enforcement:** the prompt alone decides when to call `escalate_to_human`, with no code check of P1+Enterprise. The gate guarantees nothing escalates without a yes.

## Boundaries & Constraints

**Always:**
- `escalate_to_human` is defined in `agent.py`, not in the MCP server.
- `HumanInTheLoopMiddleware` gates every call to it, with only `approve` and `reject` allowed.
- An interrupt is resumed with the approver's decision inside the same `triage()` call, so a run is still one MLflow trace under `run_agent.py`'s span.
- Anything other than an explicit yes (for example "no", EOF, or an approver returning False) is a reject.
- The returned decision still passes `TriageDecision.model_validate`, and the Story 2.1 grounding check and retry budget still hold.
- Tests run offline.

**Never:**
- Modify `schema.py`, `mcp/triage_server.py`, `TRIAGE_POLICY.md`, `seed/`, or the MLflow lines in `run_agent.py`.
- Escalate or report "escalated" without an approval.
- Build eval code or auto-approval inside the agent (Epic 3 supplies its own approver).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| P1 + Enterprise, yes | `T-1044` (whole team locked out, Enterprise) and the person types `yes` | The run pauses at a yes/no prompt, then completes: `access` / `P1`, escalated | N/A |
| P1 + Enterprise, no | `T-1044` and the person types `no` | Completes with the decision, not escalated | The rejection goes back to the model as the tool result |
| Rule doesn't fire | `T-1042` (P2) | No prompt, and not escalated | N/A |
| Unclear answer | The person types `maybe` | Re-prompts until the answer is yes or no | N/A |
| No terminal | stdin is closed (EOF) at the prompt | Treated as "no" | Never escalates |
| Programmatic approver | `triage("T-1044", approve=lambda req: True)` | Approved with no terminal input | N/A |

</frozen-after-approval>

## Code Map

- `agent.py` -- Story 2.1's agent:
  - `build_agent(model, tools, handle_errors)` is the single construction point. Add the local tool, the middleware and a checkpointer here.
  - The triage `while True` loop gets structured output and retries a plain-text answer. An interrupt needs to be handled inside this loop.
  - `_check_grounded` walks the messages. It must allow an `escalate_to_human` call and its ToolMessage between `get_customer_history` and the final `TriageDecision`.
  - `AGENT_RULES` is the prompt addition. Add a step: when the final priority is P1 and the plan is Enterprise, call `escalate_to_human` before `TriageDecision`.
- `.venv/.../langchain/agents/middleware/human_in_the_loop.py` -- `HumanInTheLoopMiddleware(interrupt_on={"escalate_to_human": {"allowed_decisions": ["approve","reject"]}})`. The interrupt payload is an `HITLRequest` with `action_requests`. Resume with `Command(resume={"decisions": [{"type": "approve"}]})` or `[{"type": "reject", "message": …}]`. A reject becomes a ToolMessage "User rejected the tool call…" sent to the model.
- `langgraph.checkpoint.memory.InMemorySaver` and `langgraph.types.Command` -- Interrupts need a checkpointer and a `thread_id` in the config. Use a fresh saver and thread per `triage` call.
- `run_agent.py` -- Only the MLflow lines are protected. It passes a terminal approver to `triage`, and prints the escalation line (see Decisions).
- `tests/test_agent.py` -- Reuse `ScriptedModel`, `SERVER_WRAPPER` and the autouse tmp-db fixture. `ScriptedModel` needs an escalation step.
- Seed: `T-1044` (C-91), `T-1048` (C-05) and `T-1057` (C-66) are P1 with Enterprise customers. `T-1042` is P2.

## Tasks & Acceptance

**Execution:**
- [x] `agent.py` -- Add the following:
  - A local `@tool escalate_to_human(ticket_id: str, reason: str) -> str`. It returns a confirmation string; no external side effect exists.
  - `HumanInTheLoopMiddleware` for that tool, with approve and reject only, plus an `InMemorySaver` checkpointer, all in `build_agent`.
  - A `triage(ticket_id, model=None, approve=None)` loop. On `__interrupt__`, it asks `approve(request) -> bool` (default: the terminal prompt) and resumes with approve or reject.
  - Escalation tracking: approved only when the approver said yes.
  - A grounding check that allows the escalation step, and the `AGENT_RULES` step. Add `triage_with_escalation` returning `(decision, escalated)`, and `triage` as a thin wrapper around it.
- [x] `run_agent.py` -- Add a terminal approver that shows the ticket and reason and asks `Escalate <id> to a human? [yes/no]`. It accepts y/yes/n/no in any case, re-prompts on anything else, and treats EOF as no. Call `triage_with_escalation`, print the decision JSON, then `Escalated to a human: yes|no`. Leave the MLflow lines unchanged.
- [x] `tests/test_agent.py` -- Add offline tests with a scripted escalation step:
  - An approver returning True gives escalated, with the decision valid.
  - An approver returning False gives not escalated, with the decision still returned.
  - A P2 ticket never calls the approver.
  - The middleware is configured with only approve and reject.
  - The grounding check accepts ticket, then history, then escalate, then decision.
  - The approver sees the ticket ID and the reason.
- [x] `tests/test_run_agent.py` -- Test the terminal approver's parsing: yes and y map to True; no, n and EOF map to False; `maybe` re-prompts. Use monkeypatched input.

**Acceptance Criteria:**
- Given `app.db` and a key, when `uv run python run_agent.py T-1044` runs and the person answers `yes`, then the run pauses for the prompt and completes as `access` / `P1`, reported as escalated.
- Given the same run, when the person answers `no`, then the run completes and is reported as not escalated.
- Given `uv run python run_agent.py T-1042`, when it runs, then no prompt appears and the output matches Story 2.1.
- Given no network, when `uv run pytest` runs, then all tests pass.

## Implementation Notes

- Files touched: `agent.py`, `run_agent.py` (MLflow lines unchanged), `tests/test_agent.py` (+17 tests), `tests/test_run_agent.py` (new, 14 tests). No protected file changed, and no dependency was added.
- `agent.py`:
  - Adds `escalate_to_human` (`@tool`, returns a confirmation string) and `build_escalation_gate()` (`HumanInTheLoopMiddleware`, approve/reject only).
  - `build_agent` appends the tool, and adds the gate and a fresh `InMemorySaver`.
  - `triage_with_escalation` runs on one `thread_id`. On `__interrupt__` it asks the approver for each action request and resumes with `Command(resume=...)`. Resuming spends no retry.
  - A text-answer retry now sends only the new HumanMessage, because the checkpointer holds the conversation.
  - Only an approver returning exactly `True` approves. A reject carries a message that tells the model not to re-call the tool and to answer with `TriageDecision`.
  - The grounding check also rejects an `escalate_to_human` call made before the customer lookup returned, or made for another ticket.
- Approver placement: there is a single `agent.terminal_approver`. It is the default (so `approve=None` never approves silently), and `run_agent.py` imports it and passes it.
- The prompt, including the question, prints to stderr, and `input()` is called with no argument. So stdout carries only the decision JSON and the `Escalated to a human:` line.
- The model-written ticket_id and reason are printed with non-printable characters escaped.
- An `OSError` from stdin rejects, like EOF.
- One escalation review per run: a second interrupt raises `RuntimeError` ("escalate_to_human requested more than once").
- `uv run pytest`: 108 passed offline.
- Live (OpenRouter, `openrouter/free`):
  - T-1044 with `yes` gave access/P1/access-team, escalated. With `no`, it gave the same decision, not escalated.
  - T-1042 gave billing/P2 with no prompt. One earlier run gave P1: the model misapplied the Enterprise rule with 2 open tickets. It still did not call escalate_to_human.
  - Traces: the `yes` run is one `triage` trace holding `get_ticket`, `get_customer_history` and `escalate_to_human`. The `no` run has no `escalate_to_human` span, because the tool never ran.
- Flagged, not done: the installed MLflow `MlflowLangchainTracer` lacks the `on_interrupt`/`on_resume` callbacks. Every escalation logs `Error in MlflowLangchainTracer.on_interrupt callback: AttributeError(...)`, and the same for `on_resume`. It is harmless (the trace stays whole) but noisy. Fixing it means an MLflow upgrade or patching the tracer, which is outside this story.

## Spec Change Log

## Review Triage Log

| # | Source | Finding | Verdict | Evidence / route |
|---|--------|---------|---------|------------------|
| 1 | blind, edge | The approval loop is unbounded: a model that keeps calling `escalate_to_human` after each decision loops forever | medium | The `if interrupts: … continue` branch spends no budget and has no cap, so `approve=lambda r: False` with a stubborn model never ends. patch: allow one escalation request per run and raise on a second |
| 2 | blind, verif-gap, edge | `input(prompt)` writes the yes/no question to stdout, so piped output isn't pure JSON | medium | Python's `input()` prints its prompt to stdout, which contradicts the decided output (JSON, then the escalation line). The `main` test fakes triage, so it can't see this. patch: print the prompt to stderr, then call `input()` |
| 3 | blind, edge (claim) | The terminal approver exists twice (`agent.py` and `run_agent.py`), and the banners have already drifted | medium | Named harm: a parsing fix (e.g. #5) lands in one copy only, and the `agent.py` copy has no "no" test (verif-gap). patch: `run_agent.py` passes `agent.terminal_approver`, with one tested copy |
| 4 | verif-gap | The default approver's explicit "no"/"n" path is untested | medium | Pre-verified: only "maybe", "YES" and EOF drive `agent.terminal_approver`. patch: parametrized no/n/NO test |
| 5 | edge | `input()` raising `OSError` (stdin unavailable) crashes instead of rejecting | low | The matrix says no terminal means "no", and the fix is direct: catch `(EOFError, OSError)`. patch |
| 6 | blind, edge | The model-written `reason` is printed raw, so ANSI and control characters from ticket text can spoof the prompt | low | Ticket text is untrusted (AGENTS.md), and the fix is a direct escape of non-printables when printing. patch |
| 7 | blind | The approver doesn't see the priority, plan or ticket text | low | An enhancement: the matrix specifies ticket ID and reason, and the fix adds plumbing. Rejected |
| 8 | blind | `escalated` comes from the approval, not from a successful tool result | low | The tool is a pure function run by the middleware right after approval, and the spec defines escalated as approved. Rejected |
| 9 | blind, edge | Only `interrupts[0]` is reviewed | false | One HITL middleware gives one interrupt per step, several calls arrive as `action_requests` (all handled), and parallel calls are off |
| 10 | blind, edge (deletion) | `triage()` now defaults to a terminal prompt and can block non-interactive callers | false | Intended by Design Notes: a direct call must never approve silently, and Epic 3 passes its own approver |
| 11 | blind | The MLflow trace doesn't record `escalated` | low | The MLflow lines are protected, and Epic 3 reads the flag from the return value. Rejected |
| 12 | blind | The grounding docstring implies escalation-before-decision and at-most-once, which the code doesn't check | low | After #1, at most once is enforced, and an escalation after the decision cannot happen (the run ends at structured output). Rejected |
| 13 | blind | The tool docstring hard-codes the rule and the prompt test checks only keywords | low | A cosmetic risk of drift. Rejected |
| 14 | edge | A wrong-ticket or pre-history escalation reaches the approver before the grounding check | low | The tool has no side effect and the run then fails as not grounded, and the fix adds checks in `_review`. Rejected |
| 15 | edge | Approved escalation next to a non-P1 decision, with no signal | false | The human chose prompt-only enforcement (frozen Decisions) |

## Design Notes

- **Approver seam:** `approve` is a plain callable: `(request: dict) -> bool`, where the request has `ticket_id`, `reason` and the raw action request. The terminal prompt lives in `run_agent.py` and is passed in. If `triage` gets `approve=None`, it uses a default terminal approver defined in `agent.py`, so a direct call can never silently approve. Epic 3 passes `lambda req: True`.
- **One trace:** the interrupt and resume both happen inside `triage()`, on the same compiled agent and `thread_id`, so `run_agent.py`'s `triage` span holds the whole run (an Epic 3 requirement).
- **Retry budget interplay:** an interrupt is not a failure. Resuming doesn't spend the Story 2.1 retry budget.

## Verification

**Commands:**
- `uv run pytest` -- expected: all tests pass offline.
- `uv run python run_agent.py T-1044`, answering `yes`, then again answering `no` -- expected: a prompt appears; the output is access / P1, escalated per the answer.
- `uv run python run_agent.py T-1042` -- expected: no prompt; billing / P2.

**Manual checks:**
- The MLflow trace of the T-1044 "yes" run holds `get_ticket`, `get_customer_history`, then `escalate_to_human`, all in one trace.
