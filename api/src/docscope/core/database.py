# api/src/docscope/core/database.py

from sqlalchemy import create_engine

from docscope.core.config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER

engine = create_engine(
    f"postgresql+psycopg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)
