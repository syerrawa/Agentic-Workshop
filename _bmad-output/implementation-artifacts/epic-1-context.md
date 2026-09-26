# Epic 1 Context: Triage data and schema

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Epic 1 gives the workshop a fixed triage-decision contract and real data before any model is involved. It builds a strict JSON schema for triage decisions, and a loader that turns the seed CSVs into a local SQLite database (`app.db`) that the existing MCP server reads. Epics 2 and 3 are specced against both: the agent must return decisions in this schema, the eval scores against it, and the MCP tools query this database. (Note: no planning artifacts directory exists (no PRD, architecture, UX or brief). This context comes only from the epic's spec and story list, plus the Epic 2 and 3 specs for dependencies.)

## Stories

- Story 1.1: Triage decision schema
- Story 1.2: Seed loader

## Requirements & Constraints

- A decision is a JSON object with exactly four fields: `category`, `priority`, `route`, `rationale`.
  - `category` is one of: billing, bug, access, performance, how-to
  - `priority` is one of: P1, P2, P3, P4
  - `route` is one of: billing-team, bug-team, access-team, performance-team, how-to-team
  - `rationale` is a non-empty string. One sentence is the intended style, but that is not enforced.
- An out-of-set value, a missing field, an extra field or an empty rationale is rejected, and the error names the offending field.
- `uv run python load_seed.py` creates `app.db` with:
  - `tickets(ticket_id, customer_id, created_at, text)` from `seed/tickets.csv`
  - `customers(customer_id, name, plan, open_tickets)` from `seed/customers.csv`
  - Row counts match the CSVs.
- The loader is idempotent: two consecutive runs give the same rows as one run, with no duplicates and no errors.
- Success signal: on a fresh clone, running the loader twice leaves an `app.db` from which `mcp/triage_server.py` returns ticket `T-1042` and its customer. `uv run pytest` passes with no network access, covering a valid decision accepted and each kind of invalid decision rejected by field name.
- No network calls and no API keys anywhere in this epic.
- Out of scope: the agent, the MCP tools, evals, and any user interface.

## Technical Decisions

- Python 3.12 or newer, managed with uv. Add packages with `uv add`, never pip.
- Schema: use pydantic (already a dependency) with extra fields forbidden. Keep it in one importable module, because Epics 2 and 3 import it.
- Loader: use only the stdlib `csv` and `sqlite3` modules and add no dependencies. Drop and recreate both tables on every run; that is how it stays idempotent.
- Column types: `open_tickets` is an integer; every other column is text.
- `mcp/triage_server.py` is read-only and must not change. Its table and column names are the contract the database must satisfy.
- `seed/` is read-only: the loader reads it and never writes to it.
- `app.db` is git-ignored and never committed.

## Cross-Story Dependencies

- Stories 1.1 and 1.2 are independent of each other.
- Epic 2 (agent) returns its structured output in the Epic 1 schema, validates against it, and retries once on a validation failure. It reads tickets and customers from `app.db` through `mcp/triage_server.py`. Expected ground truth: T-1042 comes out as `billing` / `P2` / `billing-team` (customer Northwind, Enterprise, 2 open tickets). Epic 2 treats the schema and the loader as read-only.
- Epic 3 (eval) has a `valid_schema` scorer that validates every agent output locally against the Epic 1 schema. So the schema must work as a plain importable validator with no network access.
