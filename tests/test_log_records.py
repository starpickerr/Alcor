"""Log exploration reads bounded, redacted records without changing source logs."""
import json

import pytest


def line(level, message, second=0):
    return f'[{level}][2026-10-22 12:00:{second:02d}][service.py:10] - {message}\n'


def test_query_filters_sources_levels_chinese_and_multiline(tmp_path):
    from common.log_records import query_logs
    (tmp_path / 'run.log').write_text(line('INFO', '[Briefing] 简报完成') +
        line('ERROR', '[Plugin:stella.tasks] 创建事务失败', 1) + 'Traceback (most recent call last):\n  failed()\nValueError: invalid task\n' +
        line('WARNING', '[MCP:email] connection closed', 2))
    page = query_logs(tmp_path, query='事务', levels='ERROR', source='tasks')
    assert len(page['entries']) == 1
    entry = page['entries'][0]
    assert entry['level'] == 'ERROR' and entry['source'] == 'tasks'
    assert entry['component'] == 'plugin' and entry['location'] == 'service.py:10'
    assert 'failed()' in entry['detail'] and 'invalid task' in entry['detail']
    assert entry['message'] == '[Plugin:stella.tasks] 创建事务失败'
    assert query_logs(tmp_path, source='email')['entries'][0]['component'] == 'mcp'


def test_pages_are_stable_after_append_and_rotation(tmp_path):
    from common.log_records import query_logs
    path = tmp_path / 'run.log'
    path.write_text(''.join(line('INFO', f'[Agent] event {i}', i) for i in range(5)))
    first = query_logs(tmp_path, limit=2)
    assert [r['message'] for r in first['entries']] == ['[Agent] event 4', '[Agent] event 3']
    with path.open('a') as stream:
        stream.write(line('ERROR', '[Agent] newly appended', 6))
    path.rename(tmp_path / 'run.log.1')
    path.write_text(line('INFO', '[Agent] next file', 7))
    second = query_logs(tmp_path, cursor=first['next_cursor'], limit=2)
    assert [r['message'] for r in second['entries']] == ['[Agent] event 2', '[Agent] event 1']
    assert not set(r['id'] for r in first['entries']) & set(r['id'] for r in second['entries'])


def test_redaction_applies_to_old_files_detail_and_search(tmp_path):
    from common.log_records import query_logs
    (tmp_path / 'run.log').write_text(line('ERROR', '[Core] token=supersecret') + '  Authorization: Bearer private\n')
    page = query_logs(tmp_path)
    assert 'supersecret' not in json.dumps(page) and 'private' not in json.dumps(page)
    assert query_logs(tmp_path, query='supersecret')['entries'] == []


def test_structured_shell_records_preserve_metadata_and_timestamp(tmp_path):
    from common.log_records import query_logs
    (tmp_path / 'run.log').write_text(json.dumps({'level': 'ERROR', 'timestamp': '2026-10-22T12:00:00Z',
        'source': 'renderer', 'component': 'desktop', 'message': 'Failed to load', 'location': 'App.tsx:20'}) + '\n')
    entry = query_logs(tmp_path)['entries'][0]
    assert entry['source'] == 'renderer' and entry['component'] == 'desktop'
    assert entry['timestamp'] == '2026-10-22T12:00:00Z'
    assert query_logs(tmp_path, since='2026-10-23T00:00:00Z')['entries'] == []


def test_structured_messages_are_decoded_before_redacting_quoted_secrets(tmp_path):
    from common.log_records import query_logs
    (tmp_path / 'run.log').write_text(json.dumps({'level': 'ERROR', 'source': 'renderer',
        'message': 'password="several secret words"', 'detail': 'token="another private phrase"'}) + '\n')
    page = query_logs(tmp_path)
    text = json.dumps(page)
    assert 'secret words' not in text and 'private phrase' not in text
    assert page['entries'][0]['source'] == 'renderer'


def test_long_structured_entry_keeps_source_before_truncating_details(tmp_path):
    from common.log_records import query_logs
    (tmp_path / 'run.log').write_text(json.dumps({'level': 'ERROR', 'source': 'renderer',
        'message': '中' * 16000, 'location': 'App.tsx:1'}) + '\n')
    entry = query_logs(tmp_path)['entries'][0]
    assert entry['source'] == 'renderer' and entry['level'] == 'ERROR'
    assert len(entry['detail']) <= 16384


