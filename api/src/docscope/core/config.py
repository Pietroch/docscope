# api/src/docscope/core/config.py

import os

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

# Required - raise KeyError if missing
DB_HOST = os.environ["DB_HOST"]
DB_PORT = os.environ["DB_PORT"]
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]

# Optional - typed with default
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Read-only mount point for the external documents drive (never written to)
DOCUMENTS_DIR = os.environ.get("DOCUMENTS_DIR", "/data/documents")
