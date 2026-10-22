"""Unit tests for common.log helpers: redaction and get_logger."""
import logging

from common.log import _SecretFilter, _redact, get_logger, logger


def test_redact_masks_field_values_and_bearer():
    text = 'Authorization: Bearer TOPSECRET, password=TOPSECRET, api_key="AKIASECRET"'
    redacted = _redact(text)
    assert "TOPSECRET" not in redacted
    assert "AKIASECRET" not in redacted
    assert "[REDACTED]" in redacted
    assert _redact("prefix Bearer TOPSECRET suffix") == "prefix Bearer [REDACTED] suffix"


def test_redact_masks_standalone_sk_tokens():
    text = "model reply used key sk-abcdefghijklmnopqrstuvwxyz0123456789"
    redacted = _redact(text)
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in redacted
    assert "sk-[REDACTED]" in redacted


def test_secret_filter_rewrites_log_record():
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1,
        "Authorization: Bearer TOPSECRET, password=TOPSECRET", (), None,
    )
    assert _SecretFilter().filter(record)
    assert "TOPSECRET" not in record.getMessage()


def test_get_logger_shares_handlers_and_redacts(caplog):
    named = get_logger("phase2-probe")
    assert named is not logger
    assert named.name == "log.phase2-probe"

    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level("INFO", logger="log"):
            named.info("token=%s", "LEAKED_SECRET_VALUE")
    finally:
        logger.removeHandler(caplog.handler)

    assert "LEAKED_SECRET_VALUE" not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_get_logger_none_returns_root_logger():
    assert get_logger() is logger
    assert get_logger("log") is logger


def test_redaction_masks_quoted_secrets_and_url_credentials():
    redacted = _redact('password="two word secret" url=https://example.test/cb?code=PRIVATE&token=ALSO_PRIVATE')
    assert 'two word secret' not in redacted and 'PRIVATE' not in redacted


def test_stdout_unicode_under_ascii_environment(tmp_path):
    import os
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-c',
        'from common.log import logger, _reset_logger; import sys; _reset_logger(logger); '
        'logger.info("中文日志"); assert not sys.stdout.closed'],
        env={**os.environ, 'PYTHONIOENCODING': 'ascii', 'STELLA_DATA_DIR': str(tmp_path)}, capture_output=True)
    assert '中文日志' in result.stdout.decode('utf-8')
    assert not result.stderr


def test_standalone_file_logging_rotates_and_shell_mode_has_no_file_writer(tmp_path, monkeypatch):
    from common.log import _reset_logger
    from logging.handlers import RotatingFileHandler
    isolated = logging.Logger('isolated-log')
    monkeypatch.setenv('STELLA_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('STELLA_LOG_MIRRORED_BY_SHELL', raising=False)
    _reset_logger(isolated)
    try:
        file_handler = next(handler for handler in isolated.handlers if isinstance(handler, RotatingFileHandler))
        file_handler.maxBytes = 150
        for i in range(10):
            isolated.warning('event %s password=SECRET', i)
        assert (tmp_path / 'run.log.1').exists()
        assert 'SECRET' not in (tmp_path / 'run.log.1').read_text()
        monkeypatch.setenv('STELLA_LOG_MIRRORED_BY_SHELL', '1')
        _reset_logger(isolated)
        assert not any(isinstance(handler, logging.FileHandler) for handler in isolated.handlers)
    finally:
        for handler in isolated.handlers:
            handler.close()
