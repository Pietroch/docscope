# api/src/docscope/migrations/0003_add_validated_column.py
#
# One-off migration: adds the `validated` column to `document` (default
# false). Plain ALTER TABLE ADD COLUMN - no table rebuild needed here,
# unlike the CHECK constraint changes in earlier migrations. Idempotent:
# skipped if the column already exists.
#
# Run inside the api container: python src/docscope/migrations/0003_add_validated_column.py

import sqlite3

from docscope.core.config import DB_PATH


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(document)")}
        if "validated" in columns:
            print("skip: validated column already exists")
            return
        conn.execute("ALTER TABLE document ADD COLUMN validated BOOLEAN NOT NULL DEFAULT 0")
        conn.commit()
        print("document.validated column added")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
