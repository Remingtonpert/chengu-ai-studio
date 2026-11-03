"""Local rotating log setup."""

import logging
from logging.handlers import RotatingFileHandler

from app.core.paths import AppPaths


def configure_logging(paths: AppPaths) -> logging.Logger:
    paths.prepare_app_home()
    logger = logging.getLogger("chenguai")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(
            paths.log_file,
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    return logger