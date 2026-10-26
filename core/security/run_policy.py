"""Independent execution snapshots; legacy policies are migration inputs only."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import re

from agent.permission.policy import MODES, READ_ONLY, WORKSPACE_WRITE
from core.security.policy_store import RunPolicyStore

OPERATIONS = frozenset({'read', 'write', 'delete', 'external_send', 'system_execute'})
VALUES = frozenset({'allow', 'confirm', 'deny'})
DOMAINS = ('tasks', 'calendar', 'bills', 'briefing', 'automation', 'agent-hub', 'memory', 'knowledge')
_TASK_SNAPSHOT = ContextVar('task_run_policy_snapshot', default=None)
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
    if not isinstance(value, dict) or set(value) - {'filesystem_mode', 'capabilities', 'connections', 'extra_roots', 'policy_revision', 'permission_mode', 'rules'}:
        raise ValueError('invalid run policy fields')
    result = deepcopy(value)
    if 'filesystem_mode' in value and value['filesystem_mode'] not in MODES:
        raise ValueError('invalid filesystem mode')
    permission_mode = value.get('permission_mode')
    if permission_mode is not None:
        if permission_mode not in (*MODES, 'custom'):
            raise ValueError('invalid permission mode')
        if 'filesystem_mode' not in value or (permission_mode != 'custom' and permission_mode != value['filesystem_mode']):
            raise ValueError('permission mode requires a matching filesystem base')
    if 'rules' in value:
        if permission_mode != 'custom':
            raise ValueError('custom rules require an explicit custom mode')
        result['rules'] = validate_patch({'rules': value['rules']})['rules']
    if permission_mode == 'custom' and 'rules' not in value:
        raise ValueError('custom mode requires explicit rules')
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
    if revision is not None and (type(revision) is not int or revision < 0):
        raise ValueError('invalid policy revision')
    return result


def default_template():
    # The retired 'default' row is migration evidence, never the new default.
    # A versioned key prevents a retired wide global template from reviving.
    key = subject_key('template', 'owner-default-v2')
    store = RunPolicyStore()
    initial = _seed({'filesystem_mode': READ_ONLY, 'capabilities': {}})
    policy = None
    try:
        policy = store.ensure(key, initial)
        checked = _seed({key: value for key, value in policy.items()
                         if key in {'filesystem_mode', 'permission_mode', 'rules', 'capabilities',
                                    'connections', 'extra_roots', 'policy_revision'}})
        return checked
    except (ValueError, TypeError, KeyError):
        return {**initial, 'policy_revision': policy['policy_revision'] if policy else 0,
                'migration_status': 'needs_confirmation'}


@contextmanager
def task_policy_scope(task, agent_id=None):
    from core.security.task_policy import policy_for_task
    key = subject_key('task', str(task['id']), agent_id)
    parent = _TASK_SNAPSHOT.get()
    snapshot = parent if parent and parent[0] == key else (key, policy_for_task(task, agent_id))
    token = _TASK_SNAPSHOT.set(snapshot)
    try:
        yield
    finally:
        _TASK_SNAPSHOT.reset(token)


def resolve_run_policy(session_id=None, agent_id=None, task_id=None, seed=None):
    from core.security.migration import ensure_migrated
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
    # Snapshot existing subjects before assigning a fixed default to a new one.
    store, key = RunPolicyStore(), subject_key(kind, subject, agent_id)
    snapshot = _TASK_SNAPSHOT.get()
    if kind == 'task' and snapshot and snapshot[0] == key:
        live = store.get(key)
        if live is None or live.get('revoked'):
            raise PermissionError('Task authorization was revoked')
        return deepcopy(snapshot[1])
    current = store.get(key)
    if current is not None:
        return current
    if seed is None:
        try:
            ensure_migrated()
        except (ValueError, OSError, PermissionError):
            return {**from_legacy({'error': 'Legacy permission migration requires repair'}), 'policy_revision': 0}
        current = store.get(key)
        if current is not None:
            return current
    initial = _seed(seed) if seed is not None else default_template()
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
        if (base.get('permission_mode') == 'custom' and 'filesystem_mode' in clean
                and 'permission_mode' not in clean):
            raise ValueError('This custom policy requires a custom-aware client')
        if clean.get('permission_mode') != 'custom' and 'filesystem_mode' in clean:
            base.pop('rules', None)
            base.pop('permission_mode', None)
        if 'filesystem_mode' in clean:
            # Replacing an untranslatable policy requires a complete explicit
            # choice; ordinary edits continue to preserve valid legacy denies.
            base['legacy_restrictions'] = {}
        if 'filesystem_mode' in clean and clean['filesystem_mode'] != base['filesystem_mode']:
            for name in ('workspace_read', 'workspace_write', 'workspace_outside', 'shell', 'git', 'install', 'background'):
                base.get('legacy_restrictions', {}).pop(name, None)
        if legacy_rules:
            base.setdefault('legacy_restrictions', {}).update(legacy_rules)
        return {**base, **clean, 'migration_status': 'complete'}
    return RunPolicyStore().update(key, change, expected_revision=expected_revision)


def gateway_policy(policy):
    custom = policy.get('permission_mode') == 'custom'
    rules = deepcopy(policy.get('legacy_restrictions') or {})
    if custom:
        from agent.permission.security_policy import PRESETS
        order = {'deny': 0, 'confirm': 1, 'allow': 2}
        base_rules = PRESETS[policy['filesystem_mode']]
        rules.update({name: min((value, base_rules[name]), key=order.get)
                      for name, value in (policy.get('rules') or {}).items()})
    return {'preset': 'custom' if custom else policy['filesystem_mode'], 'filesystem_mode': policy['filesystem_mode'],
            **({'permission_mode': 'custom', 'base_preset': policy['filesystem_mode']} if custom else {}),
            'rules': rules,
            'connections': deepcopy(policy.get('connections') or {}),
            'extra_roots': list(policy.get('extra_roots') or []),
            'capabilities': deepcopy(policy['capabilities']), 'policy_revision': policy.get('policy_revision', 1),
            'migration_status': policy.get('migration_status', 'complete'),
            **({'error': 'Legacy policy requires confirmation; access remains restricted.'}
               if policy.get('migration_status') == 'needs_confirmation' else {})}
