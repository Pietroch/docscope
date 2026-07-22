# api/src/docscope/core/logging.py

import logging

from docscope.core.config import LOG_LEVEL


def get_logger(name: str) -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return logging.getLogger(name)
