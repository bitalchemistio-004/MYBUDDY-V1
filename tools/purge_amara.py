from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "memory" / "mbuddy.sqlite3"


def main() -> int:
    if not DB_PATH.exists():
        print("Database not found.")
        return 0

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        for table in tables:
            print(f"Purging table: {table}")
            try:
                cursor.execute(f"PRAGMA table_info({table})")
                columns = [row[1] for row in cursor.fetchall()]
                clauses = [f"lower(cast({col} as text)) LIKE '%amara%'" for col in columns]
                if not clauses:
                    continue
                where_sql = " OR ".join(clauses)
                cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {where_sql}")
                count = cursor.fetchone()[0]
                if count:
                    print(f"Deleting {count} rows from {table}...")
                    cursor.execute(f"DELETE FROM {table} WHERE {where_sql}")
                else:
                    print("No matches.")
            except sqlite3.Error as exc:
                print(f"Skipping {table}: {exc}")

    print("\nPURGE COMPLETE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
