"""Fault injection at service construction, using the real RPC and entries."""
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'packages/alcor-secretary'))
import business.worker as runtime
from business.briefing.domain.actions import PeerOutcomeUnknown


def unavailable(_):
    if _['method'] == 'get_preferences':
        raise PeerOutcomeUnknown('private-server-secret')
    raise sqlite3.OperationalError('private-server-secret')


class Business(runtime.Business):
    def __init__(self, root):
        super().__init__(root)
        for entry in self.entries.values():
            entry._service = unavailable


runtime.Business = Business
runtime.main()
