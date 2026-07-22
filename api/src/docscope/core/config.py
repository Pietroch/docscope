# api/src/docscope/core/config.py

import os

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

# Optional - typed with default
DB_PATH = os.environ.get("DB_PATH", "/app/data/docscope.db")
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Read-only mount point for the external documents drive (never written to)
DOCUMENTS_DIR = os.environ.get("DOCUMENTS_DIR", "/data/documents")
