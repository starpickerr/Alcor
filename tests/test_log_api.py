"""The native log API authenticates before reading and never accepts a path."""
import json
from unittest.mock import patch

import pytest

from channel.desktop import desktop_channel as desktop


def test_logs_endpoint_filters_and_rejects_invalid_cursor(tmp_path, monkeypatch):
    (tmp_path / 'run.log').write_text('[ERROR][2026-10-22 12:00:00][tasks.py:3] - [Todos] task failed token=SECRET\n')
    monkeypatch.setattr(desktop, 'get_data_root', lambda: str(tmp_path))
    with patch.object(desktop, '_require_auth'), patch.object(desktop.web, 'header'):
        with patch.object(desktop.web, 'input', return_value=desktop.web.storage(
                q='task', levels='ERROR', source='tasks', since='', cursor='', limit='10')):
            result = json.loads(desktop.LogsEntriesHandler().GET())
        assert result['status'] == 'success' and len(result['entries']) == 1
        assert 'SECRET' not in json.dumps(result)
        with patch.object(desktop.web, 'input', return_value=desktop.web.storage(
                q='', levels='', source='', since='', cursor='../../private', limit='10')):
            assert json.loads(desktop.LogsEntriesHandler().GET())['status'] == 'error'


@pytest.mark.parametrize('handler', [desktop.LogsEntriesHandler, desktop.LogsDownloadHandler, desktop.LogsHandler])
def test_all_log_endpoints_require_authentication(handler):
    with patch.object(desktop, '_require_auth', side_effect=PermissionError('unauthenticated')):
        with pytest.raises(PermissionError, match='unauthenticated'):
            handler().GET()


def test_legacy_download_redacts_existing_logs(tmp_path, monkeypatch):
    (tmp_path / 'run.log').write_text('password=SECRET\n')
    monkeypatch.setattr(desktop, 'get_data_root', lambda: str(tmp_path))
    with patch.object(desktop, '_require_auth'), patch.object(desktop.web, 'header'):
        assert desktop.LogsDownloadHandler().GET() == b'password=[REDACTED]\n'
