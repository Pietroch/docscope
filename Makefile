# Makefile

.PHONY: up down build logs sh shell test lint client-sh help

up:         ## Start the stack
	docker compose up -d

down:       ## Stop the stack
	docker compose down

build:      ## Rebuild images
	docker compose build

logs:       ## Tail logs
	docker compose logs -f

sh:         ## Shell into the api container
	docker compose exec api bash

shell:      ## Open a Python REPL in the api container
	docker compose exec api python

test:       ## Run tests
	docker compose exec api pytest

lint:       ## Lint code
	docker compose exec api ruff check .

client-sh:  ## Shell into the client container
	docker compose exec client sh

help:       ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'
