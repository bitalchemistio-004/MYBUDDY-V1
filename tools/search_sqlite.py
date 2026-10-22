from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "memory" / "mbuddy.sqlite3"


def main() -> int:
    if not DB_PATH.exists():
        print("Database not found.")
        return 0

    match_found = False
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        for table in tables:
            print(f"Searching table: {table}")
            try:
                cursor.execute(f"SELECT * FROM {table}")
                for row in cursor.fetchall():
                    if "amara" in str(row).lower():
                        print(f"MATCH FOUND in {table}: {row}")
                        match_found = True
            except sqlite3.Error as exc:
                print(f"Error reading table {table}: {exc}")

    print("\nSearch complete." if match_found else "\nNo 'amara' mentions found in SQLite database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
