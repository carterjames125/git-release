import json
import logging

import pytest
from loguru import logger

from gitlab_release.logging import (
    InterceptHandler,
    configure_logging,
    redact_filter,
    resolve_level,
)


@pytest.mark.parametrize(
    ("verbose", "quiet", "expected"),
    [
        (True, False, "DEBUG"),
        (False, True, "WARNING"),
        (False, False, "INFO"),
    ],
)
def test_resolve_level(verbose: bool, quiet: bool, expected: str) -> None:
    assert resolve_level(verbose=verbose, quiet=quiet) == expected


def test_resolve_level_rejects_verbose_and_quiet() -> None:
    with pytest.raises(ValueError):
        resolve_level(verbose=True, quiet=True)


def test_redact_filter_scrubs_message_and_extra() -> None:
    filt = redact_filter(["s3cr3t"])
    record = {"message": "token is s3cr3t here", "extra": {"token": "s3cr3t", "other": "keep"}}

    assert filt(record) is True
    assert "s3cr3t" not in record["message"]
    assert "***REDACTED***" in record["message"]
    assert record["extra"]["token"] == "***REDACTED***"
    assert record["extra"]["other"] == "keep"


def test_redact_filter_noop_with_no_secrets() -> None:
    filt = redact_filter(())
    record = {"message": "nothing sensitive here", "extra": {}}

    assert filt(record) is True
    assert record["message"] == "nothing sensitive here"


def test_intercept_handler_routes_to_loguru() -> None:
    captured = []
    sink_id = logger.add(captured.append, level=0)
    try:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello from stdlib",
            args=(),
            exc_info=None,
        )
        InterceptHandler().emit(record)
    finally:
        logger.remove(sink_id)

    assert any("hello from stdlib" in r.record["message"] for r in captured)


def test_configure_logging_twice_does_not_double_log(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO")
    configure_logging(level="INFO")

    captured = []
    sink_id = logger.add(captured.append, level=0)
    try:
        logger.info("one line only")
    finally:
        logger.remove(sink_id)

    assert len(captured) == 1


def test_configure_logging_json_output_is_valid_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", json_output=True)
    logger.info("json line")
    captured = capsys.readouterr()

    line = captured.err.strip().splitlines()[-1]
    json.loads(line)
