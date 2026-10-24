"""Feature-bound management over the existing SecurityService authorization facts.

Ownership migration is a view change, not a data rewrite: grant IDs, ancestry,
revisions, expiration and revocation stay in the original transactional store.
"""
from core.security.control import require_owner
from core.security.resource_grants import ResourceGrantStore


def current_user():
    from common.runtime_identity import current_identity
    return current_identity().user_id or 'local-default'


def retained_connections():
    """Keep retired connection scopes discoverable in their owning capability module."""
    store = ResourceGrantStore()
    keys = {row['resource']['id'] for row in store.list_owned(current_user()) if row['resource']['kind'] == 'connection'}
    with store.transaction() as db:
        store._restrictions(db)
        keys.update(row[0] for row in db.execute('SELECT DISTINCT connection FROM resource_restrictions WHERE user=?', (current_user(),)))
    from agent.permission.mcp_access import _owned
    keys.update('mcp:' + row.plugin_id.removeprefix('stella.') for row in _owned())
    keys.update(row['id'] for row in owner_connection_settings())
    return {'mcp:' + key if key in {'email', 'elearning'} else key for key in keys}


def retained_plugins():
    store = ResourceGrantStore()
    result = {row['subject'][7:] for row in store.list_owned(current_user()) if row['subject'].startswith('plugin:')}
    with store.transaction() as db:
        store._restrictions(db)
        result.update(row[0][7:] for row in db.execute('SELECT DISTINCT subject FROM resource_restrictions WHERE user=?', (current_user(),)) if row[0].startswith('plugin:'))
    from agent.permission.mcp_access import _owned
    result.update(row.consumer_plugin_id for row in _owned() if row.consumer_plugin_id)
    return result


def owner_connection_settings():
    store = ResourceGrantStore()
    rows = store.connection_settings(user_id=current_user(), subject='user:' + current_user())
    from core.security.migration import resource_authority_migrated
    if not resource_authority_migrated():
        # Discover legacy owners before the first edit. The business control runs
        # the existing verified migration before showing or mutating their scope.
        from config import conf
        from agent.permission.security_policy import validate_patch
        policy = conf().get('security_policy')
        original = policy.get('connections', {}) if isinstance(policy, dict) else {}
        try:
            legacy = validate_patch({'connections': original})['connections']
        except ValueError:
            legacy = {key: {'allowed': False, 'scopes': []} for key in original if isinstance(key, str) and key} if isinstance(original, dict) else {}
        known = {row['id'] for row in rows}
        rows += [row for row in store.summarize_connections([(key, 0, value) for key, value in legacy.items()]) if row['id'] not in known]
    return rows


