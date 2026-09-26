import csv
import importlib.util
import shutil
import sqlite3
from pathlib import Path

import pytest

import load_seed

ROOT = Path(__file__).resolve().parent.parent


def csv_rows(name: str) -> int:
    with (load_seed.SEED_DIR / name).open(newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.DictReader(f))


def table_rows(db_path: Path, table: str) -> list[tuple]:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "app.db"
    load_seed.load(db_path)
    return db_path


@pytest.fixture
def triage_server(db, monkeypatch):
    # `import mcp` finds the installed MCP SDK, so load the local server by path.
    spec = importlib.util.spec_from_file_location("triage_server", ROOT / "mcp" / "triage_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "DB_PATH", db)
    return module


def test_row_counts_match_the_csvs(db):
    assert len(table_rows(db, "tickets")) == csv_rows("tickets.csv") == 24
    assert len(table_rows(db, "customers")) == csv_rows("customers.csv") == 20


def test_load_returns_row_counts(tmp_path):
    assert load_seed.load(tmp_path / "app.db") == {"tickets": 24, "customers": 20}


def test_columns_match_the_mcp_server_contract(db):
    with sqlite3.connect(db) as conn:
        for table, columns in load_seed.TABLES.items():
            assert [row[1] for row in conn.execute(f"PRAGMA table_info({table})")] == list(columns)


def test_open_tickets_is_stored_as_an_integer(db):
    with sqlite3.connect(db) as conn:
        types = {row[0] for row in conn.execute("SELECT typeof(open_tickets) FROM customers")}
    assert types == {"integer"}


def test_second_run_gives_the_same_rows(db):
    before = {table: table_rows(db, table) for table in load_seed.TABLES}
    load_seed.load(db)
    assert {table: table_rows(db, table) for table in load_seed.TABLES} == before


def test_mcp_server_returns_t1042_and_its_customer(triage_server):
    ticket = triage_server.get_ticket("T-1042")
    assert ticket["customer_id"] == "C-77"
    assert ticket["text"] == "I was charged twice this month and nobody answers."
    customer = triage_server.get_customer_history(ticket["customer_id"])
    assert customer["name"] == "Northwind"
    assert customer["plan"] == "Enterprise"
    assert customer["open_tickets"] == 2
    assert "T-1042" in customer["ticket_ids"]


def test_wrong_csv_header_is_rejected_by_file_name(tmp_path):
    seed_dir = tmp_path / "seed"
    shutil.copytree(load_seed.SEED_DIR, seed_dir)
    (seed_dir / "customers.csv").write_text("customer_id,name,tier,open_tickets\nC-1,A,Team,0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="customers.csv"):
        load_seed.load(tmp_path / "app.db", seed_dir)


def test_every_ticket_has_a_customer(db):
    with sqlite3.connect(db) as conn:
        orphans = conn.execute(
            "SELECT ticket_id FROM tickets WHERE customer_id NOT IN (SELECT customer_id FROM customers)"
        ).fetchall()
    assert orphans == []


@pytest.mark.parametrize(
    "bad_row, error",
    [
        ("C-99,Extra,Team,1,surplus\n", "customers.csv line 22"),
        ("C-99,Short\n", "customers.csv line 22"),
    ],
)
def test_malformed_row_is_rejected_by_file_and_line(tmp_path, bad_row, error):
    seed_dir = tmp_path / "seed"
    shutil.copytree(load_seed.SEED_DIR, seed_dir)
    with (seed_dir / "customers.csv").open("a", encoding="utf-8") as f:
        f.write(bad_row)
    with pytest.raises(ValueError, match=error):
        load_seed.load(tmp_path / "app.db", seed_dir)


def test_non_integer_open_tickets_is_rejected(tmp_path):
    seed_dir = tmp_path / "seed"
    shutil.copytree(load_seed.SEED_DIR, seed_dir)
    with (seed_dir / "customers.csv").open("a", encoding="utf-8") as f:
        f.write("C-99,Initech,Team,two\n")
    with pytest.raises(sqlite3.IntegrityError, match="open_tickets"):
        load_seed.load(tmp_path / "app.db", seed_dir)


def test_failed_load_leaves_the_previous_database(db, tmp_path):
    before = {table: table_rows(db, table) for table in load_seed.TABLES}
    seed_dir = tmp_path / "seed"
    shutil.copytree(load_seed.SEED_DIR, seed_dir)
    with (seed_dir / "customers.csv").open("a", encoding="utf-8") as f:
        f.write("C-05,Duplicate,Team,0\n")
    with pytest.raises(sqlite3.IntegrityError):
        load_seed.load(db, seed_dir)
    assert {table: table_rows(db, table) for table in load_seed.TABLES} == before
