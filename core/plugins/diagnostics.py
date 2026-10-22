"""Validate worker diagnostics; never forward arbitrary worker stderr or payloads."""
import json
import logging

from common.log import logger

MAX_FRAME = 32768


class DiagnosticStream:
    def __init__(self, plugin_id):
        self.plugin_id = plugin_id
        self.pending = ''
        self.dropping = False

    def feed(self, text):
        for part in text.splitlines(keepends=True):
            ended = part.endswith('\n')
            if not self.dropping:
                if len(self.pending) + len(part) > MAX_FRAME:
                    self.pending, self.dropping = '', True
                else:
                    self.pending += part
            if ended:
                if not self.dropping:
                    self._emit(self.pending.rstrip('\r\n'))
                self.pending, self.dropping = '', False

    def _emit(self, line):
        if not line.startswith('@stella-log:'):
            return
        try:
            row = json.loads(line[len('@stella-log:'):])
            if (not isinstance(row, dict) or row.get('v') != 1 or set(row) != {'v', 'level', 'message'}
                    or row.get('level') not in {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}
                    or not isinstance(row.get('message'), str)):
                return
            logger.log(getattr(logging, row['level']), '[Plugin:%s] %s', self.plugin_id, row['message'][:16384])
        except (ValueError, TypeError):
            return
