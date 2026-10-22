"""First-party worker contracts classify each operation, including destructive methods."""
from pathlib import Path

import pytest


@pytest.mark.parametrize('domain', ['tasks', 'calendar', 'bills', 'briefing', 'automation', 'agent-hub', 'knowledge', 'memory'])
def test_real_worker_operations_are_explicit_and_match_effect(domain):
    from core.plugins.host import PluginSession
    from core.plugins.registry import PluginRegistry
    session = PluginSession(Path('stella-plugins-workspace/plugins') / domain,
                            source_label='operation-contract', registry=PluginRegistry())
    try:
        methods = [row for service in session.start()['prepare']['services'] for row in service['methods']]
        assert methods
        for method in methods:
            assert method['operation'] in {'read', 'write', 'delete', 'external_send', 'system_execute'}
            if method['operation'] == 'read':
                assert method['effect'] == 'query'
            if method['name'].startswith('delete_'):
                assert method['operation'] == 'delete'
            if method['name'] == 'provider_start':
                assert method['operation'] == 'system_execute'
    finally:
        session.dispose()


def test_dispatch_and_tool_operations_cannot_hide_destructive_or_execute_effects():
    from core.security.capabilities import describe_tool, method_operation
    assert describe_tool('todos', {'action': 'status'}).operation == 'write'
    assert describe_tool('agent_hub', {'action': 'submit'}).operation == 'system_execute'
    assert describe_tool('agent_hub', {'action': 'providers'}).operation == 'read'
    from core.plugins.host import PluginSession
    from core.plugins.registry import PluginRegistry
    session = PluginSession(Path('stella-plugins-workspace/plugins/knowledge'), source_label='test', registry=PluginRegistry())
    try:
        dispatch = next(row for service in session.start()['prepare']['services'] for row in service['methods'] if row['name'] == 'dispatch')
        assert method_operation(dispatch, {'action': 'delete_documents'}) == 'delete'
        assert method_operation(dispatch, {'action': 'create_document'}) == 'write'
        assert method_operation(dispatch, {'action': 'unrecognized'}) == 'unknown'
    finally:
        session.dispose()


def test_agent_cannot_reconfigure_host_runtime_with_credential_tool(tmp_path, monkeypatch):
    from agent.tools.env_config.env_config import EnvConfig
    monkeypatch.setenv('HOME', str(tmp_path))
    tool = EnvConfig()
    for key in ('STELLA_PLUGIN_RUNNER', 'STELLA_PLUGIN_CATALOG', 'PYTHONPATH', 'NODE_OPTIONS', 'LD_PRELOAD', 'PATH'):
        assert tool.execute({'action': 'set', 'key': key, 'value': '/untrusted'}) .status == 'error'
    from core.security.run_policy import from_legacy
    assert from_legacy({'preset': 'workspace-write'})['capabilities']['builtin:env_config']['write'] == 'confirm'
