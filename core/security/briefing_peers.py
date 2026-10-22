"""Owner-policy inheritance for Core's native Briefing action workflows only.

The gateway binds the root operation before entering the worker. Only its first
Tasks/Calendar callback may reuse an owner default, subject to live policy checks
in PluginAuthorizationStore. Explicit consumer policy always takes precedence.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from core.plugins.caller import current_consumer_plugin, current_delegation, plugin_user_id, user_grant_ref

_WORKFLOW = ContextVar('native_briefing_peer_workflow', default=None)
_VIEWS = frozenset({'get_briefing', 'get_revision', 'get_overview', 'complete_briefing', 'run_pipeline'})
_PEERS = {
    'stella.tasks': {
        'get_todo': 'tasks.read', 'list_todos': 'tasks.read',
        'find_briefing_sources': 'tasks.read', 'has_source_tombstone': 'tasks.read',
        'create_todo': 'tasks.write', 'update_todo': 'tasks.write', 'sync_briefing_facts': 'tasks.write',
    },
    'stella.calendar': {
        'get_event': 'calendar.read', 'list_events': 'calendar.read',
        'find_briefing_sources': 'calendar.read', 'has_source_tombstone': 'calendar.read',
        'create_event': 'calendar.write', 'sync_briefing_facts': 'calendar.write',
    },
}


@contextmanager
def briefing_peer_workflow(method):
    native = not current_delegation() and current_consumer_plugin() in {None, 'stella.briefing'}
    token = _WORKFLOW.set((plugin_user_id(), method) if native else None)
    try:
        yield
    finally:
        _WORKFLOW.reset(token)


def inherited_peer_ref(*, grant_ref, plugin_id, user_id, consumer_plugin_id, method,
                       required_capabilities, purpose):
    workflow = _WORKFLOW.get()
    delegation = current_delegation() or {}
    capability = _PEERS.get(plugin_id, {}).get(method)
    if (not workflow or workflow[0] != user_id or plugin_user_id() != user_id
            or not capability or set(required_capabilities) != {capability}
            or purpose not in {'interactive', 'background'}
            or consumer_plugin_id != 'stella.briefing' or current_consumer_plugin() != consumer_plugin_id
            or delegation.get('chain') != ['stella.briefing']
            or delegation.get('callerPluginId') != 'stella.briefing'
            or delegation.get('context', {}).get('grantRef') != user_grant_ref('briefing-default-grant', user_id)
            or grant_ref != user_grant_ref(plugin_id.removeprefix('stella.') + '-default-grant', user_id)):
        return None
    root_method = workflow[1]
    if root_method in _VIEWS:
        allowed = method in {'get_todo', 'get_event'}
    elif root_method == 'get_action_targets':
        allowed = method in {'list_todos', 'list_events'}
    else:
        allowed = root_method in {'update_action', 'dispatch_outbox'}
    if not allowed:
        return None
    return f'{grant_ref}:{purpose}:{capability}'
