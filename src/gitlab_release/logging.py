"""loguru sink setup: level selection, --json serialize mode, non-TTY color detection,
secret redaction, and stdlib-logging interception. Configured once, called from cli.py
before anything else runs.
"""

from __future__ import annotations

import inspect
import logging
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loguru import Record

FMT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<cyan>{name}</cyan> - <level>{message}</level>"
)

REDACTED = "***REDACTED***"


def resolve_level(*, verbose: bool, quiet: bool) -> str:
    """DEBUG / INFO / WARNING. Raises ValueError if both flags are set; cli.py turns
    that into a click.UsageError (exit code 2) before this is ever called with a
    resolvable combination."""
    if verbose and quiet:
        raise ValueError("--verbose and --quiet are mutually exclusive")
    if verbose:
        return "DEBUG"
    if quiet:
        return "WARNING"
    return "INFO"


def redact_filter(secrets: Sequence[str]):  # type: ignore[no-untyped-def]
    """Public (not underscore-prefixed) so tests can exercise it directly without
    reaching into loguru's internal handler registry."""
    active = tuple(s for s in secrets if s)

    def _filter(record: Record) -> bool:
        for s in active:
            if s in record["message"]:
                record["message"] = record["message"].replace(s, REDACTED)
            for key, value in record["extra"].items():
                if isinstance(value, str) and s in value:
                    record["extra"][key] = value.replace(s, REDACTED)
        return True

    return _filter


class InterceptHandler(logging.Handler):
    """Routes stdlib `logging` records (python-gitlab, urllib3, etc.) into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def configure_logging(
    *,
    level: str,
    json_output: bool = False,
    verbose: bool = False,
    secrets: Sequence[str] = (),
) -> None:
    """Idempotent: always removes existing sinks first (skipping this double-logs
    every line). Safe to call twice - cli.py does exactly that: once at startup with
    secrets=() so early failures are visible, and again right after Settings is built,
    this time with the real secret values, before any business logic that might touch
    the token runs."""
    logger.remove()
    interactive = sys.stderr.isatty()
    logger.add(
        sys.stderr,
        level=level,
        format=FMT,
        colorize=interactive,
        backtrace=verbose,
        diagnose=verbose and interactive,
        serialize=json_output,
        filter=redact_filter(secrets),
    )
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
