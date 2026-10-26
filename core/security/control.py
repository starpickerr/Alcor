"""Authenticated native controls. No plugin callback or Agent tool exposes this API."""
from copy import deepcopy
from pathlib import Path


def require_owner():
    from core.security.task_policy import trusted_user_edit
    from core.execution.context import current_execution_context
    active = current_execution_context()
    if not trusted_user_edit('user') or active and active.origin != 'ui':
        raise PermissionError('Permission settings require an authenticated user action')


def public_policy(policy):
    return {key: deepcopy(policy[key]) for key in ('filesystem_mode', 'policy_revision',
        'extra_roots', 'connections', 'permission_mode', 'rules') if key in policy}


class PermissionControl:
    def __init__(self, *, task_service=None):
        self.task_service = task_service

    def _tasks(self, agent):
        if self.task_service is None:
            raise ValueError('Task service is unavailable')
        return self.task_service(agent)

    def inventory(self):
        require_owner()
        from common.runtime_identity import current_identity
        from core.security.migration import ensure_migrated
        from core.security.policy_store import RunPolicyStore
        ensure_migrated()
        rows = RunPolicyStore().subjects(current_identity().user_id or 'local-default')
        return {'subjects': [dict(agent=agent, kind=kind, subject=subject, policy=public_policy(policy),
            source='independent', migration_status=policy.get('migration_status', 'complete'),
            original_url='/tasks' if kind == 'task' else '/')
            for agent, kind, subject, policy in rows if not policy.get('revoked')]}

    @staticmethod
    def _prepare(kind, subject, agent):
        require_owner()
        from core.security.migration import ensure_migrated
        from core.security.run_policy import subject_key
        ensure_migrated()
        key = subject_key(kind, subject, agent)
        if kind != 'template':
            from agent.registry import get_agent_registry
            get_agent_registry().get(key[1], require_enabled=False)
        return key

    def read(self, kind, subject, agent=None):
        self._prepare(kind, subject, agent)
        from core.security.run_policy import default_template, resolve_run_policy
        if kind == 'template':
            policy = default_template()
        elif kind == 'session':
            policy = resolve_run_policy(subject, agent)
        else:
            task = self._tasks(agent).get(subject)
            if not task:
                raise ValueError('Task not found')
            policy = task['run_policy']
        return {'policy': public_policy(policy), 'migration_status': policy.get('migration_status', 'complete'),
                'compatibility_restrictions': deepcopy(policy.get('legacy_restrictions') or {}),
                'capabilities': []}

    def save(self, kind, subject, patch, agent=None):
        require_owner()
        if kind not in {'template', 'session', 'task'}:
            raise ValueError('Invalid policy subject')
        if kind == 'template' and subject != 'default':
            raise ValueError('Only the current default template is editable')
        self._prepare(kind, subject, agent)
        from core.security.run_policy import validate_policy, save_run_policy
        clean = validate_policy(patch)
        if type(clean.get('policy_revision')) is not int or clean['policy_revision'] < (0 if kind == 'task' else 1):
            raise ValueError('Current policy revision is required')
        if kind == 'template':
            save_run_policy(kind, 'owner-default-v2', clean, expected_revision=clean['policy_revision'])
        elif kind == 'task':
            self._tasks(agent).update(subject, {'run_policy': clean}, agent_id=agent, origin='user')
        else:
            save_run_policy(kind, subject, clean, agent_id=agent, expected_revision=clean['policy_revision'])
        from agent.permission.audit import record
        record(event='policy_update', operation='update', resource=f'{kind} RunPolicy', decision='policy_change',
               reason='Owner updated independent execution permissions.')
        return self.read(kind, subject, agent)



class RuntimeControl:
    def inventory(self):
        require_owner()
        from common.runtime_identity import current_identity
        from core.plugins.management import default_store
        from core.plugins.package_ops import PackageStore
        from core.plugins.host import _LIVE_SESSIONS
        from agent.permission.process_sandbox import capability
        from agent.tools.mcp.mcp_client import LIVE_CLIENTS
        from agent.tools.tool_manager import ToolManager
        from core.security.mcp_runtime import launch_identity
        user = current_identity().user_id or 'local-default'
        packages = PackageStore(default_store().root)
        rows = []
        for pointer in packages.packages.glob('*/current.json'):
            identifier = pointer.parent.name
            package = packages.current_package(identifier)
            if not package:
                continue
            from core.plugins.package_ops import _sha256_tree
            if _sha256_tree(Path(package['path'])) != package['digest']:
                rows.append({'kind': 'plugin', 'id': identifier, 'mode': 'unavailable', 'reason': 'Package integrity changed.', 'running': False})
                continue
            session = next((row for row in list(_LIVE_SESSIONS) if row.plugin_id == identifier and row.user_id == user
                            and row._proc is not None and row._proc.poll() is None), None)
            rows.append({'kind': 'plugin', 'id': identifier, 'digest': package['digest'], 'environment': getattr(session, 'environment', None),
                'running': session is not None, 'mode': session.sandbox_state['mode'] if session else 'stopped',
                'reason': session.sandbox_state.get('reason') if session else 'Worker has not started.'})
        manager = ToolManager()
        for config in manager._load_mcp_configs():
            if config.get('type', 'stdio') != 'stdio':
                rows.append({'kind': 'mcp', 'id': config['name'], 'mode': 'remote', 'reason': 'Remote connection; local process isolation does not apply.'})
                continue
            try:
                digest, _ = launch_identity(config)
                client = next((row for row in list(LIVE_CLIENTS) if row._runtime_key and
                               row._runtime_key['user_id'] == user and row._runtime_key['identifier'] == config['name'] and row._proc is not None
                               and row._proc.poll() is None), None)
                rows.append({'kind': 'mcp', 'id': config['name'], 'digest': digest,
                    'mode': client.sandbox_state['mode'] if client else 'stopped', 'running': client is not None,
                    'environment': getattr(client, 'environment', None),
                    'reason': client.sandbox_state.get('reason') if client else 'Server has not started.'})
            except (ValueError, OSError) as error:
                rows.append({'kind': 'mcp', 'id': config['name'], 'mode': 'unavailable', 'reason': str(error)})
        from core.security.isolation import application_isolation
        return {'runtimes': rows, 'isolation': {**capability(), 'scope': 'child-process'},
                'application_isolation': application_isolation()}
