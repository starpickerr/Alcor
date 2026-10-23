from pathlib import Path
import importlib
import pytest

@pytest.mark.parametrize('domain,method', [('memory','list_files'), ('knowledge','list_tree')])
def test_domain_reads_with_explicit_isolation(tmp_path, monkeypatch, domain, method):
    from common import state_dir
    from core.plugins.lifecycle import domain_data_handle
    from core.plugins.package_ops import _sha256_tree
    from core.security.runtime_trust import RuntimeTrustStore
    user = tmp_path / 'owner'
    user.mkdir()
    monkeypatch.setattr(state_dir, 'user_root', lambda *args, **kwargs: user)
    monkeypatch.setattr(state_dir, 'shared_root', lambda: tmp_path)
    monkeypatch.setattr(state_dir, 'knowledge_dir', lambda **kwargs: user / 'knowledge')
    root = Path('../Stella-Plugins/plugins') / domain
    monkeypatch.setenv('STELLA_' + domain.upper() + '_PLUGIN', '1')
    monkeypatch.setenv('STELLA_' + domain.upper() + '_PLUGIN_ROOT', str(root.resolve()))
    handle = domain_data_handle('stella.' + domain, 'local-default')
    data = Path(handle['dataDir'])
    (data / ('memory' if domain == 'memory' else 'notes')).mkdir(parents=True)
    (data / ('memory/day.md' if domain == 'memory' else 'notes/page.md')).write_text('SYNTHETIC')
    RuntimeTrustStore().set(user_id='local-default', kind='plugin', identifier='stella.'+domain,
        digest=_sha256_tree(root), trusted=False)
    gateway = importlib.import_module('core.services.' + domain + '_gateway')
    try:
        result = getattr(gateway, 'invoke_' + domain)(method, {}, data_dir=data)
        assert result['status'] == 'succeeded', result
    finally:
        getattr(gateway, 'shutdown_' + domain + '_session')()


@pytest.mark.parametrize('domain,method,args', [
    ('bills', 'list_ledger', {'year': 2026, 'month': 10}),
    ('calendar', 'list_events', {}), ('todos', 'list_todos', {}),
    ('briefing', 'list_briefings', {}), ('automation', 'list_tasks', {}),
])
def test_isolated_sqlite_owners_can_use_their_core_bound_database(tmp_path, monkeypatch, domain, method, args):
    from core.plugins.package_ops import _sha256_tree
    from core.security.runtime_trust import RuntimeTrustStore
    actual = 'tasks' if domain == 'todos' else domain
    root = Path('stella-plugins-workspace/plugins') / actual
    gateway = importlib.import_module('core.services.' + domain + '_gateway')
    getattr(gateway, 'shutdown_' + domain + '_session')()
    monkeypatch.setenv('STELLA_' + domain.upper() + '_PLUGIN', '1')
    monkeypatch.setenv('STELLA_' + domain.upper() + '_PLUGIN_ROOT', str(root.resolve()))
    monkeypatch.setenv('STELLA_TODOS_PLUGIN_SCHEDULER_SYNC', '0')
    RuntimeTrustStore().set(user_id='local-default', kind='plugin', identifier='stella.' + actual,
        digest=_sha256_tree(root), trusted=False)
    values = {'db_path': tmp_path / domain / 'domain.sqlite'}
    if domain == 'automation':
        values['agent_id'] = 'default'
    try:
        result = getattr(gateway, 'invoke_' + domain)(method, args, **values)
        assert result['status'] == 'succeeded', result
        assert values['db_path'].exists()
    finally:
        getattr(gateway, 'shutdown_' + domain + '_session')()


def test_isolated_gateway_rebinds_storage_without_granting_sibling_roots(tmp_path, monkeypatch):
    from core.security.runtime_trust import RuntimeTrustStore
    from core.plugins.package_ops import _sha256_tree
    from core.services import knowledge_gateway as gateway
    root = Path('stella-plugins-workspace/plugins/knowledge')
    monkeypatch.setenv('STELLA_KNOWLEDGE_PLUGIN', '1')
    monkeypatch.setenv('STELLA_KNOWLEDGE_PLUGIN_ROOT', str(root.resolve()))
    RuntimeTrustStore().set(user_id='local-default', kind='plugin', identifier='stella.knowledge',
        digest=_sha256_tree(root), trusted=False)
    one, two = tmp_path / 'one', tmp_path / 'two'
    for directory in (one, two):
        (directory / 'notes').mkdir(parents=True)
        (directory / 'notes' / (directory.name + '.md')).write_text(directory.name)
    try:
        assert gateway.invoke_knowledge('list_tree', {}, data_dir=one)['status'] == 'succeeded'
        first = gateway._SESSION
        assert gateway.invoke_knowledge('list_tree', {}, data_dir=two)['status'] == 'succeeded'
        assert gateway._SESSION is not first
        assert first._proc is None
        with pytest.raises(PermissionError, match='bound|storage'):
            gateway._SESSION.invoke_contract(contract='stella.knowledge@1', method='list_tree', input_payload={},
                origin='ui', grant_ref='compat', data_handle={'namespace': 'stella.knowledge', 'dataDir': str(one)})
    finally:
        gateway.shutdown_knowledge_session()


