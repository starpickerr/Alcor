"""Native Briefing peer workflows use live owner policy, without minting grants."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.plugins.authz import PluginAuthorizationStore


@pytest.fixture
def peers(tmp_path, monkeypatch, request):
    from common import state_dir
    from core.plugins import management
    from core.plugins.management import ManagementStore
    from core.plugins.package_ops import PackageStore
    from core.services import briefing_gateway, calendar_gateway, todos_gateway

    user = getattr(request, 'param', 'local-default')
    monkeypatch.setenv('STELLA_PLUGIN_USER', user)
    store = ManagementStore(tmp_path / 'packages')
    packages = PackageStore(store.root)
    authz = PluginAuthorizationStore(path=tmp_path / 'authorization.sqlite')
    monkeypatch.setattr(management, 'default_store', lambda: store)
    gateways = {'briefing': briefing_gateway, 'calendar': calendar_gateway, 'todos': todos_gateway}
    try:
        for name, gateway in gateways.items():
            getattr(gateway, f'shutdown_{name}_session')()
            domain = 'tasks' if name == 'todos' else name
            root = Path(__file__).resolve().parents[2] / 'Stella-Plugins/plugins' / domain
            op = packages.stage(root)
            packages.inspect(op.operation_id)
            packages.prepare(op.operation_id)
            packages.commit(op.operation_id)
            store.set_intent(f'stella.{domain}', user, 'enabled')
            monkeypatch.setattr(gateway, 'DEFAULT_AUTHZ', authz)
            monkeypatch.setattr(state_dir, name + '_db', lambda ensure=True, name=name: tmp_path / f'{name}.sqlite')

        def call(method, **params):
            return briefing_gateway.invoke_briefing(method, params, db_path=tmp_path / 'briefing.sqlite')

        def ok(method, **params):
            result = call(method, **params)
            assert result['status'] == 'succeeded', result
            return result.get('value')

        def publish(due='2099-10-25T08:00:00Z'):
            source = ok('upsert_source_item', payload={
                'source': 'email', 'account_id': 'school', 'source_id': 'assignment', 'title': '完成作业',
                'occurred_at': '2026-10-22T08:00:00Z',
                'payload': {'information_type': 'assignment', 'due_at': due}})['item']
            ok('start_briefing', briefing_date='2026-10-22')
            return ok('complete_briefing', briefing_id='brf-2026-10-22', payload={
                'status': 'completed', 'items': [{'source_item_id': source['id'], 'section': 'action'}]})

        yield SimpleNamespace(authz=authz, ok=ok, call=call, publish=publish, path=tmp_path, user=user)
    finally:
        for name, gateway in gateways.items():
            getattr(gateway, f'shutdown_{name}_session')()


@pytest.mark.parametrize('kind', ['todo', 'calendar'])
def test_default_owner_grants_allow_native_create_repeated_create_and_linked_views(peers, kind):
    detail = peers.publish()
    item = detail['items'][0]['id']
    payload = {'operation': 'create', 'kind': kind}
    if kind == 'calendar':
        payload.update(start_at='2099-10-25T08:00:00Z', end_at='2099-10-25T09:00:00Z')
    count = len(peers.authz.scoped_grants(peers.user))
    first = peers.ok('update_action', item_id=item, payload=payload)
    assert first['state'] == 'succeeded', first
    again = peers.ok('update_action', item_id=item, payload={'operation': 'create', 'kind': kind})
    assert again['target_id'] == first['target_id']
    targets = peers.ok('get_action_targets', kind=kind, query='作业')
    assert [row['id'] for row in targets] == [first['target_id']]
    for method, params in [('get_briefing', {'briefing_id': 'brf-2026-10-22'}),
                           ('get_revision', {'briefing_id': 'brf-2026-10-22', 'number': 1}),
                           ('get_overview', {})]:
        receipt = peers.ok(method, **params)['actions'][item][kind]
        assert receipt['business']['title'] == '完成作业'
        assert receipt['state'] == 'succeeded'
    assert len(peers.authz.scoped_grants(peers.user)) == count


@pytest.mark.parametrize('background', [False, True])
def test_default_owner_grants_allow_projection_and_sync(peers, background):
    from core.plugins.caller import caller_origin_scope

    item = peers.publish()['items'][0]['id']
    with caller_origin_scope('scheduler' if background else 'desktop'):
        result = peers.ok('dispatch_outbox', limit=20)
        assert result['pending'] == 0 and result['projected'] == 2, result
        receipt = peers.ok('update_action', item_id=item, payload={'operation': 'create', 'kind': 'todo'})
        assert receipt['state'] == 'succeeded', receipt
        peers.publish(due='2099-10-26T08:00:00Z')
        result = peers.ok('dispatch_outbox', limit=20)
        assert result['pending'] == 0 and result['projected'] == 2, result
        receipt = peers.ok('get_briefing', briefing_id='brf-2026-10-22')['actions'][item]['todo']
        assert receipt['business']['due_on'] == '2099-10-26'


def test_obsolete_plugin_grant_does_not_block_native_workflow(peers):
    peers.authz.issue(grant_ref='tasks-default-grant', plugin_id='stella.tasks', user_id=peers.user,
        capabilities=[], purposes=[])
    peers.authz.revoke('tasks-default-grant')
    item = peers.publish()['items'][0]['id']
    receipt = peers.ok('update_action', item_id=item, payload={'operation': 'create', 'kind': 'todo'})
    assert receipt['state'] == 'succeeded', receipt


def test_disabled_owner_hides_enrichment_and_preserves_pending_projection(peers, monkeypatch):
    item = peers.publish()['items'][0]['id']
    target = peers.ok('update_action', item_id=item, payload={'operation': 'create', 'kind': 'todo'})['target_id']
    from core.plugins.management import default_store
    default_store().set_intent('stella.tasks', peers.user, 'disabled')
    receipt = peers.ok('get_briefing', briefing_id='brf-2026-10-22')['actions'][item]['todo']
    assert receipt['target_id'] == target and receipt['state'] == 'succeeded'
    assert receipt['business'] == {'id': target, 'unresolved': True, 'access': 'denied'}
    denied = peers.call('get_action_targets', kind='todo', query='')
    assert denied['status'] == 'failed' and denied['error']['code'] == 'permission_denied'
    # Unchanged revisions can be acknowledged without another peer write.
    # A changed revision must stay pending when its write authorization is revoked.
    peers.publish(due='2099-10-26T08:00:00Z')
    assert peers.ok('dispatch_outbox')['pending'] == 1
    default_store().set_intent('stella.tasks', peers.user, 'enabled')
    assert peers.ok('dispatch_outbox')['pending'] == 0
    assert peers.ok('get_briefing', briefing_id='brf-2026-10-22')['actions'][item]['todo']['business']['title'] == '完成作业'


@pytest.mark.parametrize('peers', ['alice'], indirect=True)
def test_nondefault_user_uses_only_its_own_owner_grants(peers):
    item = peers.publish()['items'][0]['id']
    receipt = peers.ok('update_action', item_id=item, payload={'operation': 'create', 'kind': 'todo'})
    assert receipt['state'] == 'succeeded', receipt
    assert peers.authz.scoped_grants('local-default') == []


def test_native_worker_reads_need_no_plugin_grant(peers):
    from core.services import briefing_gateway

    peers.publish()
    result = briefing_gateway.ensure_briefing_session().invoke_contract(
        contract='stella.briefing@1', method='get_action_targets', input_payload={'kind': 'todo', 'query': ''},
        origin='system', grant_ref='briefing-default-grant',
        data_handle={'dbPath': str(peers.path / 'briefing.sqlite'), 'namespace': 'stella.briefing'})
    assert result['status'] == 'succeeded'


def test_native_reminder_configuration_reaches_task_owner(peers):
    item = peers.publish()['items'][0]['id']
    peers.ok('update_action', item_id=item, payload={'operation': 'create', 'kind': 'todo'})
    reminder = peers.ok('update_action', item_id=item, payload={
        'operation': 'create', 'kind': 'reminder', 'remind_at': '2099-10-24T08:00:00Z'})
    assert reminder['business']['remind_at'] == '2099-10-24T08:00:00+00:00'
    assert reminder['result']['operation'] in {'reminder_pending', 'reminder_configured'}
    assert 'scoped grant denied' not in reminder['error']


@pytest.mark.parametrize(('root', 'domain', 'method', 'capability', 'allowed'), [
    ('get_briefing', 'tasks', 'get_todo', 'tasks.read', True),
    ('get_revision', 'calendar', 'get_event', 'calendar.read', True),
    ('complete_briefing', 'tasks', 'create_todo', 'tasks.write', False),
    ('run_pipeline', 'calendar', 'create_event', 'calendar.write', False),
    ('get_action_targets', 'tasks', 'list_todos', 'tasks.read', True),
    ('get_action_targets', 'tasks', 'create_todo', 'tasks.write', False),
    ('update_action', 'tasks', 'has_source_tombstone', 'tasks.read', True),
    ('update_action', 'calendar', 'find_briefing_sources', 'calendar.read', True),
    ('dispatch_outbox', 'tasks', 'sync_briefing_facts', 'tasks.write', True),
    ('dispatch_outbox', 'calendar', 'sync_briefing_facts', 'calendar.write', True),
    ('update_action', 'tasks', 'delete_todo', 'tasks.write', False),
    ('dispatch_outbox', 'calendar', 'delete_event', 'calendar.write', False),
    ('list_sources', 'tasks', 'list_todos', 'tasks.read', False),
])
def test_native_workflow_authority_is_limited_to_its_peer_operations(root, domain, method, capability, allowed):
    from datetime import datetime, timedelta, timezone
    from core.plugins.caller import delegation_scope
    from core.security.briefing_peers import briefing_peer_workflow

    authz = PluginAuthorizationStore()
    parent = f'{domain}-default-grant'
    # Deliberately grant the owner the tested operation, including dangerous
    # methods: only the native workflow allowlist should restrict inheritance.
    authz.issue_scoped(grant_ref=f'{parent}:interactive:{capability}', parent_ref=parent,
        plugin_id=f'stella.{domain}', user_id='local-default', consumer_plugin_id=f'stella.{domain}',
        capability=capability, methods=[method], resources=[{'kind': 'user', 'id': 'local-default'}],
        purpose='interactive')
    scope = dict(grant_ref=parent, plugin_id=f'stella.{domain}', user_id='local-default',
        consumer_plugin_id='stella.briefing', method=method, required_capabilities=[capability],
        resources=[{'kind': 'user', 'id': 'local-default'}], purpose='interactive')
    with briefing_peer_workflow(root), delegation_scope(
            {'origin': 'ui', 'grantRef': 'briefing-default-grant'}, 'stella.briefing',
            datetime.now(timezone.utc) + timedelta(seconds=10)):
        if allowed:
            authz.check_scoped(**scope)
        else:
            with pytest.raises(PermissionError):
                authz.check_scoped(**scope)
    with pytest.raises(PermissionError):
        authz.check_scoped(**scope)


@pytest.mark.parametrize('change', ['nested', 'delegated_root', 'consumer', 'caller', 'user', 'parent',
                                    'briefing_grant', 'capability', 'purpose'])
def test_native_peer_authority_cannot_cross_caller_or_identity_boundaries(change):
    from contextlib import ExitStack
    from datetime import datetime, timedelta, timezone
    from core.plugins.caller import consumer_plugin_scope, delegation_scope
    from core.security.briefing_peers import briefing_peer_workflow

    authz = PluginAuthorizationStore()
    authz.issue_scoped(grant_ref='tasks-default-grant:interactive:tasks.write', parent_ref='tasks-default-grant',
        plugin_id='stella.tasks', user_id='local-default', consumer_plugin_id='stella.tasks',
        capability='tasks.write', methods=['create_todo'], resources=[{'kind': 'user', 'id': 'local-default'}],
        purpose='interactive')
    scope = dict(grant_ref='tasks-default-grant', plugin_id='stella.tasks', user_id='local-default',
        consumer_plugin_id='stella.briefing', method='create_todo', required_capabilities=['tasks.write'],
        resources=[{'kind': 'user', 'id': 'local-default'}], purpose='interactive')
    context = {'origin': 'ui', 'grantRef': 'briefing-default-grant'}
    if change == 'nested':
        context['delegationChain'] = ['third.party']
    elif change == 'user':
        scope['user_id'] = 'other-user'
    elif change == 'parent':
        scope['grant_ref'] = 'arbitrary-parent'
    elif change == 'briefing_grant':
        context['grantRef'] = 'arbitrary-briefing-grant'
    elif change == 'capability':
        scope['required_capabilities'] = ['tasks.write', 'tasks.read']
    elif change == 'purpose':
        scope['purpose'] = 'background'
    deadline = datetime.now(timezone.utc) + timedelta(seconds=10)
    with ExitStack() as stack:
        if change == 'delegated_root':
            stack.enter_context(delegation_scope({'origin': 'ui'}, 'third.party', deadline))
        if change == 'consumer':
            stack.enter_context(consumer_plugin_scope('third.party'))
        stack.enter_context(briefing_peer_workflow('update_action'))
        stack.enter_context(delegation_scope(context, 'third.party' if change == 'caller' else 'stella.briefing', deadline))
        with pytest.raises(PermissionError):
            authz.check_scoped(**scope)


def test_background_obeys_mode_while_native_ui_does_not(peers):
    from common.runtime_identity import identity_scope
    from core.security.run_policy import save_run_policy
    from core.plugins.caller import caller_origin_scope
    peers.publish()
    save_run_policy('session', 'briefing-readonly', {'filesystem_mode': 'read-only'})
    with identity_scope(session_id='briefing-readonly'), caller_origin_scope('scheduler'):
        with pytest.raises(PermissionError):
            peers.call('dispatch_outbox')
    with caller_origin_scope('desktop'):
        assert peers.ok('dispatch_outbox')['pending'] == 0


def test_native_background_workflow_does_not_replace_a_tasks_readonly_snapshot(peers):
    from common.runtime_identity import identity_scope
    from core.security.run_policy import save_run_policy
    from core.plugins.caller import caller_origin_scope
    peers.publish()
    save_run_policy('task', 'readonly-briefing-task', {'filesystem_mode': 'read-only'})
    with identity_scope(task_id='readonly-briefing-task'), caller_origin_scope('scheduler'):
        with pytest.raises(PermissionError):
            peers.call('dispatch_outbox')
