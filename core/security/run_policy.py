"""Independent execution snapshots; legacy policies are migration inputs only."""
from copy import deepcopy
import re

from agent.permission.policy import MODES, READ_ONLY, WORKSPACE_WRITE
from core.security.policy_store import RunPolicyStore

OPERATIONS = frozenset({'read', 'write', 'delete', 'external_send', 'system_execute'})
VALUES = frozenset({'allow', 'confirm', 'deny'})
DOMAINS = ('tasks', 'calendar', 'bills', 'briefing', 'automation', 'agent-hub', 'memory', 'knowledge')
_CAPABILITY = re.compile(r'^(?:builtin|plugin|mcp):[^\s\x00-\x1f]{1,240}$')


def subject_key(kind, subject, agent_id=None):
    from common.runtime_identity import current_identity
    from agent.registry import get_agent_registry
    identity = current_identity()
    if kind not in {'template', 'session', 'task'} or not isinstance(subject, str) or not subject or len(subject) > 240:
        raise ValueError('invalid run policy subject')
    agent = '*' if kind == 'template' else agent_id or identity.agent_id or get_agent_registry().default_agent_id
    return (identity.user_id or 'local-default', agent, kind, subject)


def from_legacy(value):
    from agent.permission.security_policy import PRESETS, effective_rules, merge, default_policy, validate_patch
    try:
        if not isinstance(value, dict) or value.get('error'):
            raise ValueError('legacy policy is invalid')
        policy = merge(default_policy(), validate_patch(value))
        rules = effective_rules(policy)
        mode = policy.get('base_preset', WORKSPACE_WRITE) if policy['preset'] == 'custom' else policy['preset']
        if mode not in MODES:
            mode = READ_ONLY
        if rules['workspace_write'] == rules['shell'] == 'deny':
            mode = READ_ONLY
        order = {'deny': 0, 'confirm': 1, 'allow': 2}
        sensitive = min((rules['workspace_write'], rules['high_risk']), key=order.get)
        capabilities = {'plugin:stella.' + domain: {'read': rules['workspace_read'],
            'write': rules['workspace_write'], 'delete': sensitive,
            'external_send': sensitive, 'system_execute': min((rules['shell'], rules['high_risk']), key=order.get)}
            for domain in DOMAINS}
        for name in ('read', 'ls', 'search_files', 'vision', 'web_search', 'web_fetch'):
            capabilities['builtin:' + name] = {'read': rules['workspace_read']}
        for name in ('write', 'edit', 'skill'):
            capabilities['builtin:' + name] = {'write': rules['workspace_write']}
        capabilities['builtin:bash'] = {'system_execute': rules['shell'], 'external_send': rules['high_risk'], 'delete': sensitive}
        capabilities['builtin:subagent'] = {'system_execute': rules['managed']}
        capabilities['builtin:send'] = {'external_send': rules['high_risk']}
        capabilities['builtin:browser'] = {'read': rules['external'], 'external_send': rules['high_risk']}
        for name in ('apple', 'env_config', 'data_manage'):
            capabilities['builtin:' + name] = {'read': rules['workspace_read'], 'write': rules['workspace_write'], 'delete': sensitive}
        capabilities['builtin:env_config']['write'] = rules['high_risk']
        for domain in ('email', 'elearning'):
            capabilities['mcp:' + domain] = {'read': rules['external'], 'write': rules['workspace_write'], 'delete': sensitive}
        return {'filesystem_mode': mode, 'capabilities': capabilities,
                'legacy_restrictions': {name: setting for name, setting in rules.items() if setting != PRESETS[mode][name]},
                'connections': deepcopy(policy.get('connections') or {}),
                'extra_roots': list(policy.get('extra_roots') or []), 'migration_status': 'complete'}
    except (ValueError, TypeError, KeyError):
        return {'filesystem_mode': READ_ONLY, 'capabilities': {}, 'legacy_restrictions':
                {name: 'deny' for name in PRESETS[READ_ONLY]}, 'connections': {}, 'extra_roots': [],
                'migration_status': 'needs_confirmation'}


