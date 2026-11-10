import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler

from core.log_redaction import redact as _redact


class _SecretFilter(logging.Filter):
    def filter(self, record):
        message = _redact(record.getMessage())
        record.msg = message
        record.args = ()
        if record.exc_info:
            record.exc_text = _redact(''.join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True


def _log_path():
    # Mirror config.get_data_root() without importing config (avoids a circular
    # import, since config imports this module). The desktop build sets
    # STELLA_DATA_DIR (e.g. ~/.stella); source deployments fall back to CWD.
    data_dir = os.environ.get("STELLA_DATA_DIR")
    if data_dir:
        data_dir = os.path.expanduser(data_dir)
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "run.log")
    return "run.log"


def _reset_logger(log):
    for handler in list(log.handlers):
        handler.close()
        log.removeHandler(handler)
        del handler
    log.handlers.clear()
    log.propagate = False
    if os.environ.get('STELLA_PLUGIN_ID'):
        from core.plugins.runner.diagnostics import DiagnosticHandler
        log.addHandler(DiagnosticHandler())
        return  # The host is the only file writer, including standalone mode.
    # Borrow stdout. Wrapping its buffer transfers ownership and garbage
    # collection during reconfiguration can close the application's stdout.
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    except (AttributeError, OSError, ValueError, TypeError):
        pass  # Custom/captured streams may not support encoding configuration.
    console_handle = logging.StreamHandler(sys.stdout)
    console_handle.addFilter(_SecretFilter())
    console_handle.setFormatter(
        logging.Formatter(
            "[%(levelname)s][%(asctime)s][%(filename)s:%(lineno)d] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    log.addHandler(console_handle)
    # The Electron shell persists the backend's stdout/stderr to run.log.
    # Writing the same record here would duplicate every packaged-app log.
    if os.environ.get("STELLA_LOG_MIRRORED_BY_SHELL") == "1":
        return
    # File logging is best-effort: if the log path isn't writable (e.g. a
    # packaged app installed under Program Files run by a non-admin user, with
    # an unwritable CWD), fall back to console-only instead of crashing the
    # whole process at import time.
    try:
        file_handle = RotatingFileHandler(_log_path(), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        file_handle.addFilter(_SecretFilter())
        file_handle.setFormatter(
            logging.Formatter(
                "[%(levelname)s][%(asctime)s][%(filename)s:%(lineno)d] - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        log.addHandler(file_handle)
    except OSError:
        console_handle.handle(
            logging.LogRecord(
                "log", logging.WARNING, __file__, 0,
                "[log] file logging disabled (log path not writable): %s",
                (_log_path(),), None,
            )
        )


def _get_logger():
    log = logging.getLogger("log")
    _reset_logger(log)
    log.setLevel(logging.INFO)
    return log


# 日志句柄
logger = _get_logger()


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the Stella logger, or a child that shares its handlers and redaction.

    Named loggers propagate to ``log`` so FileHandler / StreamHandler and
    ``_SecretFilter`` are applied once. Prefer this over bare ``logging.getLogger``
    for product diagnostics.
    """
    if not name or name == "log":
        return logger
    child = logging.getLogger(f"log.{name}")
    child.setLevel(logger.level)
    child.propagate = True
    return child
