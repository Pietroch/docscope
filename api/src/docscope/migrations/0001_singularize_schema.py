# api/src/docscope/migrations/0001_singularize_schema.py
#
# One-off migration: renames docscope's tables from plural to singular
# (companies -> company, people -> person, documents -> document,
# document_fields -> document_field). No column rename needed - the FK
# columns (company_id, person_id) were already singular before this change.
# Idempotent: renamed tables are skipped on re-run.
#
# The CHECK constraint on `document` keeps its old internal name
# (ck_documents_template) after this runs - SQLite has no ALTER TABLE
# statement to rename a constraint without a full table rebuild, and the
# name is cosmetic (schema introspection only, no effect on behavior).
# A table created fresh via create_tables() gets the new name directly.
#
# Run inside the api container: python src/docscope/migrations/0001_singularize_schema.py

import sqlite3

from docscope.core.config import DB_PATH

RENAMES = [
    ("companies", "company"),
    ("people", "person"),
    ("documents", "document"),
    ("document_fields", "document_field"),
]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for old_name, new_name in RENAMES:
            if old_name not in existing:
                print(f"skip {old_name} -> {new_name} (not found)")
                continue
            conn.execute(f"ALTER TABLE {old_name} RENAME TO {new_name}")
            print(f"renamed {old_name} -> {new_name}")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
