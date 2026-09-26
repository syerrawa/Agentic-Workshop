---
title: 'Story 1.2: Seed loader'
type: 'feature'
created: '2026-09-26'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: ['{project-root}/_bmad-output/specs/spec-epic-1/SPEC.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `mcp/triage_server.py` reads `tickets` and `customers` from `app.db`, but nothing creates that database, so Epic 2's agent has no data and fails with "app.db not found" (CAP-2, CAP-3).

**Approach:** Add `load_seed.py` at the repo root. `uv run python load_seed.py` reads `seed/tickets.csv` and `seed/customers.csv` with the stdlib `csv` module, then drops, recreates and fills both tables in `app.db` with `sqlite3`, so every run gives the same database. Offline pytest tests in `tests/test_load_seed.py` check the row counts, that a second run leaves the same rows, and that the unchanged queries in `mcp/triage_server.py` return `T-1042` and its customer.

</frozen-after-approval>

## Implementation Notes

- Tables: `tickets(ticket_id TEXT PRIMARY KEY, customer_id TEXT, created_at TEXT, text TEXT)` and `customers(customer_id TEXT PRIMARY KEY, name TEXT, plan TEXT, open_tickets INTEGER)`. The seed data has 24 tickets and 20 customers, with unique IDs and no ticket pointing at a missing customer.
- Decision: the primary keys make a duplicate ID in the seed fail loudly rather than load twice. No foreign key: the MCP server does not need one, and the seed has no orphan tickets.
- Decision: the drop, create and insert steps run in one transaction (`sqlite3.connect(..., autocommit=False)`, Python 3.12+), so if a load fails partway, the previous `app.db` is left as it was.
- Decision: the CSV header must equal the expected columns exactly, or the loader raises `ValueError` naming the file. A seed change then shows up at load time, not as a missing column in the MCP server.
- Decision: `load(db_path=APP_DB, seed_dir=SEED_DIR) -> dict[str, int]` holds the logic so tests can point it at `tmp_path`. `main()` prints one line with both row counts. Paths are resolved from `__file__`, so the command works from any working directory.
- Tests load `mcp/triage_server.py` by file path (`importlib.util.spec_from_file_location`), because `import mcp` resolves to the installed MCP SDK and not the local `mcp/` folder. They monkeypatch its `DB_PATH` to the temporary database and call `get_ticket` and `get_customer_history` directly; `@server.tool()` returns the plain function. No MCP process and no network.
- Expected T-1042 values: customer `C-77`, Northwind, Enterprise, 2 open tickets (stored as the integer `2`).
- Do not touch `seed/`, `mcp/`, `schema.py`, `run_agent.py`, `eval/` or `TRIAGE_POLICY.md`. No new dependencies.
- Files changed: `load_seed.py` (new), `tests/test_load_seed.py` (new, 12 offline tests; suite is now 52). No change to `pyproject.toml`: the `pythonpath = ["."]` from Story 1.1 already lets tests import `load_seed`.
- Table layout lives in one `TABLES` dict (column name to SQL type, in CSV header order). The CREATE statements, the header check and the tests all read from it.
- Verified by hand: `uv run python load_seed.py` run twice (once from the repo root, once from `/tmp`) prints 24 tickets and 20 customers each time; `app.db` stays untracked; `seed/` is unchanged.
- After review: tables are `STRICT` (SQLite 3.37+; the bundled one is 3.50), so `open_tickets` must convert to an integer and a value like `two` fails with `IntegrityError`. `read_rows` rejects a row with too many or too few fields, naming the file and line; before this, `csv.DictReader` quietly cut or padded such rows.

## Review Triage Log

- low, patch: rows under the header were never checked; an extra comma truncated `text`, a short row loaded NULLs, and `open_tickets=two` loaded as text. Fixed with the per-row field check and `STRICT` tables; tested.
- low, patch: no check that every ticket's customer exists, although the no-foreign-key decision relies on it. Added `test_every_ticket_has_a_customer`.
- low, patch: the rollback test compared `tickets` by count only, though `tickets` is the table rebuilt before the failure. Now both tables are compared row for row.
- low, patch: the `TABLES` comment described a tuple; it is a dict whose key order is the expected header. Reworded.
- low, rejected: `main()` and the real command have no automated test. Checked by hand; a subprocess test would write the real `app.db` or need a new path parameter on `main()`.
- low, rejected: a failed *first* load leaves an empty `app.db`, so the MCP server says "no such table" instead of "app.db not found". The seed is read-only and valid, so this needs a broken seed, and the fix (temp file plus replace, or cleanup) adds branches.
- low, rejected: more header-mismatch cases (reordered, extra, missing, BOM) are untested. The check is exact list equality, so every variant fails the same way; the seed has no BOM.
- false: the story file is incomplete (`in-progress`, no triage log). That was the correct state mid-workflow; the spec is finalized in this step.
