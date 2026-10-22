"""Worker diagnostics cross the real channel; arbitrary stderr remains private."""
import io
import json
import logging

from common.log import logger


def test_real_worker_logs_and_failed_results_reach_host(tmp_path, caplog):
    from core.plugins.host import PluginSession
    from core.plugins.registry import PluginRegistry
    package = tmp_path / 'plugin'
    package.mkdir()
    (package / 'plugin.json').write_text(json.dumps({'manifestVersion': 1, 'id': 'stella.sample.logs', 'name': 'logs',
        'version': '1.0.0', 'hostApi': '^1.0.0', 'runtime': {'kind': 'python', 'entry': 'main.py', 'abi': 'cp312'},
        'permissions': [{'capability': 'sample.read', 'required': True}], 'contributes': {'services': ['stella.sample.logs@1']}}))
    (package / 'main.py').write_text('''import logging, sys
from common.log import logger
SERVICE_DESCRIPTORS = [{'contract':'stella.sample.logs@1','methods':[{'name':'run','effect':'query','permissions':['sample.read']}]}]
def handle_invoke(params):
    assert not any(isinstance(handler, logging.FileHandler) for handler in logger.handlers)
    logger.info('common worker diagnostic')
    print('RAW_STDERR_PRIVATE', file=sys.stderr)
    logging.getLogger('sample').info('phase ready token=PRIVATE_TOKEN')
    return {'status':'failed','sideEffects':'none','error':{'code':'invalid_argument','message':'BUSINESS_BODY_PRIVATE'}}
''')
    session = PluginSession(package, source_label='test', registry=PluginRegistry())
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger='log'):
            session.start()
            assert session.invoke('run')['status'] == 'failed'
            session.dispose()  # join stderr drain before inspecting records
        text = caplog.text
        assert 'phase ready' in text and '[Plugin:stella.sample.logs]' in text
        assert 'common worker diagnostic' in text
        assert 'invalid_argument' in text and 'run' in text
        assert 'PRIVATE_TOKEN' not in text and 'RAW_STDERR_PRIVATE' not in text and 'BUSINESS_BODY_PRIVATE' not in text
    finally:
        session.dispose()
        logger.removeHandler(caplog.handler)


def test_mcp_stderr_preserves_levels_and_redacts(caplog):
    from agent.tools.mcp.mcp_client import McpClient
    client = McpClient({'name': 'example'})
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger='log'):
            client._drain_stderr(io.StringIO('INFO:school:course ready\nERROR:school:token=SECRET\n'))
        records = [row for row in caplog.records if '[MCP:example]' in row.message]
        assert [row.levelname for row in records] == ['INFO', 'ERROR']
        assert 'SECRET' not in caplog.text
    finally:
        logger.removeHandler(caplog.handler)


def test_diagnostic_stream_caps_invalid_frames_and_handles_utf8_chunks(caplog):
    from core.plugins.diagnostics import DiagnosticStream
    stream = DiagnosticStream('stella.tasks')
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger='log'):
            stream.feed('untrusted raw\n' + 'x' * 100000 + '\n')
            frame = '@stella-log:' + json.dumps({'v': 1, 'level': 'WARNING', 'message': '中文 password=SECRET'}, ensure_ascii=False) + '\n'
            for char in frame:
                stream.feed(char)
        assert '中文' in caplog.text and 'SECRET' not in caplog.text and 'untrusted raw' not in caplog.text
        assert len(stream.pending) < 32768
    finally:
        logger.removeHandler(caplog.handler)


def test_mcp_failed_tool_records_summary_without_arguments_or_response_body(monkeypatch, caplog):
    import pytest
    from agent.tools.mcp.mcp_client import McpClient
    client = McpClient({'name': 'email'})
    # The network seam returns a real MCP error result; logging and exception
    # conversion remain production behavior.
    monkeypatch.setattr(client, '_send_request', lambda *args, **kwargs: {'result': {'isError': True,
        'content': [{'type': 'text', 'text': 'PRIVATE_RESPONSE_BODY'}]}})
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger='log'), pytest.raises(RuntimeError):
            client.call_tool_result('fetch_messages', {'password': 'PRIVATE_ARGUMENT'})
        assert '[MCP:email]' in caplog.text and 'fetch_messages' in caplog.text and 'failed' in caplog.text
        assert 'PRIVATE_RESPONSE_BODY' not in caplog.text and 'PRIVATE_ARGUMENT' not in caplog.text
    finally:
        logger.removeHandler(caplog.handler)
