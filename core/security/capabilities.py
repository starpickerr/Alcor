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
        if name == 'agent_hub' and action in {'start', 'run', 'execute'}:
            operation = 'system_execute'
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
    grants = policy.get('capabilities') or {}
    operations = grants.get(descriptor.capability_id)
    if operations is None and descriptor.source == 'mcp':
        operations = grants.get(descriptor.capability_id.split('/', 1)[0])
    if descriptor.operation == 'unknown':
        return (operations or {}).get('system_execute', 'confirm')
    if operations is None:
        return 'deny'
    return operations.get(descriptor.operation, 'confirm' if descriptor.operation in {'delete', 'external_send', 'system_execute'} else 'deny')


def method_operation(method):
    operation = method.get('operation', 'unknown')
    if operation not in OPERATIONS | {'unknown'}:
        raise ValueError('invalid service method operation')
    if operation == 'read' and method.get('effect') != 'query':
        raise ValueError('mutating service method cannot declare read')
    return operation


def mcp_descriptor(server, method, config):
    operation = (config.get('toolOperations') or {}).get(method, 'unknown')
    if operation not in OPERATIONS:
        operation = 'unknown'
    return CapabilityDescriptor(f'mcp:{server}/{method}', 'mcp', operation,
                                method in (config.get('filesystemTools') or []))