class FeatureAccessControl:
    def __init__(self, module, identifier, *, kind=None, agent=None):
        self.module, self.identifier, self.kind = module, identifier, kind
        self.agent = agent

    def _subjects(self):
        from core.security.policy_store import RunPolicyStore
        with RunPolicyStore().transaction() as db:
            rows = [dict(id=f'{kind}:{agent}:{subject}', kind=kind, agent=agent, subject=subject)
                    for agent, kind, subject in db.execute(
                        'SELECT agent,kind,subject FROM run_policies WHERE user=? AND kind!=?', (current_user(), 'template'))]
        from core.plugins.management import default_store
        from core.plugins.package_ops import PackageStore
        rows += [dict(id='plugin:' + path.parent.name, kind='plugin', subject=path.parent.name)
                 for path in PackageStore(default_store().root).packages.glob('*/current.json')]
        known = {row['id'] for row in rows}
        rows += [dict(id='plugin:' + key, kind='plugin', subject=key, retained=True) for key in sorted(retained_plugins()) if 'plugin:' + key not in known]
        return rows

    def _sources(self):
        from core.data.runtime import current_service
        return current_service().list_sources()

    @staticmethod
    def _source_resources(source):
        if source['provider'] == 'elearning':
            config = source.get('config') or {}
            account = config.get('account_id') or source.get('account_id')
            return [dict(kind='course', id=f'{account}:{course}', name=f"{source.get('name', 'eLearning')} · {course}", operations=['read'])
                    for course in config.get('course_ids', []) if account and isinstance(course, str) and course.isdigit()]
        kind = 'folder' if source['provider'] == 'email' else 'data-source'
        identifier = source.get('account_id') if kind == 'folder' else source['id']
        return [dict(kind=kind, id=identifier, name=source.get('name') or source['id'], operations=['read'])] if identifier else []

    def _scope(self):
        require_owner()
        if self.module == 'tasks' or self.module == 'capabilities' and self.kind != 'connection':
            raise ValueError('Independent task/provider authorization management was removed')
        from core.security.migration import ensure_migrated
        ensure_migrated()
        if not isinstance(self.identifier, str) or not self.identifier or len(self.identifier) > 512:
            raise ValueError('Feature identifier is required')
        subjects = self._subjects()
        pinned = None
        connection = None
        self.retired = False
        from agent.tools.tool_manager import ToolManager
        if self.module == 'data':
            source = next((row for row in self._sources() if row['id'] == self.identifier), None)
            if source is None:
                raise ValueError('Data source not found')
            available = self._source_resources(source)
            # Preserve revoked/stale courses from the same account even if no longer selected.
            account = (source.get('config') or {}).get('account_id') or source.get('account_id')
            def matches(resource):
                return (resource in [dict(kind=row['kind'], id=row['id']) for row in available] or
                        source['provider'] == 'elearning' and resource['kind'] == 'course' and account and
                        resource['id'].startswith(str(account) + ':'))
        elif self.module in {'sessions', 'capabilities'}:
            if self.module == 'sessions':
                from agent.registry import get_agent_registry
                self.agent = self.agent or get_agent_registry().default_agent_id
                pinned = next((row for row in subjects if row['id'] == f'session:{self.agent}:{self.identifier}'), None)
                if pinned is None:
                    raise ValueError('Conversation not found')
            elif self.kind == 'connection':
                connection = self.identifier
                if connection not in {row['id'] for row in owner_connection_settings()} or connection.startswith('mcp:'):
                    raise ValueError('Connection settings not found')
            else:
                raise ValueError('Unknown provider kind')
            if pinned:
                subjects = [pinned]
                available = [item for source in self._sources() for item in self._source_resources(source)]
                available += [dict(kind='connection', id='mcp:' + row['name'], name=row['name'],
                                   operations=['read', 'write', 'delete', 'external_send', 'system_execute'])
                              for row in ToolManager()._load_mcp_configs()]
                if self.retired:
                    available = []
                def matches(resource):
                    return True
            else:
                available = [dict(kind='connection', id=connection, name=self.identifier,
                                  operations=['read', 'write', 'delete', 'external_send', 'system_execute'])] if not self.retired and self.kind == 'mcp' else []
                def matches(resource):
                    return resource == dict(kind='connection', id=connection)
        else:
            raise ValueError('Unknown feature module')
        return subjects, available, pinned, connection, matches

    def inventory(self):
        subjects, available, pinned, connection, matches = self._scope()
        rows = ResourceGrantStore().list_owned(current_user())
        grants = [row for row in rows if matches(row['resource']) and (not pinned or row['subject'] == pinned['id'])]
        from agent.permission.mcp_access import inventory
        inherited = [row for row in inventory() if (
            connection and 'mcp:' + row['server'] == connection or
            self.module == 'data' and row['scoped'] and row['resources'] and all(matches(resource) for resource in row['resources']))]
        result = dict(grants=grants, available=available, subjects=subjects, inherited=inherited)
        subject = pinned['id'] if pinned else 'user:' + current_user()
        restrictions = ResourceGrantStore().connection_settings(user_id=current_user(), subject=subject)
        result['restrictions'] = restrictions if pinned else [row for row in restrictions if row['id'] == connection] if connection else []
        if connection and not self.retired:
            result['connection'] = next((row for row in restrictions if row['id'] == connection),
                dict(id=connection, allowed=True, scopes=[], revision=0))
        result['retired'] = self.retired
        return result

    def save(self, body):
        state = self.inventory()  # Resolve/validate real business ownership before every write.
        if not isinstance(body, dict):
            raise ValueError('Invalid access settings')
        body = dict(body)
        action = body.pop('action', None)
        store = ResourceGrantStore()
        if action == 'issue':
            if set(body) - {'subject', 'resource', 'operations', 'purposes', 'valid_until'}:
                raise ValueError('Invalid access fields')
            if body.get('subject') not in {row['id'] for row in state['subjects'] if not row.get('retained')}:
                raise ValueError('Select an existing execution subject')
            available = next((row for row in state['available'] if dict(kind=row['kind'], id=row['id']) == body.get('resource')), None)
            if not available or not set(body.get('operations') or []) <= set(available['operations']):
                raise PermissionError('Resource is outside these feature settings')
            store.issue(user_id=current_user(), **body)
        elif action == 'revoke' and set(body) == {'grant_id', 'revision'}:
            if body['grant_id'] not in {row['grant_id'] for row in state['grants']}:
                raise PermissionError('Grant is outside these feature settings')
            store.revoke(body['grant_id'], user_id=current_user(), expected_revision=body['revision'])
        elif action == 'legacy':
            if body.get('id') not in {row['id'] for row in state['inherited']}:
                raise PermissionError('Legacy grant is outside these feature settings')
            if self.retired:
                if body.get('revoked') is not True:
                    raise PermissionError('Removed connections only allow revocation')
                body = {key: body[key] for key in ('id', 'revision', 'revoked')}
            if self.module == 'data' and 'resources' in body:
                original = next(row for row in state['inherited'] if row['id'] == body['id'])
                keys = {(row['kind'], row['id']) for row in [*state['available'], *original['resources']]}
                if any((row.get('kind'), row.get('id')) not in keys for row in body['resources']):
                    raise PermissionError('Legacy resources are outside this source')
            from agent.permission.mcp_access import update
            update(body)
        elif action == 'connection' and set(body) <= {'connection_id', 'allowed', 'scopes', 'revision'}:
            connection = body.pop('connection_id', None) or (state.get('connection') or {}).get('id')
            allowed_ids = {row['id'] for row in state.get('restrictions', [])}
            if state.get('connection'):
                allowed_ids.add(state['connection']['id'])
            if connection not in allowed_ids or self.retired:
                raise ValueError('Connection is outside these feature settings')
            if (type(body.get('allowed')) is not bool or not isinstance(body.get('scopes'), list) or
                    any(not isinstance(scope, str) for scope in body['scopes']) or body['allowed'] and not body['scopes']):
                raise ValueError('Select allowed operations or disable connection access')
            subjects = state['subjects']
            subject = subjects[0]['id'] if self.module in {'tasks', 'sessions'} or self.kind == 'plugins' else 'user:' + current_user()
            store.update_connection(user_id=current_user(), subject=subject, connection=connection,
                policy=dict(allowed=body['allowed'], scopes=body['scopes']), expected_revision=body.get('revision'))
        else:
            raise ValueError('Invalid access settings action')
        from agent.permission.audit import record
        record(event='resource_grant', operation=action, resource=self.module, decision='policy_change',
               reason='Owner updated access in feature settings.')
        return self.inventory()
