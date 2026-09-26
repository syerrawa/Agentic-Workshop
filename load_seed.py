"""Load the seed CSVs into app.db for the MCP server (Epic 1, CAP-2 and CAP-3).

Usage: uv run python load_seed.py

Both tables are dropped and rebuilt on every run, so running it twice gives
the same database as running it once.
"""

import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_DB = ROOT / "app.db"
SEED_DIR = ROOT / "seed"

# Table name -> {column name: SQL type}. Key order is the expected CSV header, so do not reorder.
# mcp/triage_server.py queries these names.
TABLES = {
    "tickets": {
        "ticket_id": "TEXT PRIMARY KEY",
        "customer_id": "TEXT",
        "created_at": "TEXT",
        "text": "TEXT",
    },
    "customers": {
        "customer_id": "TEXT PRIMARY KEY",
        "name": "TEXT",
        "plan": "TEXT",
        "open_tickets": "INTEGER",
    },
}


def read_rows(csv_path: Path, columns: list[str]) -> list[dict]:
    # utf-8-sig also reads files saved with a BOM (Excel adds one), which would break the header check.
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != columns:
            raise ValueError(f"{csv_path.name}: expected columns {columns}, got {reader.fieldnames}")
        rows = []
        for row in reader:
            # DictReader files extra fields under None and fills missing ones with None.
            if None in row or None in row.values():
                raise ValueError(f"{csv_path.name} line {reader.line_num}: expected {len(columns)} fields")
            rows.append(row)
        return rows


def load(db_path: Path = APP_DB, seed_dir: Path = SEED_DIR) -> dict[str, int]:
    """Rebuild every table in one transaction and return the row count per table."""
    rows = {table: read_rows(seed_dir / f"{table}.csv", list(columns)) for table, columns in TABLES.items()}
    conn = sqlite3.connect(db_path, autocommit=False)
    try:
        for table, columns in TABLES.items():
            names = ", ".join(columns)
            placeholders = ", ".join(f":{name}" for name in columns)
            conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.execute(f"CREATE TABLE {table} ({', '.join(f'{name} {kind}' for name, kind in columns.items())}) STRICT")
            conn.executemany(f"INSERT INTO {table} ({names}) VALUES ({placeholders})", rows[table])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {table: len(table_rows) for table, table_rows in rows.items()}


def main() -> None:
    counts = load()
    print(f"Loaded {counts['tickets']} tickets and {counts['customers']} customers into {APP_DB.name}")


if __name__ == "__main__":
    main()
