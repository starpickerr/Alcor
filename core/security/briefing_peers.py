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
def briefing_peer_workflow(method, *, session=None):
    native = not current_delegation() and current_consumer_plugin() in {None, 'stella.briefing'}
    token = _WORKFLOW.set((plugin_user_id(), method, session) if native else None)
    try:
        yield
    finally:
        _WORKFLOW.reset(token)


def native_background_operation(descriptor, arguments):
    """Core's own workflow does not acquire a new conversation's default.

    A real Agent/task identity always keeps its own policy. The source-pinned
    owner and existing resource checks still constrain every business call.
    """
    from common.runtime_identity import current_identity
    from core.plugins.caller import current_caller_origin
    identity = current_identity()
    workflow = _WORKFLOW.get()
    if (not workflow or len(workflow) < 3 or workflow[2] is None
            or current_caller_origin() != 'job'
            or any((identity.session_id, identity.execution_session_id, identity.task_id, identity.run_id))
            or workflow[0] != plugin_user_id() or descriptor.source != 'plugin' or descriptor.filesystem
            or descriptor.operation not in {'read', 'write', 'delete'} or not isinstance(arguments, dict)):
        return False
    session = workflow[2]
    from core.security.runtime_trust import plugin_trusted
    if (session.plugin_id != 'stella.briefing' or session.user_id != workflow[0]
            or not plugin_trusted(session.plugin_root, session.plugin_id, session.package_digest, session.user_id)):
        return False
    session._check_runtime()
    method = arguments.get('method')
    delegation = current_delegation()
    if not delegation:
        return descriptor.capability_id == 'plugin:stella.briefing' and method == workflow[1]
    peer = descriptor.capability_id.removeprefix('plugin:')
    capability = _PEERS.get(peer, {}).get(method)
    if capability is None:
        return False
    return inherited_peer_ref(grant_ref=user_grant_ref(peer.removeprefix('stella.') + '-default-grant', workflow[0]),
        plugin_id=peer, user_id=workflow[0], consumer_plugin_id=current_consumer_plugin(),
        method=method, required_capabilities=[capability], purpose='background') is not None


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