def test_core_bound_domain_does_not_expose_other_domains_users_or_symlinks(tmp_path):
    import json
    from core.plugins.host import PluginSession
    from core.plugins.registry import PluginRegistry
    package = tmp_path / 'package'
    package.mkdir()
    (package / 'plugin.json').write_text(json.dumps({'manifestVersion': 1, 'id': 'sample.storage', 'name': 'Storage',
        'version': '1.0.0', 'hostApi': '^1.0.0', 'runtime': {'kind': 'python', 'entry': 'main.py', 'abi': 'cp312'},
        'permissions': [], 'contributes': {'services': ['sample.storage@1']}}))
    (package / 'main.py').write_text('''from pathlib import Path
SERVICE_DESCRIPTORS = [{'contract':'sample.storage@1','methods':[{'name':'probe','effect':'query','operation':'read','permissions':[]}]}]
def handle_invoke(params):
    result = {}
    for key, path in params['input'].items():
        try:
            if key.startswith('write_'):
                Path(path).write_text('changed'); result[key] = 'allowed'
            else: result[key] = Path(path).read_text()
        except OSError: result[key] = 'denied'
    return {'status':'succeeded','sideEffects':'none','value':result}
''')
    own = tmp_path / 'alice' / 'own'
    other_domain = tmp_path / 'alice' / 'other'
    other_user = tmp_path / 'bob' / 'own'
    paths = {}
    for key, directory in [('own', own), ('domain', other_domain), ('user', other_user)]:
        directory.mkdir(parents=True)
        file = directory / 'body.txt'
        file.write_text(key)
        paths[key] = str(file)
    (own / 'escape').symlink_to(other_domain, target_is_directory=True)
    paths['symlink'] = str(own / 'escape' / 'body.txt')
    legacy = tmp_path / 'legacy.md'
    legacy.write_text('legacy')
    paths.update(write_own=str(own / 'new.md'), write_domain=str(other_domain / 'new.md'),
        write_user=str(other_user / 'new.md'), write_symlink=str(own / 'escape' / 'new.md'),
        legacy=str(legacy), write_legacy=str(legacy))
    session = PluginSession(package, source_label='test', registry=PluginRegistry(),
        data_handle={'namespace': 'sample.storage', 'dataDir': str(own), 'legacyMainFile': str(legacy)})
    try:
        session.start()
        assert session.sandbox_state['mode'] == 'isolated'
        result = session.invoke_contract(contract='sample.storage@1', method='probe', input_payload=paths,
            origin='ui', grant_ref='compat', data_handle={'namespace': 'sample.storage', 'dataDir': str(own)})
        assert result['value'] == {'own': 'own', 'domain': 'denied', 'user': 'denied', 'symlink': 'denied',
            'write_own': 'allowed', 'write_domain': 'denied', 'write_user': 'denied', 'write_symlink': 'denied',
            'legacy': 'legacy', 'write_legacy': 'denied'}
        assert legacy.read_text() == 'legacy'
    finally:
        session.dispose()
    alias = tmp_path / 'alias'
    alias.symlink_to(other_user, target_is_directory=True)
    with pytest.raises(PermissionError, match='symlink'):
        PluginSession(package, source_label='test', registry=PluginRegistry(),
            data_handle={'namespace': 'sample.storage', 'dataDir': str(alias)})


def test_managed_third_party_needs_owner_trust_but_no_plugin_grants(tmp_path, monkeypatch):
    from core.plugins.management import ManagementStore
    from core.plugins.package_ops import PackageStore
    from core.plugins.host import PluginSession
    from core.plugins.registry import PluginRegistry
    from core.security.runtime_trust import RuntimeTrustStore
    store = ManagementStore(tmp_path / 'managed')
    packages = PackageStore(store.root)
    operation = packages.stage('samples/plugins/alpha')
    packages.inspect(operation.operation_id)
    packages.prepare(operation.operation_id)
    packages.commit(operation.operation_id)
    store.set_intent('stella.sample.alpha', 'local-default', 'enabled')
    monkeypatch.setattr('core.plugins.management.default_store', lambda: store)
    current = packages.current_package('stella.sample.alpha')
    session = PluginSession(Path(current['path']), source_label='installed', registry=PluginRegistry())
    try:
        session.start()
        kwargs = dict(contract='stella.sample.alpha@1', method='ping', input_payload={}, origin='ui', grant_ref='compat')
        with pytest.raises(PermissionError, match='trust'):
            session.invoke_contract(**kwargs)
        RuntimeTrustStore().set(user_id='local-default', kind='plugin', identifier=session.plugin_id,
            digest=session.package_digest, trusted=True)
        assert session.invoke_contract(**kwargs)['status'] == 'succeeded'
        RuntimeTrustStore().set(user_id='local-default', kind='plugin', identifier=session.plugin_id,
            digest=session.package_digest, trusted=False)
        with pytest.raises(PermissionError, match='trust'):
            session.invoke_contract(**kwargs)
    finally:
        session.dispose()


