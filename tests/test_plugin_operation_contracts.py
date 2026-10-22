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