def validate_policy(value):
    from agent.permission.security_policy import validate_patch
    if not isinstance(value, dict) or set(value) - {'filesystem_mode', 'capabilities', 'connections', 'extra_roots', 'policy_revision'}:
        raise ValueError('invalid run policy fields')
    result = deepcopy(value)
    if 'filesystem_mode' in value and value['filesystem_mode'] not in MODES:
        raise ValueError('invalid filesystem mode')
    capabilities = value.get('capabilities', {})
    if not isinstance(capabilities, dict) or len(capabilities) > 2000:
        raise ValueError('invalid capability authorization')
    for name, operations in capabilities.items():
        if (not isinstance(name, str) or not _CAPABILITY.fullmatch(name) or not isinstance(operations, dict)
                or set(operations) - OPERATIONS or any(setting not in VALUES for setting in operations.values())):
            raise ValueError('invalid capability operations')
    for field in ('connections', 'extra_roots'):
        if field in value:
            validate_patch({field: value[field]})
    revision = result.get('policy_revision')
    if revision is not None and (type(revision) is not int or revision < 1):
        raise ValueError('invalid policy revision')
    return result


def default_template():
    from agent.permission.policy import global_mode
    from agent.permission.security_policy import _load_global
    return RunPolicyStore().ensure(subject_key('template', 'default'), from_legacy(_load_global(global_mode(), None)))


def resolve_run_policy(session_id=None, agent_id=None, task_id=None, seed=None):
    from common.runtime_identity import current_identity
    identity = current_identity()
    task_id = task_id or identity.task_id
    session_id = session_id or identity.execution_session_id or identity.session_id
    if task_id:
        kind, subject = 'task', task_id
    elif session_id:
        kind, subject = 'session', session_id
    else:
        return default_template() if seed is None else _seed(seed)
    store, key = RunPolicyStore(), subject_key(kind, subject, agent_id)
    current = store.get(key)
    if current is not None:
        return current
    if seed is not None:
        initial = _seed(seed)
    elif kind == 'task':
        initial = _seed({'filesystem_mode': READ_ONLY, 'capabilities': {}})
    else:
        from agent.permission.policy import global_mode
        from agent.permission.security_policy import _legacy_resolve
        from agent.workspace.project_store import get_project_dir
        legacy = _legacy_resolve(global_mode(), project_dir=get_project_dir(subject, agent_id),
                                 session_id=subject, agent_id=agent_id)
        from agent.workspace.session_prefs import get_prefs
        if legacy.get('error'):
            initial = from_legacy(legacy)
        else:
            initial = from_legacy(legacy) if get_project_dir(subject, agent_id) or get_prefs(subject, agent_id).get('security_policy') else default_template()
    return store.ensure(key, initial)


def _seed(value):
    if isinstance(value, dict) and 'filesystem_mode' in value:
        checked = validate_policy(value)
        return {'filesystem_mode': READ_ONLY, 'capabilities': {}, 'connections': {}, 'extra_roots': [],
                'legacy_restrictions': {}, 'migration_status': 'complete', **checked}
    return from_legacy(value)


def save_run_policy(subject_kind, subject_id, policy, *, agent_id=None, expected_revision=None, legacy_rules=None):
    clean = validate_policy(policy)
    if legacy_rules is not None:
        from agent.permission.security_policy import validate_patch
        legacy_rules = validate_patch({'rules': legacy_rules})['rules']
    key = subject_key(subject_kind, subject_id, agent_id)
    def change(current):
        base = current or _seed({'filesystem_mode': READ_ONLY, 'capabilities': {}})
        base = deepcopy(base)
        if 'filesystem_mode' in clean and clean['filesystem_mode'] != base['filesystem_mode']:
            for name in ('workspace_read', 'workspace_write', 'workspace_outside', 'shell', 'git', 'install', 'background'):
                base.get('legacy_restrictions', {}).pop(name, None)
        if legacy_rules:
            base.setdefault('legacy_restrictions', {}).update(legacy_rules)
        return {**base, **clean, 'migration_status': 'complete'}
    return RunPolicyStore().update(key, change, expected_revision=expected_revision)


def gateway_policy(policy):
    return {'preset': policy['filesystem_mode'], 'filesystem_mode': policy['filesystem_mode'],
            'rules': deepcopy(policy.get('legacy_restrictions') or {}),
            'connections': deepcopy(policy.get('connections') or {}),
            'extra_roots': list(policy.get('extra_roots') or []),
            'capabilities': deepcopy(policy['capabilities']), 'policy_revision': policy.get('policy_revision', 1),
            'migration_status': policy.get('migration_status', 'complete'),
            **({'error': 'Legacy policy requires confirmation; access remains restricted.'}
               if policy.get('migration_status') == 'needs_confirmation' else {})}