def test_scan_bounds_and_partial_last_line(tmp_path, monkeypatch):
    from common import log_records
    monkeypatch.setattr(log_records, 'MAX_SCAN_BYTES', 1000)
    path = tmp_path / 'run.log'
    path.write_text(''.join(line('INFO', '[Agent] ' + '中' * 40, i % 60) for i in range(100)) + '[ERROR] partial')
    page = log_records.query_logs(tmp_path)
    assert page['truncated'] is True
    assert page['scanned_bytes'] <= 1000
    assert all('partial' not in entry['message'] for entry in page['entries'])
    assert page['entries']


@pytest.mark.parametrize('options', [{'limit': 0}, {'limit': 201}, {'levels': 'FAKE'}, {'cursor': '../../run.log'},
                                    {'since': 'yesterday'}, {'query': 'x' * 501}])
def test_invalid_queries_are_rejected(tmp_path, options):
    from common.log_records import query_logs
    with pytest.raises(ValueError):
        query_logs(tmp_path, **options)


def test_missing_files_symlinks_and_expired_cursor(tmp_path):
    from common.log_records import query_logs
    assert query_logs(tmp_path)['entries'] == []
    outside = tmp_path / 'private'
    outside.write_text(line('INFO', 'private data'))
    (tmp_path / 'run.log').symlink_to(outside)
    assert query_logs(tmp_path)['entries'] == []
    (tmp_path / 'run.log').unlink()
    (tmp_path / 'run.log').write_text(line('INFO', 'one') + line('INFO', 'two', 1))
    cursor = query_logs(tmp_path, limit=1)['next_cursor']
    (tmp_path / 'run.log').unlink()
    with pytest.raises(ValueError, match='history'):
        query_logs(tmp_path, cursor=cursor)


@pytest.mark.parametrize('structured', [True, False])
def test_oversized_structured_secret_is_never_exposed(tmp_path, structured):
    from common.log_records import query_logs
    message = 'password="first PRIVATE phrase ' + 'x' * 200000 + '"'
    data = json.dumps({'level': 'ERROR', 'message': message}) + '\n' if structured else line('ERROR', message)
    (tmp_path / 'run.log').write_text(data)
    assert 'PRIVATE' not in json.dumps(query_logs(tmp_path))
    assert query_logs(tmp_path, query='PRIVATE')['entries'] == []


def test_window_boundary_does_not_split_traceback(tmp_path, monkeypatch):
    from common import log_records
    older = line('ERROR', 'original failure') + 'Traceback (most recent call last):\n' + '  中文失败()\n' * 20 + 'ValueError: bad\n'
    newest = line('INFO', 'newest') + '  ' + '中' * 100 + '\n'
    (tmp_path / 'run.log').write_text(older + newest)
    boundary = len((line('ERROR', 'original failure') + 'Traceback (most recent call last):\n').encode()) + 3
    monkeypatch.setattr(log_records, 'MAX_SCAN_BYTES', len((older + newest).encode()) - boundary)
    first = log_records.query_logs(tmp_path)
    assert [row['message'] for row in first['entries']] == ['newest']
    second = log_records.query_logs(tmp_path, cursor=first['next_cursor'])
    assert len(second['entries']) == 1
    assert second['entries'][0]['level'] == 'ERROR'
    assert 'ValueError: bad' in second['entries'][0]['detail']


@pytest.mark.parametrize('ending', ['\n', ''])
def test_oversized_line_keeps_older_history_reachable(tmp_path, monkeypatch, ending):
    from common import log_records
    monkeypatch.setattr(log_records, 'MAX_SCAN_BYTES', 1024)
    (tmp_path / 'run.log').write_text(line('INFO', 'older') + 'x' * 2049 + ending)
    cursor, messages = '', []
    for _ in range(5):
        page = log_records.query_logs(tmp_path, cursor=cursor)
        messages.extend(row['message'] for row in page['entries'])
        if not page['next_cursor']:
            break
        assert page['next_cursor'] != cursor
        cursor = page['next_cursor']
    assert 'older' in messages


@pytest.mark.skipif(__import__('os').name == 'nt', reason='POSIX FIFO')
def test_fifo_is_skipped_without_blocking(tmp_path):
    import os
    import subprocess
    import sys
    os.mkfifo(tmp_path / 'run.log')
    result = subprocess.run([sys.executable, '-c',
        'from common.log_records import query_logs; import sys; assert query_logs(sys.argv[1])["entries"] == []', str(tmp_path)],
        capture_output=True, timeout=3)
    assert result.returncode == 0, result.stderr
