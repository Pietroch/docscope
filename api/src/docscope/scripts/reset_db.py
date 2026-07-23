# api/src/docscope/scripts/reset_db.py
#
# Dev reset used by `make db-reset`: wipes every document that isn't
# validated (and its fields), but leaves validated documents untouched -
# validation is meant to mark data as finalized, so it must survive a
# reset. company/person rows are left as-is either way.
#
# Run inside the api container: python src/docscope/scripts/reset_db.py

import sqlite3

from docscope.core.config import DB_PATH


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "document" not in existing:
            print("skip: document table not found (nothing to reset)")
            return

        cur = conn.execute(
            "DELETE FROM document_field WHERE document_id IN "
            "(SELECT id FROM document WHERE validated = 0)"
        )
        deleted_fields = cur.rowcount
        cur = conn.execute("DELETE FROM document WHERE validated = 0")
        deleted_documents = cur.rowcount
        conn.commit()
        print(
            f"removed {deleted_documents} document(s) and {deleted_fields} field(s) "
            "(validated documents kept)"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
