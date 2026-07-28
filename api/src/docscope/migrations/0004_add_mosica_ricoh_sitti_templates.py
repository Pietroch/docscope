# api/src/docscope/migrations/0004_add_mosica_ricoh_sitti_templates.py
#
# One-off migration: widens the `ck_document_template` CHECK constraint to
# accept "mosica"/"ricoh"/"sitti" alongside "ucm"/"delvaux"/"apside". Same
# table-rebuild approach as 0002 (SQLite has no ALTER TABLE for CHECK
# constraints), this time also carrying over the `validated` column added
# by 0003. Idempotent: skipped if the constraint already allows "ricoh".
#
# Run inside the api container: python src/docscope/migrations/0004_add_mosica_ricoh_sitti_templates.py

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
        if "'ricoh'" in row[0]:
            print("skip: ricoh already allowed")
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
                validated BOOLEAN NOT NULL DEFAULT 0,
                PRIMARY KEY (id),
                CONSTRAINT ck_document_template CHECK (
                    template IN ('ucm', 'delvaux', 'apside', 'mosica', 'ricoh', 'sitti')
                ),
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
        print("document.ck_document_template now allows mosica/ricoh/sitti")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
