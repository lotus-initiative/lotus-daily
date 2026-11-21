import argparse
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_LOG_DIR = Path("logs")
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5
DEFAULT_LOG_LEVEL = "INFO"


def default_log_path(filename: str) -> Path:
    """Return the default log file path using ``LOTUS_LOG_DIR`` or ``logs``.

    Parameters
    ----------
    filename : str
        Log file name.

    Returns
    -------
    Path
        Full path to the log file.
    """
    log_dir = os.getenv("LOTUS_LOG_DIR") or str(DEFAULT_LOG_DIR)
    return Path(log_dir) / filename


def _resolve_positive_int(value: int | None, env_var: str, default: int, *, minimum: int = 0) -> int:
    if value is not None:
        return value if value >= minimum else default
    raw = os.getenv(env_var)
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed >= minimum else default


def _resolve_level(level: int | str | None) -> int:
    candidate = level if level is not None else (os.getenv("LOTUS_LOG_LEVEL") or DEFAULT_LOG_LEVEL)
    if isinstance(candidate, int):
        return candidate
    numeric = logging.getLevelName(candidate.strip().upper())
    return numeric if isinstance(numeric, int) else logging.INFO


def _reset_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def setup_logging(
    name: str = "daily_lotus",
    log_file: Path | str | None = None,
    *,
    max_bytes: int | None = None,
    backup_count: int | None = None,
    level: int | str | None = None,
    console: bool = True,
) -> logging.Logger:
    """Configure a logger with optional console and rotating file output.

    Parameters
    ----------
    name : str
        Logger name. Defaults to ``"daily_lotus"``.
    log_file : Path | str | None
        Optional path to a log file. Parent directories are created automatically.
    max_bytes : int | None
        Maximum size in bytes before log rotation. Defaults to ``LOTUS_LOG_MAX_BYTES`` or 5 MiB.
    backup_count : int | None
        Number of rotated backup files to retain. Defaults to ``LOTUS_LOG_BACKUP_COUNT`` or 5.
    level : int | str | None
        Logging level. Defaults to ``LOTUS_LOG_LEVEL`` or ``"INFO"``.
    console : bool
        Whether to attach a stdout stream handler.

    Returns
    -------
    logging.Logger
        Configured logger instance.
    """
    resolved_level = _resolve_level(level)
    resolved_max_bytes = _resolve_positive_int(max_bytes, "LOTUS_LOG_MAX_BYTES", DEFAULT_MAX_BYTES, minimum=1)
    resolved_backup_count = _resolve_positive_int(backup_count, "LOTUS_LOG_BACKUP_COUNT", DEFAULT_BACKUP_COUNT, minimum=0)

    logger = logging.getLogger(name)
    _reset_handlers(logger)
    logger.setLevel(resolved_level)
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
    formatter.converter = time.gmtime

    if console:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(resolved_level)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    if log_file is not None:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=resolved_max_bytes,
            backupCount=resolved_backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(resolved_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def add_logging_args(parser: argparse.ArgumentParser, default_filename: str) -> None:
    """Register standard logging CLI options on an argument parser.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        Target argument parser.
    default_filename : str
        Default log file name under ``LOTUS_LOG_DIR`` / ``logs``.
    """
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help=f"Path to the rotating log file (default: {DEFAULT_LOG_DIR / default_filename}).",
    )
    parser.add_argument(
        "--no-file-log",
        action="store_true",
        help="Disable writing logs to a file (console only).",
    )
    parser.add_argument(
        "--max-log-bytes",
        type=int,
        default=None,
        help="Maximum log file size in bytes before rotation.",
    )
    parser.add_argument(
        "--log-backup-count",
        type=int,
        default=None,
        help="Number of rotated log backup files to retain.",
    )


def configure_logging_from_args(
    args: argparse.Namespace,
    default_filename: str,
    *,
    name: str = "daily_lotus",
) -> logging.Logger:
    """Configure logging from parsed CLI arguments.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed command-line arguments from :func:`add_logging_args`.
    default_filename : str
        Default log file name when ``--log-file`` is not supplied.
    name : str
        Logger name to configure.

    Returns
    -------
    logging.Logger
        Configured logger instance.
    """
    log_file: Path | None = None
    if not getattr(args, "no_file_log", False):
        log_file = getattr(args, "log_file", None) or default_log_path(default_filename)
    return setup_logging(
        name=name,
        log_file=log_file,
        max_bytes=getattr(args, "max_log_bytes", None),
        backup_count=getattr(args, "log_backup_count", None),
    )