@pytest.mark.parametrize('domain', ['bills', 'memory', 'knowledge'])
def test_real_isolated_migration_uses_bound_scratch_and_preserves_backup(tmp_path, domain):
    from core.plugins.management import ManagementStore
    from core.plugins.migration import MigrationCoordinator
    from core.plugins.package_ops import _sha256_tree
    from core.security.runtime_trust import RuntimeTrustStore
    root = Path('stella-plugins-workspace/plugins') / domain
    RuntimeTrustStore().set(user_id='local-default', kind='plugin', identifier='stella.' + domain,
        digest=_sha256_tree(root), trusted=False)
    if domain == 'bills':
        import sqlite3
        path = tmp_path / 'domain' / 'bills.db'
        path.parent.mkdir()
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE owner_kept(value TEXT)')
            db.execute("INSERT INTO owner_kept VALUES ('synthetic')")
        handle = {'dbPath': str(path), 'namespace': 'stella.bills'}
    else:
        path = tmp_path / 'domain'
        (path / ('memory' if domain == 'memory' else 'notes')).mkdir(parents=True)
        (path / ('memory' if domain == 'memory' else 'notes') / 'kept.md').write_text('synthetic')
        handle = {'dataDir': str(path), 'namespace': 'stella.' + domain}
        if domain == 'memory':
            legacy = tmp_path / 'legacy'
            legacy.mkdir()
            (legacy / 'old.md').write_text('legacy synthetic')
            main = tmp_path / 'MEMORY.md'
            main.write_text('main synthetic')
            handle.update(legacyMemoryDir=str(legacy), legacyMainFile=str(main))
    result = MigrationCoordinator(ManagementStore(tmp_path / 'packages')).run(root,
        user_id='local-default', data_handle=handle)
    assert result['stage'] == 'data_selected'
    backup = Path(result['facts']['backupRef'])
    if domain == 'bills':
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT value FROM owner_kept').fetchone()[0] == 'synthetic'
        with sqlite3.connect(backup) as db:
            assert not db.execute("SELECT name FROM sqlite_master WHERE name='_stella_migrations'").fetchone()
    else:
        assert (path / ('memory' if domain == 'memory' else 'notes') / 'kept.md').read_text() == 'synthetic'
        assert not (backup / '.stella-migrations.sqlite').exists()
        if domain == 'memory':
            assert (path / 'memory/old.md').read_text() == 'legacy synthetic'
            assert (path / 'MEMORY.md').read_text() == 'main synthetic'
            assert (legacy / 'old.md').read_text() == 'legacy synthetic'


def test_isolated_tasks_binds_core_legacy_calendar_import_as_read_only(tmp_path, monkeypatch):
    import sqlite3
    from core.services import todos_gateway as gateway
    from core.plugins.package_ops import _sha256_tree
    from core.security.runtime_trust import RuntimeTrustStore
    root = Path('stella-plugins-workspace/plugins/tasks')
    monkeypatch.setenv('STELLA_TODOS_PLUGIN', '1')
    monkeypatch.setenv('STELLA_TODOS_PLUGIN_ROOT', str(root.resolve()))
    legacy = tmp_path / 'legacy' / 'calendar.db'
    legacy.parent.mkdir()
    with sqlite3.connect(legacy) as db:
        db.execute('CREATE TABLE owner_kept(value TEXT)')
        db.execute("INSERT INTO owner_kept VALUES ('synthetic')")
    before = legacy.read_bytes()
    RuntimeTrustStore().set(user_id='local-default', kind='plugin', identifier='stella.tasks',
        digest=_sha256_tree(root), trusted=False)
    try:
        result = gateway.invoke_todos('list_todos', {}, db_path=tmp_path / 'tasks' / 'todos.db',
            legacy_calendar_db_path=legacy)
        assert result['status'] == 'succeeded'
        assert legacy.read_bytes() == before
    finally:
        gateway.shutdown_todos_session()
