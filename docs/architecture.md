<!-- docs/architecture.md -->

# Architecture — docscope

## Purpose

Store, index and analyze documents: extract text from PDFs, persist the extracted
data (never the source files) in SQLite, and generate Excel reports.

## Services

| Service | Role | Tech | Port |
|---|---|---|---|
| `api` | REST API | FastAPI (package `docscope`) | 8000 |
| `client` | Static frontend | HTML/JS served by nginx | 3000 |

SQLite is a file, not a service: the database lives at `DB_PATH`, in
`./api/data` bind-mounted into `api` only.

`client` has no `depends_on` on `api`: it compiles/serves independently, CORS is
configured on the `api` side.

## Key constraint — external documents

Source documents live on an external drive/folder outside the project. That
folder is bind-mounted **read-only** into the `api` container at
`/data/documents` (`DOCUMENTS_DIR` in `.env`).

- Documents are **never** copied into application storage (no named volume,
  no object storage).
- Only the data extracted from them — text, metadata, analysis results — is
  persisted in SQLite.

## Data flow

```
external drive (read-only) --> api (pdfplumber: text extraction)
                                 |
                                 v
                         SQLite (extracted data)
                                 |
                                 v
                    api (openpyxl: report generation) --> Excel export
```

## Frontend

Prototype stage: static HTML/JS consuming the API directly, no build step.
Can be replaced later by a React/Vue SPA without changing the `api`/`db`
services.
