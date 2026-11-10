"""Versioned, bounded diagnostics on stderr; stdout remains the RPC transport."""
import json
import logging
import sys

from core.log_redaction import redact


class DiagnosticHandler(logging.Handler):
    def emit(self, record):
        try:
            message = self.format(record)
            frame = {'v': 1, 'level': record.levelname, 'message': redact(message)[:12000]}
            sys.stderr.write('@stella-log:' + json.dumps(frame, ensure_ascii=False) + '\n')
            sys.stderr.flush()
        except Exception:
            pass  # Diagnostics must not change a business outcome.


def configure():
    handler = DiagnosticHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
