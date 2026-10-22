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
    return {key: deepcopy(policy[key]) for key in ('filesystem_mode', 'capabilities', 'policy_revision',
        'extra_roots', 'connections') if key in policy}


class PermissionControl:
    def __init__(self, *, task_service=None):
        self.task_service = task_service

    def _tasks(self, agent):
        if self.task_service is None:
            raise ValueError('Task service is unavailable')
        return self.task_service(agent)

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
                'capabilities': capability_catalog(policy)}

    def save(self, kind, subject, patch, agent=None):
        self._prepare(kind, subject, agent)
        from core.security.run_policy import validate_policy, save_run_policy
        clean = validate_policy(patch)
        if type(clean.get('policy_revision')) is not int or clean['policy_revision'] < 1:
            raise ValueError('Current policy revision is required')
        if kind == 'task':
            self._tasks(agent).update(subject, {'run_policy': clean}, agent_id=agent, origin='user')
        else:
            save_run_policy(kind, subject, clean, agent_id=agent, expected_revision=clean['policy_revision'])
        from agent.permission.audit import record
        record(event='policy_update', operation='update', resource=f'{kind} RunPolicy', decision='policy_change',
               reason='Owner updated independent execution permissions.')
        return self.read(kind, subject, agent)


def capability_catalog(policy):
    from core.security.run_policy import DOMAINS, default_template
    from core.plugins.management import default_store
    from core.plugins.package_ops import PackageStore
    from common.runtime_identity import current_identity
    store = default_store()
    user = current_identity().user_id or 'local-default'
    names = {'tasks': '事务', 'calendar': '日程', 'bills': '账单', 'briefing': '简报', 'automation': '定时任务',
        'agent-hub': '外部 Agent', 'memory': '记忆', 'knowledge': '知识'}
    identifiers = set(default_template()['capabilities']) | set(policy['capabilities'])
    identifiers.update('plugin:stella.' + domain for domain in DOMAINS)
    identifiers.update('plugin:' + path.parent.name for path in PackageStore(store.root).packages.glob('*/current.json'))
    from agent.tools.tool_manager import ToolManager
    from core.security.capabilities import describe_tool
    manager = ToolManager()
    identifiers.update(describe_tool(name, {}).capability_id for name in manager.tool_classes)
    mcp_configs = {row['name']: row for row in manager._load_mcp_configs()}
    identifiers.update('mcp:' + name for name in mcp_configs)
    rows = []
    for identifier in sorted(identifiers):
        source, name = identifier.split(':', 1)
        row = {'id': identifier, 'name': name, 'source': source}
        if source == 'plugin':
            domain = name.removeprefix('stella.')
            state = store.instance(name, user)
            package = PackageStore(store.root).current_package(name)
            row.update(name=names.get(domain, name), installed=bool(package),
                       enabled=bool(package) and store.has_instance(name, user) and state['intent'] == 'enabled', runtime_state=state['state'])
        elif source == 'builtin':
            row.update(installed=True, runtime_state='available')
        else:
            config = mcp_configs.get(name.split('/', 1)[0])
            row.update(runtime_state='configured' if config else 'unavailable', enabled=manager._mcp_enabled(config) if config else False)
        rows.append(row)
    return rows


class ResourceControl:
    @staticmethod
    def _user():
        from common.runtime_identity import current_identity
        return current_identity().user_id or 'local-default'

    def subjects(self):
        require_owner()
        from core.security.policy_store import RunPolicyStore
        with RunPolicyStore().transaction() as db:
            rows = [{'id': f'{kind}:{agent}:{subject}', 'kind': kind, 'agent': agent, 'subject': subject}
                    for agent, kind, subject in db.execute('SELECT agent,kind,subject FROM run_policies WHERE user=? AND kind!=?',
                                                          (self._user(), 'template'))]
        from core.plugins.management import default_store
        from core.plugins.package_ops import PackageStore
        packages = PackageStore(default_store().root).packages
        rows += [{'id': 'plugin:' + path.parent.name, 'kind': 'plugin', 'subject': path.parent.name}
                 for path in packages.glob('*/current.json')]
        return rows

    def available(self):
        from agent.tools.tool_manager import ToolManager
        rows = [{'kind': 'connection', 'id': 'mcp:' + cfg['name'], 'name': cfg['name'],
                 'operations': ['read', 'write', 'delete', 'external_send', 'system_execute']}
                for cfg in ToolManager()._load_mcp_configs()]
        from core.data.runtime import current_service
        try:
            sources = current_service().list_sources()
        except (ValueError, OSError, RuntimeError, PermissionError):
            sources = []
        for source in sources:
            if source['provider'] == 'elearning':
                config = source.get('config') or {}
                account = config.get('account_id')
                if account:
                    rows.extend({'kind': 'course', 'id': f'{account}:{course}',
                                 'name': f"{source.get('name') or 'eLearning'} · {course}", 'operations': ['read']}
                                for course in config.get('course_ids', []) if isinstance(course, str) and course.isdigit())
                continue
            kind = 'folder' if source['provider'] == 'email' else 'course' if source['provider'] == 'elearning' else 'data-source'
            identifier = source.get('account_id') if kind != 'data-source' else source['id']
            if identifier:
                rows.append({'kind': kind, 'id': identifier, 'name': source.get('name') or source['id'], 'operations': ['read']})
        return list({(row['kind'], row['id']): row for row in rows}.values())

    def inventory(self):
        require_owner()
        from core.security.resource_grants import ResourceGrantStore
        from agent.permission.mcp_access import inventory
        return {'grants': ResourceGrantStore().list_owned(self._user()), 'available': self.available(),
                'subjects': self.subjects(), 'inherited': inventory()}

    def issue(self, body):
        require_owner()
        if not isinstance(body, dict) or set(body) - {'subject', 'resource', 'operations', 'purposes', 'valid_until'}:
            raise ValueError('Invalid resource authorization fields')
        if body.get('subject') not in {row['id'] for row in self.subjects()}:
            raise ValueError('Execution subject not found; select an existing conversation, task or plugin')
        resource = body.get('resource')
        allowed = next((row for row in self.available() if {'kind': row['kind'], 'id': row['id']} == resource), None)
        if not allowed or not set(body.get('operations') or []) <= set(allowed['operations']):
            raise PermissionError('Resource scope is unavailable or its operations are unsupported')
        from core.security.resource_grants import ResourceGrantStore
        row = ResourceGrantStore().issue(user_id=self._user(), **body)
        from agent.permission.audit import record
        record(event='resource_grant', operation='issue', resource=resource['kind'], decision='policy_change',
               reason='Owner authorized a specific resource scope.')
        return row

    def revoke(self, grant_id, revision):
        require_owner()
        from core.security.resource_grants import ResourceGrantStore
        ResourceGrantStore().revoke(grant_id, user_id=self._user(), expected_revision=revision)
        from agent.permission.audit import record
        record(event='resource_grant', operation='revoke', resource='resource grant', decision='policy_change',
               reason='Owner revoked a resource scope.')

    def set_connection(self, connection, allowed, scopes, revision):
        require_owner()
        from core.security.migration import ensure_migrated
        ensure_migrated()
        if not isinstance(allowed, bool) or not isinstance(scopes, list) or any(not isinstance(value, str) for value in scopes):
            raise ValueError('Invalid connection scope')
        from core.security.resource_grants import ResourceGrantStore
        ResourceGrantStore().update_owned_connection(user_id=self._user(), connection=connection,
            policy={'allowed': allowed, 'scopes': scopes}, expected_revision=revision)
        from agent.permission.audit import record
        record(operation='update', resource='connection scope', event='resource_grant', decision='policy_change',
               reason='Owner updated connection resource restrictions.')


