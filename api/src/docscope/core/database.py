# api/src/docscope/core/database.py

from sqlalchemy import create_engine

from docscope.core.config import DB_PATH

# check_same_thread=False: FastAPI can serve a single SQLite connection
# from different worker threads.
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
