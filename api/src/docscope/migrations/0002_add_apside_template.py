# api/src/docscope/migrations/0002_add_apside_template.py
#
# One-off migration: widens the `ck_document_template` CHECK constraint to
# accept "apside" alongside "ucm"/"delvaux". SQLite has no ALTER TABLE for
# CHECK constraints, so the table is rebuilt: create the new table under a
# temp name, copy rows, drop the old one, rename. Idempotent: skipped if
# the constraint already allows "apside".
#
# Run inside the api container: python src/docscope/migrations/0002_add_apside_template.py

import sqlite3

from docscope.core.config import DB_PATH


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='document'"
        ).fetchone()
        if row is None:
            print("skip: document table not found")
            return
        if "'apside'" in row[0]:
            print("skip: apside already allowed")
            return

        conn.executescript(
            """
            PRAGMA foreign_keys=off;

            CREATE TABLE document_new (
                id INTEGER NOT NULL,
                name VARCHAR NOT NULL,
                type VARCHAR NOT NULL,
                template VARCHAR NOT NULL,
                date_document DATE NOT NULL,
                company_id INTEGER NOT NULL,
                person_id INTEGER NOT NULL,
                PRIMARY KEY (id),
                CONSTRAINT ck_document_template CHECK (template IN ('ucm', 'delvaux', 'apside')),
                FOREIGN KEY(company_id) REFERENCES company (id),
                FOREIGN KEY(person_id) REFERENCES person (id)
            );

            INSERT INTO document_new SELECT * FROM document;

            DROP TABLE document;

            ALTER TABLE document_new RENAME TO document;

            PRAGMA foreign_keys=on;
            """
        )
        conn.commit()
        print("document.ck_document_template now allows apside")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
