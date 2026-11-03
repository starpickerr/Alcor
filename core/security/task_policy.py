"""Trusted scheduler policy boundary, shared by legacy stores and plugin RPC."""
from copy import deepcopy
from dataclasses import dataclass
import os

from core.security.policy_store import RunPolicyStore
from core.security.run_policy import (gateway_policy, subject_key,
                                      validate_policy, from_legacy)

_ORDER = {'deny': 0, 'confirm': 1, 'allow': 2}
_MODES = {'read-only': 0, 'workspace-write': 1, 'full-access': 2}


def trusted_user_edit(origin):
    from common.runtime_identity import current_identity
    from core.plugins.caller import current_caller_origin, current_delegation
    identity = current_identity()
    return (origin == 'user' and not any((identity.session_id, identity.run_id, identity.task_id))
            and current_caller_origin() not in {'tool', 'job', 'plugin'} and current_delegation() is None)


def policy_for_task(task, agent_id=None):
    key = subject_key('task', str(task['id']), agent_id)
    store = RunPolicyStore()
    current = store.get(key)
    if current is None:
        if task.get('run_policy_version') == 1:
            current = {'filesystem_mode': 'read-only', 'capabilities': {}, 'policy_revision': 0,
                       'migration_status': 'needs_confirmation'}
        else:
            from agent.permission.policy import legacy_global_mode
            from agent.permission.security_policy import _legacy_resolve
            action = task.get('action') or {}
            source = action.get('execution_session_id') or action.get('source_session_id')
            legacy = _legacy_resolve(legacy_global_mode(), project_dir=action.get('execution_project_dir'),
                                      session_id=source, agent_id=agent_id)
            mode = task.get('run_permission_mode')
            if mode in _MODES:
                legacy = {**legacy, 'preset': mode, 'base_preset': mode, 'rules': {}}
            current = store.ensure(key, from_legacy(legacy))
    if task.get('run_policy_revision', 0) > current['policy_revision']:
        return {**current, 'filesystem_mode': 'read-only', 'capabilities': {}, 'migration_status': 'needs_confirmation'}
    return current


def describe_task(task, agent_id=None):
    policy = policy_for_task(task, agent_id)
    return {**task, 'run_policy': policy, 'run_permission_mode': policy.get('permission_mode', policy['filesystem_mode']),
            'permission_source': 'independent', 'permission_needs_confirmation': policy.get('migration_status') != 'complete'}


def _check_delegation(candidate, ceiling):
    custom = candidate.get('permission_mode') == 'custom' or ceiling.get('permission_mode') == 'custom'
    if not custom and _MODES[candidate['filesystem_mode']] > _MODES[ceiling['filesystem_mode']]:
        raise PermissionError('Task filesystem permission exceeds the delegating run')
    from agent.permission.security_policy import effective_rules
    from core.security.capabilities import authorization, CapabilityDescriptor
    from core.security.run_policy import OPERATIONS
    candidate_policy = gateway_policy(candidate) if 'preset' not in candidate else candidate
    parent_policy = gateway_policy(ceiling) if 'preset' not in ceiling else ceiling
    child_rules, parent_rules = effective_rules(candidate_policy), effective_rules(parent_policy)
    # Named legacy modes keep inheriting the caller's historical restrictions
    # below. Explicit custom rules must be checked before that inheritance so
    # a caller cannot conceal an escalation behind an identical mode name.
    if custom and any(_ORDER[value] > _ORDER[parent_rules[name]] for name, value in child_rules.items()):
        raise PermissionError('Task custom rules exceed the delegating run')
    if not custom:
        # Named modes inherit restrictions before comparing operation ceilings.
        candidate_policy = {**candidate_policy, 'rules': {
            name: min((value, parent_rules[name]), key=_ORDER.get) for name, value in child_rules.items()}}
    if any(_ORDER[authorization(candidate_policy, CapabilityDescriptor('builtin:delegated', 'builtin', operation, filesystem))]
           > _ORDER[authorization(parent_policy, CapabilityDescriptor('builtin:delegated', 'builtin', operation, filesystem))]
           for operation in OPERATIONS for filesystem in (True, False)):
        raise PermissionError('Task operation permission exceeds the delegating run')
    for path in candidate.get('extra_roots') or []:
        real = os.path.realpath(path)
        if not any(os.path.commonpath((real, os.path.realpath(root))) == os.path.realpath(root)
                   for root in ceiling.get('extra_roots') or []):
            raise PermissionError('Task cannot acquire additional path grants')
    for name, grant in (candidate.get('connections') or {}).items():
        parent = (ceiling.get('connections') or {}).get(name)
        if grant.get('allowed', True) and (not parent or not parent.get('allowed', True)):
            raise PermissionError('Task cannot acquire an additional connection grant')
        scopes = grant.get('scopes') or []
        if parent and parent.get('scopes') and '*' not in parent['scopes']:
            if not scopes or '*' in scopes or not set(scopes) <= set(parent['scopes']):
                raise PermissionError('Task resource scopes exceed the delegating run')


