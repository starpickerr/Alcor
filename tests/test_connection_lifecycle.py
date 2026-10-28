"""Connection intent revokes current IO independently of capability permissions."""
import socket
import threading

import pytest

from common.runtime_identity import identity_scope


@pytest.fixture
def connections(tmp_path):
    from core.security.connections import ConnectionControl, ConnectionStore
    return ConnectionControl(store=ConnectionStore(tmp_path / 'security.sqlite'),
                             catalog=lambda: [dict(id='service:example', name='Example', owners=['mcp:example'], endpoint='https://example.invalid')])


def test_disabling_closes_real_socket_and_reopening_does_not_revive_lease(connections):
    left, right = socket.socketpair()
    outcome = []
    def cancel():
        left.shutdown(socket.SHUT_RDWR)
        left.close()
    lease = connections.begin('service:example', cancel=cancel)
    def read():
        try:
            outcome.append(left.recv(16))
        except OSError:
            outcome.append(b'closed')
    worker = threading.Thread(target=read)
    worker.start()
    try:
        disabled = connections.save('service:example', enabled=False, expected_revision=0)
        worker.join(2)
        assert not worker.is_alive() and outcome
        with pytest.raises(PermissionError):
            connections.begin('service:example')
        connections.save('service:example', enabled=True, expected_revision=disabled['revision'])
        with pytest.raises(PermissionError):
            lease.check()
        with connections.begin('service:example') as fresh:
            fresh.check()
    finally:
        right.close()
        lease.close()


def test_connection_edits_are_owner_only_revisioned_and_user_scoped(connections):
    with identity_scope(session_id='agent'), pytest.raises(PermissionError):
        connections.save('service:example', enabled=False, expected_revision=0)
    saved = connections.save('service:example', enabled=False, expected_revision=0)
    with pytest.raises(ValueError, match='revision'):
        connections.save('service:example', enabled=True, expected_revision=0)
    with identity_scope(user_id='other'):
        with connections.begin('service:example') as lease:
            lease.check()
    assert saved['enabled'] is False


def test_unknown_connections_are_not_persisted_and_begin_does_not_create_intent(connections):
    assert connections.store.rows('local-default') == []
    with connections.begin('service:example'):
        pass
    assert connections.store.rows('local-default') == []
    with pytest.raises(ValueError, match='Unknown'):
        connections.save('random-url', enabled=False, expected_revision=0)
    assert connections.store.rows('local-default') == []


def test_disable_racing_callback_registration_closes_late_io(connections):
    lease = connections.begin('service:example')
    connections.save('service:example', enabled=False, expected_revision=0)
    closed = []
    with pytest.raises(PermissionError):
        lease.attach(lambda: closed.append(True))
    assert closed == [True]


def test_remote_mcp_connection_switch_closes_generation_and_blocks_reconnect():
    from core.security.connections import ConnectionControl, service_id
    from agent.tools.mcp.mcp_client import McpClient
    from tests.fixtures.mcp_streamable_http_server import start_server
    server, url, stop = start_server()
    identifier, _ = service_id(url)
    control = ConnectionControl(catalog=lambda: [dict(id=identifier)])
    client = McpClient(dict(name='switchable', type='streamable-http', url=url))
    try:
        assert client.initialize()
        disabled = control.save(identifier, enabled=False, expected_revision=0)
        assert client._closed
        assert not McpClient(dict(name='switchable', type='streamable-http', url=url)).initialize()
        control.save(identifier, enabled=True, expected_revision=disabled['revision'])
        assert not client.initialize()
        fresh = McpClient(dict(name='switchable', type='streamable-http', url=url))
        try:
            assert fresh.initialize()
        finally:
            fresh.shutdown()
    finally:
        client.shutdown()
        stop()


def test_external_store_revocation_interrupts_existing_lease(connections):
    stopped = threading.Event()
    lease = connections.begin('service:example', cancel=stopped.set)
    try:
        # Simulates an authenticated second Core process writing the same DB.
        connections.store.save('local-default', 'service:example', False, 0)
        assert stopped.wait(2), 'Live IO must notice revocation by another Core process'
        with pytest.raises(PermissionError):
            lease.check()
    finally:
        lease.close()


def test_catalog_includes_implicit_model_endpoints_and_shared_service_owners(monkeypatch):
    from core.security.connections import connection_catalog
    from config import conf
    cfg = conf()
    for key in list(cfg):
        if key.endswith(('_api_key', '_api_base', '_base_url')):
            monkeypatch.delitem(cfg, key, raising=False)
    for name in ('open_ai', 'minimax', 'linkai', 'dashscope'):
        monkeypatch.setitem(cfg, name + '_api_key', 'synthetic-key')
    monkeypatch.setitem(cfg, 'moonshot_api_key', 'synthetic-moonshot-key')
    monkeypatch.setitem(cfg, 'moonshot_base_url', 'https://shared.invalid/v1')
    monkeypatch.setitem(cfg, 'ark_api_key', 'synthetic-ark-key')
    monkeypatch.setitem(cfg, 'ark_base_url', 'https://shared.invalid/v3')
    monkeypatch.setitem(cfg, 'custom_providers', [])
    from agent.tools.tool_manager import ToolManager
    monkeypatch.setattr(ToolManager, '_load_mcp_configs', lambda self: [])
    rows = {row['endpoint']: row for row in connection_catalog() if row['endpoint']}
    for endpoint in ('https://api.openai.com:443', 'https://api.minimaxi.com:443', 'https://api.link-ai.tech:443', 'https://dashscope.aliyuncs.com:443'):
        assert endpoint in rows
    assert rows['https://shared.invalid:443']['owners'] == ['model:doubao', 'model:moonshot']


def test_catalog_aggregates_actual_mailbox_endpoint_without_loading_password(tmp_path, monkeypatch):
    from core.security.connections import connection_catalog, service_id
    from core.services import email_facade
    from core.security.keychain import MacOSKeychain
    from tests.email_domain import EmailService
    service = EmailService(tmp_path / 'email.db')
    for user in ('alice', 'bob'):
        service.save_account({'action': 'add', 'host': 'imap.example.test', 'user': user,
                              'password': 'SYNTHETIC_PASSWORD'})
    monkeypatch.setattr(email_facade, '_paths', lambda: (service.db_path, tmp_path / 'legacy.json'))
    def forbidden(*args):
        raise AssertionError('Connection catalog must not decrypt mailbox credentials')
    monkeypatch.setattr(MacOSKeychain, 'get', forbidden)
    identifier, endpoint = service_id('imaps://imap.example.test')
    matches = [row for row in connection_catalog() if row['id'] == identifier]
    assert len(matches) == 1
    assert matches[0]['endpoint'] == endpoint
    assert matches[0]['configure_url'] == '/email'
    assert len(matches[0]['owners']) == 2
