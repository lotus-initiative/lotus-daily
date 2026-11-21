import argparse
import logging
from pathlib import Path

from daily_lotus.logging_config import (
    add_logging_args,
    configure_logging_from_args,
    default_log_path,
    setup_logging,
)


def test_default_log_path_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("LOTUS_LOG_DIR", raising=False)
    assert default_log_path("check_edits.log") == Path("logs/check_edits.log")

    monkeypatch.setenv("LOTUS_LOG_DIR", str(tmp_path))
    assert default_log_path("check_edits.log") == tmp_path / "check_edits.log"


def test_setup_logging_creates_parent_and_rotates(tmp_path: Path) -> None:
    log_path = tmp_path / "nested" / "bot.log"
    logger = setup_logging(
        name="test_rotation_logger",
        log_file=log_path,
        max_bytes=120,
        backup_count=2,
        level=logging.INFO,
        console=False,
    )

    assert log_path.parent.is_dir()
    for i in range(25):
        logger.info("log entry number %d with padding to trigger rotation", i)

    for handler in list(logger.handlers):
        handler.close()

    assert log_path.exists()
    assert (tmp_path / "nested" / "bot.log.1").exists()
    assert (tmp_path / "nested" / "bot.log.2").exists()
    assert not (tmp_path / "nested" / "bot.log.3").exists()


def test_setup_logging_no_duplicate_handlers_and_env_vars(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOTUS_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LOTUS_LOG_MAX_BYTES", "256")
    monkeypatch.setenv("LOTUS_LOG_BACKUP_COUNT", "3")

    log_path = tmp_path / "env.log"
    logger = setup_logging(name="test_reconfig_logger", log_file=log_path, console=True)
    logger = setup_logging(name="test_reconfig_logger", log_file=log_path, console=True)

    assert len(logger.handlers) == 2
    assert logger.level == logging.DEBUG

    logger.debug("debug line")
    for handler in list(logger.handlers):
        handler.close()

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "[DEBUG] test_reconfig_logger: debug line" in lines[0]


def test_logging_cli_args(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    add_logging_args(parser, "bot.log")

    args_no_file = parser.parse_args(["--no-file-log"])
    logger = configure_logging_from_args(args_no_file, "bot.log", name="test_cli_logger_1")
    assert len(logger.handlers) == 1
    for handler in list(logger.handlers):
        handler.close()

    custom_file = tmp_path / "custom.log"
    args_with_file = parser.parse_args(["--log-file", str(custom_file), "--max-log-bytes", "1024", "--log-backup-count", "1"])
    logger = configure_logging_from_args(args_with_file, "bot.log", name="test_cli_logger_2")
    assert len(logger.handlers) == 2
    logger.info("hello cli")
    for handler in list(logger.handlers):
        handler.close()
    assert "hello cli" in custom_file.read_text(encoding="utf-8")