@dataclass(frozen=True)
class TaskPolicyChange:
    key: tuple
    policy: dict
    expected_revision: int

    def apply(self):
        return RunPolicyStore().update(self.key, lambda _current: deepcopy(self.policy),
                                       expected_revision=self.expected_revision)


def prepare_task_policy(task, *, agent_id=None, origin='user', creating=True):
    """Prepare a policy; commit it only after task mutation succeeds.

    The task stores the expected revision. A crash between task persistence and
    policy persistence fails closed, rather than borrowing an older wider policy.
    """
    task = deepcopy(task)
    task.pop('run_policy_version', None)
    task.pop('run_policy_revision', None)
    requested = task.pop('run_policy', None)
    mode = task.pop('run_permission_mode', None)
    key = subject_key('task', str(task['id']), agent_id)
    current = RunPolicyStore().get(key)
    if not creating and not trusted_user_edit(origin) and (current is None or current.get('migration_status') == 'needs_confirmation'):
        raise PermissionError('Task policy repair requires an authenticated user action')
    from core.security.run_policy import default_template
    base = deepcopy(default_template() if creating or current is None else current)
    action = task.get('action') or {}
    if creating and trusted_user_edit(origin) and (action.get('conversation_mode') in {'source', 'reuse', 'new'}
            or action.get('source_session_id') or action.get('execution_session_id')):
        # Linking a conversation supplies context, never its permissions or a
        # wider default intended only for directly created independent jobs.
        from core.security.run_policy import _seed
        base = _seed({'filesystem_mode': 'read-only'})
    if requested is not None:
        patch = validate_policy(requested)
        if not creating and patch.get('policy_revision') is not None and patch['policy_revision'] != (current or {}).get('policy_revision', 0):
            raise ValueError('Task policy revision changed; refresh and retry')
        if trusted_user_edit(origin) and (current is None or current.get('migration_status') == 'needs_confirmation'):
            if 'filesystem_mode' not in patch:
                raise ValueError('Task policy repair requires a complete explicit choice')
            base['legacy_restrictions'] = {}
            base['migration_status'] = 'complete'
        if 'filesystem_mode' in patch and patch.get('permission_mode') != 'custom':
            base.pop('permission_mode', None)
            base.pop('rules', None)
        base.update(patch)
        if trusted_user_edit(origin) and 'filesystem_mode' in patch:
            base['legacy_restrictions'] = {}
            base['migration_status'] = 'complete'
    if mode not in (None, '', 'inherit'):
        if mode not in _MODES:
            raise ValueError('invalid run permission mode')
        base.pop('permission_mode', None)
        base.pop('rules', None)
        base['filesystem_mode'] = mode
    if base.get('migration_status') != 'complete':
        raise PermissionError('Permissions require owner confirmation')
    base.pop('policy_revision', None)
    if not trusted_user_edit(origin):
        if not creating:
            _check_delegation(base, current or {'filesystem_mode': 'read-only', 'capabilities': {}})
        from common.runtime_identity import current_identity
        from agent.runtime.tool_invocation import _current_authorization
        from agent.permission.security_policy import intersect
        identity = current_identity()
        _, effective, _ = _current_authorization(None, identity.agent_id or agent_id,
                                                 identity.session_id, None, False)
        ceiling = effective['policy']
        if creating and requested is None and mode in (None, '', 'inherit'):
            # New Agent-created tasks copy the effective intersection, never the
            # caller's later edits or a subsequently changed default template.
            merged = intersect(gateway_policy(base), ceiling)
            base['filesystem_mode'] = merged['filesystem_mode']
            base['extra_roots'] = merged['extra_roots']
            base['connections'] = merged['connections']
            base['legacy_restrictions'] = merged['rules']
        _check_delegation(base, ceiling)
        # Persist restrictions too: a later scheduler must not lose a caller's
        # legacy deny merely because its filesystem mode has the same name.
        from agent.permission.security_policy import PRESETS
        rules = intersect(gateway_policy(base), ceiling)['rules']
        base['legacy_restrictions'] = {name: value for name, value in rules.items()
                                       if value != PRESETS[base['filesystem_mode']][name]}
    change = TaskPolicyChange(key, base, current['policy_revision'] if current else 0)
    task.update(run_policy_version=1, run_policy_revision=change.expected_revision + 1)
    return task, change


def initialize_scheduled_policy(task, agent_id=None):
    from common.runtime_identity import current_identity
    identity = current_identity()
    if not identity.task_id or identity.task_id != str(task.get('id') or ''):
        raise PermissionError('Scheduled policy requires the trusted task identity')
    policy = policy_for_task(task, agent_id or identity.agent_id)
    if policy.get('migration_status') != 'complete':
        raise PermissionError('Task permissions require confirmation. Adjust authorization and rerun this task manually.')
    return gateway_policy(policy)


def revoke_task_policy(task_id, agent_id=None):
    from agent.permission.security_policy import RULES
    key = subject_key('task', str(task_id), agent_id)
    return RunPolicyStore().update(key, lambda current: {
        **(current or {}), 'filesystem_mode': 'read-only', 'capabilities': {}, 'revoked': True,
        'legacy_restrictions': {name: 'deny' for name in RULES}, 'migration_status': 'needs_confirmation'})
