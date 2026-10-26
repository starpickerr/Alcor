"""Trusted runtime descriptions. Wire annotations and model parameters grant nothing."""
from dataclasses import dataclass
from threading import RLock

from core.security.run_policy import OPERATIONS


@dataclass(frozen=True)
class CapabilityDescriptor:
    capability_id: str
    source: str
    operation: str
    filesystem: bool = False


_registered = {}
_lock = RLock()
_BUSINESS = {'todos': 'tasks', 'calendar': 'calendar', 'bills': 'bills', 'briefing': 'briefing',
             'scheduler': 'automation', 'agent_hub': 'agent-hub', 'memory': 'memory', 'knowledge': 'knowledge'}
_READ = {'list', 'get', 'status', 'search', 'view', 'today', 'upcoming', 'rules', 'sources', 'history', 'categories',
         'list_rules', 'get_preferences', 'list_files', 'get_content', 'read', 'query', 'overview'}
_WRITE = {'create', 'update', 'complete', 'status_update', 'add', 'run', 'item_status', 'add_rule', 'update_rule',
          'decide', 'set_preferences', 'enable', 'disable', 'add_subtask', 'move', 'add_dependency', 'remove_dependency',
          'write', 'sync', 'dispatch', 'ingest', 'create_category', 'update_category'}
_DELETE = {'delete', 'remove', 'delete_rule', 'delete_category', 'delete_document', 'delete_file', 'clear', 'reset'}


def register(name, descriptor):
    if not isinstance(descriptor, CapabilityDescriptor) or descriptor.operation not in OPERATIONS | {'unknown'}:
        raise ValueError('invalid capability descriptor')
    with _lock:
        _registered[name] = descriptor


def describe_tool(name, arguments):
    with _lock:
        registered = _registered.get(name)
    if registered is not None:
        return registered
    action = str(arguments.get('action') or '').lower()
    if name in _BUSINESS:
        operation = 'delete' if action in _DELETE else 'read' if action in _READ else 'write' if action in _WRITE else 'unknown'
        if name == 'todos' and action == 'status':
            operation = 'write'
        if name == 'agent_hub' and action in {'start', 'run', 'execute', 'submit', 'continue'}:
            operation = 'system_execute'
        if name == 'agent_hub' and action == 'providers':
            operation = 'read'
        if name == 'agent_hub' and action == 'cancel':
            operation = 'write'
        return CapabilityDescriptor('plugin:stella.' + _BUSINESS[name], 'plugin', operation)
    for domain in ('memory', 'knowledge'):
        if name.startswith(domain + '_'):
            return CapabilityDescriptor('plugin:stella.' + domain, 'plugin', 'write' if name == domain + '_write' else 'read')
    if name in {'read', 'ls', 'search_files', 'vision'}:
        return CapabilityDescriptor('builtin:' + name, 'builtin', 'read', True)
    if name in {'write', 'edit', 'skill'}:
        return CapabilityDescriptor('builtin:' + name, 'builtin', 'write', True)
    if name in {'bash', 'subagent', 'evolution_undo'}:
        return CapabilityDescriptor('builtin:' + name, 'builtin', 'system_execute', name == 'bash')
    if name == 'send':
        return CapabilityDescriptor('builtin:send', 'builtin', 'external_send')
    if name == 'browser':
        local_file = action == 'navigate' and str(arguments.get('url') or '').lower().startswith('file://')
        operation = 'read' if action in {'navigate', 'back', 'forward', 'snapshot', 'get_text', 'screenshot', 'scroll', 'wait'} else 'external_send'
        return CapabilityDescriptor('builtin:browser', 'builtin', operation, local_file)
    if name in {'web_fetch', 'web_search'}:
        return CapabilityDescriptor('builtin:' + name, 'builtin', 'read')
    if name in {'apple', 'env_config', 'data_manage'}:
        operation = 'delete' if action in _DELETE else 'read' if action in _READ | {'sources', 'items', 'lookup', 'preview', 'preview_scope'} else 'write'
        return CapabilityDescriptor('builtin:' + name, 'builtin', operation)
    return CapabilityDescriptor('builtin:' + name, 'builtin', 'unknown')


def authorization(policy, descriptor):
    """One Agent mode governs every capability source; no per-plugin grants."""
    mode = policy.get('filesystem_mode') or policy.get('preset')
    if mode == 'custom':
        mode = policy.get('base_preset', 'read-only')
    if policy.get('migration_status') == 'needs_confirmation' or policy.get('error'):
        return 'deny'
    if mode not in {'read-only', 'workspace-write', 'full-access'}:
        return 'deny'
    if descriptor.operation == 'read':
        granted = 'allow'
    elif mode == 'read-only':
        granted = 'deny'
    elif mode == 'full-access':
        granted = 'allow'
    elif descriptor.operation == 'system_execute':
        granted = 'allow' if descriptor.filesystem else 'confirm'
    else:
        granted = 'allow' if descriptor.operation == 'write' else 'confirm'
    if policy.get('permission_mode') == 'custom':
        from agent.permission.security_policy import rule
        operation_rules = {
            'read': ('workspace_read',), 'write': ('workspace_write',),
            'delete': ('workspace_write', 'high_risk'),
            'external_send': ('external', 'network', 'high_risk'),
            'system_execute': ('shell',) if descriptor.filesystem else ('shell', 'managed', 'high_risk'),
            'unknown': ('external', 'shell', 'high_risk'),
        }
        order = {'deny': 0, 'confirm': 1, 'allow': 2}
        granted = min((granted, *(rule(policy, key) for key in operation_rules.get(descriptor.operation, ('high_risk',)))), key=order.get)
    return granted


def method_operation(method, arguments=None):
    operation = method.get('operation', 'unknown')
    if operation not in OPERATIONS | {'unknown'}:
        raise ValueError('invalid service method operation')
    if operation == 'read' and method.get('effect') != 'query':
        raise ValueError('mutating service method cannot declare read')
    cases = method.get('operationCases')
    if cases is not None:
        if (not isinstance(cases, dict) or any(not isinstance(key, str) or value not in OPERATIONS for key, value in cases.items())
                or 'read' in cases.values() and method.get('effect') != 'query'):
            raise ValueError('invalid service operation cases')
        if arguments is not None:
            return cases.get(arguments.get('action'), 'unknown')
    return operation


def mcp_descriptor(server, method, config):
    operation = (config.get('toolOperations') or {}).get(method, 'unknown')
    if operation not in OPERATIONS:
        operation = 'unknown'
    return CapabilityDescriptor(f'mcp:{server}/{method}', 'mcp', operation,
                                method in (config.get('filesystemTools') or []))