class RuntimeControl:
    def inventory(self):
        require_owner()
        from common.runtime_identity import current_identity
        from core.plugins.management import default_store
        from core.plugins.package_ops import PackageStore
        from core.plugins.host import _LIVE_SESSIONS
        from core.security.runtime_trust import plugin_trusted
        from agent.permission.process_sandbox import capability
        from agent.tools.mcp.mcp_client import LIVE_CLIENTS
        from agent.tools.tool_manager import ToolManager
        from core.security.mcp_runtime import launch_identity
        from core.security.runtime_trust import RuntimeTrustStore
        from core.mcp.managed import ManagedMcpConfig
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
            trusted = plugin_trusted(package['path'], identifier, package['digest'], user)
            rows.append({'kind': 'plugin', 'id': identifier, 'digest': package['digest'], 'trusted': trusted,
                'running': session is not None, 'mode': session.sandbox_state['mode'] if session else 'stopped',
                'reason': session.sandbox_state.get('reason') if session else 'Worker has not started.'})
        manager = ToolManager()
        for config in manager._load_mcp_configs():
            if config.get('type', 'stdio') != 'stdio':
                rows.append({'kind': 'mcp', 'id': config['name'], 'mode': 'remote', 'reason': 'Remote connection; local process isolation does not apply.'})
                continue
            try:
                digest, _ = launch_identity(config)
                choice = RuntimeTrustStore().choice(user_id=user, kind='mcp', identifier=config['name'], digest=digest)
                trusted = choice if choice is not None else isinstance(config, ManagedMcpConfig)
                client = next((row for row in list(LIVE_CLIENTS) if row._runtime_key and
                               row._runtime_key['user_id'] == user and row._runtime_key['identifier'] == config['name'] and row._proc is not None
                               and row._proc.poll() is None), None)
                rows.append({'kind': 'mcp', 'id': config['name'], 'digest': digest, 'trusted': trusted,
                    'mode': client.sandbox_state['mode'] if client else 'stopped', 'running': client is not None,
                    'reason': client.sandbox_state.get('reason') if client else 'Server has not started.'})
            except (ValueError, OSError) as error:
                rows.append({'kind': 'mcp', 'id': config['name'], 'mode': 'unavailable', 'reason': str(error)})
        return {'runtimes': rows, 'isolation': capability()}

    def save(self, body):
        require_owner()
        if set(body) != {'kind', 'id', 'digest', 'trusted'} or not isinstance(body['trusted'], bool):
            raise ValueError('Invalid runtime trust choice')
        current = next((row for row in self.inventory()['runtimes'] if row['kind'] == body['kind'] and row['id'] == body['id']), None)
        if not current or not current.get('digest') or current['digest'] != body['digest']:
            raise ValueError('Runtime package changed; refresh before changing trust')
        from core.security.runtime_trust import RuntimeTrustStore
        RuntimeTrustStore().set(user_id=ResourceControl._user(), kind=body['kind'], identifier=body['id'],
                               digest=body['digest'], trusted=body['trusted'])
        from agent.permission.audit import record
        record(event='runtime_trust', operation='update', resource=body['kind'], decision='policy_change',
               reason='Owner changed digest-bound runtime trust.')
        return self.inventory()
