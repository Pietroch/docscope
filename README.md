<!-- README.md -->

# docscope

Store, index and analyze documents: extract text from PDFs, persist the
extracted data in PostgreSQL, and generate Excel reports.

## Stack

- **api** — FastAPI (Python), package `docscope`
- **db** — PostgreSQL 16
- **client** — static HTML/JS (prototype, no framework yet), served by nginx

See [docs/architecture.md](docs/architecture.md) for the service map and the
external documents constraint.

## Quickstart

```bash
cp .env.example .env
# fill in DB_PASSWORD, SECRET_KEY and DOCUMENTS_DIR (absolute host path to
# the external documents folder, mounted read-only into api)

make up
make sh      # shell into the api container
```

API available at http://localhost:8000 (docs at `/docs`), client at
http://localhost:3000.

## Development

VSCode: reopen in container (`.devcontainer/`) — points to the `api` service,
no local `.venv` needed.

```bash
make logs    # tail all services
make test    # run pytest inside api
make lint    # ruff check
```
