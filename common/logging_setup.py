"""Logging configuration helpers for CLI entry points."""

from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def add_logging_cli_args(
    parser: argparse.ArgumentParser,
    *,
    default_level: str = "INFO",
    default_console: bool = True,
) -> None:
    """Add common logging flags to an argparse parser.

    Args:
        parser: ArgumentParser to extend.
        default_level: Default log level string.
        default_console: Enable console logging by default.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - Mutates the parser by adding flags.

    Contract:
        - Intended to be called once per parser instance.

    Failure Modes:
        - None.
    """
    parser.add_argument(
        "--log-level",
        default=default_level,
        help="logging level (DEBUG, INFO, WARNING, ERROR). default: INFO",
    )
    parser.add_argument(
        "--log-console",
        action=argparse.BooleanOptionalAction,
        default=default_console,
        help="enable console logging (default: enabled)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="optional log file path (RotatingFileHandler). default: disabled",
    )
    parser.add_argument(
        "--log-max-bytes",
        type=int,
        default=5_000_000,
        help="max size per log file before rotation (default: 5_000_000)",
    )
    parser.add_argument(
        "--log-backup-count",
        type=int,
        default=3,
        help="number of rotated log files to keep (default: 3)",
    )


def setup_logging(
    *,
    level: str = "INFO",
    console: bool = True,
    log_file: str | None = None,
    max_bytes: int = 5_000_000,
    backup_count: int = 3,
) -> None:
    """Configure root logging handlers for CLI usage.

    Args:
        level: Root log level string.
        console: Enable console logging when True.
        log_file: Optional log file path for rotation.
        max_bytes: Max bytes per log file before rotation.
        backup_count: Number of rotated log files to keep.

    Returns:
        None.

    Raises:
        OSError: If the log file directory cannot be created.

    Side Effects:
        - Resets root handlers and attaches new ones.
        - Creates log file directories when requested.

    Contract:
        - Intended for one-time initialization in a process.

    Failure Modes:
        - File I/O errors surface during handler setup.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())

    fmt = logging.Formatter(DEFAULT_LOG_FORMAT, datefmt=DEFAULT_DATE_FORMAT)

    # Reset handlers (CLI-style deterministic setup).
    for h in list(root.handlers):
        root.removeHandler(h)

    if console:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    if log_file:
        p = Path(str(log_file))
        p.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            p,
            maxBytes=int(max_bytes),
            backupCount=int(backup_count),
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)


def setup_logging_from_args(args: argparse.Namespace) -> None:
    """Configure logging from an argparse namespace.

    Args:
        args: Parsed CLI arguments with logging fields.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - Calls setup_logging with values from args.

    Contract:
        - Missing attributes fall back to defaults via getattr.

    Failure Modes:
        - None.
    """
    setup_logging(
        level=str(getattr(args, "log_level", "INFO")),
        console=bool(getattr(args, "log_console", True)),
        log_file=getattr(args, "log_file", None),
        max_bytes=int(getattr(args, "log_max_bytes", 5_000_000)),
        backup_count=int(getattr(args, "log_backup_count", 3)),
    )
