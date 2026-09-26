---
id: SPEC-epic-1
companions: [../../../mcp/triage_server.py]
sources: [../../../INTENT.md]
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability — consult them only if you need narrative rationale or prose color this contract intentionally omits.

# Epic 1: triage data and schema

## Why

Epics 2 and 3 are specced against an Epic 1 triage-decision schema and a SQLite database that don't exist yet: the agent must return decisions in that schema, the eval scores against it, and `mcp/triage_server.py` reads `app.db`. Epic 1 creates both, so the workshop has a fixed decision contract and real data before any model is involved.

## Capabilities

- **CAP-1**
  - **intent:** Every triage decision is a JSON object with exactly four fields — `category`, `priority`, `route`, `rationale` — and anything else is rejected with a clear error.
  - **success:** A decision with `category` in {billing, bug, access, performance, how-to}, `priority` in {P1, P2, P3, P4}, `route` in {billing-team, bug-team, access-team, performance-team, how-to-team} and a non-empty `rationale` string validates (one sentence is the intended style, not an enforced check). An out-of-set value, a missing field, an extra field or an empty rationale fails, and the error names the offending field.

- **CAP-2**
  - **intent:** One command loads the seed CSVs into a local SQLite database the MCP server can read.
  - **success:** `uv run python load_seed.py` creates `app.db` with table `tickets` (columns of `seed/tickets.csv`: `ticket_id, customer_id, created_at, text`) and table `customers` (columns of `seed/customers.csv`: `customer_id, name, plan, open_tickets`). Row counts match the CSVs, and the queries in `mcp/triage_server.py` return rows, e.g. ticket `T-1042`.

- **CAP-3**
  - **intent:** Running the loader again gives the same database.
  - **success:** After two consecutive runs of `load_seed.py`, both tables hold the same rows as after one run — no duplicates, no errors.

## Constraints

- Python 3.12 or newer, managed with uv; add packages with `uv add`, never pip.
- `seed/` is read-only: the loader reads it and never writes to it.
- No network calls and no API keys in this epic.
- `mcp/triage_server.py` is read-only and queries `tickets(ticket_id, customer_id, created_at, text)` and `customers(customer_id, name, plan, open_tickets)`; those table and column names must keep working.
- `app.db` is git-ignored and never committed.

## Non-goals

- The agent (Epic 2).
- The MCP tools; `mcp/triage_server.py` already exists and is not changed.
- Evals (Epic 3).
- Any user interface.

## Success signal

On a fresh clone, `uv run python load_seed.py` run twice leaves an `app.db` from which `mcp/triage_server.py` returns ticket `T-1042` and its customer. The test suite (`uv run pytest`) passes without network access, showing a valid decision accepted and each kind of invalid decision rejected by field name.

## Assumptions

- "Anything else is rejected" includes extra fields beyond the four.
- The loader rebuilds both tables from scratch on each run, which is how it stays idempotent.
- `open_tickets` is stored as an integer; every other column as text.
- Where the schema lives and which library enforces it are left to the build; `pydantic` is already a dependency.
