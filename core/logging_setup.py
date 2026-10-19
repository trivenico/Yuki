"""Local rotating logs with conservative, content-free event helpers."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re


LOGGER_NAME = "yuki"
_MAX_LOG_BYTES = 1_000_000
_BACKUP_COUNT = 3


def _safe_component(value, limit=120):
    text = str(value)
    text = re.sub(r"[\r\n\x00-\x1f\x7f]+", " ", text)
    return text[:limit]


def configure_logging(log_directory):
    """Configure UTF-8 local logs; never log prompts or action arguments here."""
    directory = Path(log_directory).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    target = (directory / "yuki.log").resolve()
    for handler in list(logger.handlers):
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename).resolve() == target:
            return logger
        logger.removeHandler(handler)
        handler.close()

    handler = RotatingFileHandler(
        target, maxBytes=_MAX_LOG_BYTES, backupCount=_BACKUP_COUNT,
        encoding="utf-8", delay=True,
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    logger.addHandler(handler)
    return logger


def log_event(logger, level, event, *, component="runtime", error_type=None):
    """Write bounded metadata only; callers must not pass prompts or arguments."""
    safe_event = _safe_component(event)
    safe_component_name = _safe_component(component, 60)
    parts = [f"event={safe_event!r}", f"component={safe_component_name!r}"]
    if error_type is not None:
        parts.append(f"error_type={_safe_component(error_type, 100)!r}")
    logger.log(level, " ".join(parts))
