"""
src/utils/logging_config.py
────────────────────────────
Structured logging configuration using structlog.
All agents and utilities import get_logger() from here.

Branch: feature/01-project-setup
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog


def configure_logging(log_level: str = "INFO", log_format: str = "console") -> None:
    """
    Initialise structlog with the requested level and renderer.

    Parameters
    ----------
    log_level:
        Standard Python log-level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    log_format:
        ``"json"`` for machine-parseable output (CI / prod),
        ``"console"`` for human-readable coloured output (dev).
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if log_format == "json":
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Reduce noisy SQL parsing logs unless explicitly enabled.
    sqlglot_level = os.getenv("SQLGLOT_LOG_LEVEL", "CRITICAL").upper()
    sqlglot_logger = logging.getLogger("sqlglot")
    sqlglot_logger.setLevel(getattr(logging, sqlglot_level, logging.CRITICAL))

    suppress_sqlglot = os.getenv("SQLGLOT_LOG_SUPPRESS", "1") != "0"
    if suppress_sqlglot:
        class _SqlglotFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
                return not record.name.startswith("sqlglot")

        handler.addFilter(_SqlglotFilter())


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger for *name*."""
    return structlog.get_logger(name)
