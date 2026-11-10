"""Official desktop packages and source checkouts use distinct digest catalogs."""
import json

import pytest


def write_catalog(path, plugin_id, digest):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'plugins': [{'pluginId': plugin_id, 'sha256': digest}]}))


@pytest.fixture
def core_layout(tmp_path, monkeypatch):
    from core.security import runtime_trust
    core = tmp_path / 'workspace' / 'Stella-Core'
    core.mkdir(parents=True)
    monkeypatch.setattr(runtime_trust, '__file__', str(core / 'core/security/runtime_trust.py'))
    monkeypatch.delattr(runtime_trust.sys, 'frozen', raising=False)
    monkeypatch.delattr(runtime_trust.sys, '_MEIPASS', raising=False)
    return core


@pytest.mark.parametrize('domain', ['bills', 'calendar', 'tasks', 'briefing', 'automation', 'agent-hub', 'knowledge', 'memory'])
def test_official_desktop_digest_is_trusted_without_matching_source_digest(tmp_path, core_layout, domain):
    from core.plugins.package_ops import _sha256_tree
    from core.security.runtime_trust import verified_first_party
    plugin_id = 'stella.' + domain
    package = tmp_path / domain
    package.mkdir()
    (package / 'plugin.json').write_text(json.dumps({'id': plugin_id}))
    digest = _sha256_tree(package)
    write_catalog(core_layout / 'desktop/resources/plugin-catalog.json', plugin_id, digest)
    write_catalog(core_layout.parent / 'Stella-Plugins/catalog/official.json', plugin_id, '0' * 64)
    assert verified_first_party(package, plugin_id, digest)
    # The shipped pin must still match the current complete package bytes.
    (package / 'added.py').write_text('print("changed")')
    assert not verified_first_party(package, plugin_id, digest)
    assert not verified_first_party(package, plugin_id, _sha256_tree(package))


def test_custom_catalog_cannot_establish_first_party_trust(tmp_path, core_layout, monkeypatch):
    from core.plugins.package_ops import _sha256_tree
    from core.security.runtime_trust import verified_first_party
    package = tmp_path / 'custom'
    package.mkdir()
    (package / 'plugin.json').write_text('{"id":"stella.briefing"}')
    digest = _sha256_tree(package)
    catalog = tmp_path / 'custom-catalog.json'
    write_catalog(catalog, 'stella.briefing', digest)
    monkeypatch.setenv('STELLA_PLUGIN_CATALOG', str(catalog))
    assert not verified_first_party(package, 'stella.briefing', digest)


def test_managed_bundled_briefing_rules_need_no_owner_trust(tmp_path, core_layout, monkeypatch):
    from core.plugins.management import ManagementStore
    from core.plugins.package_ops import PackageStore
    from core.security.runtime_trust import RuntimeTrustStore
    from core.services import briefing_gateway
    store = ManagementStore(tmp_path / 'managed')
    packages = PackageStore(store.root)
    operation = packages.stage('stella-plugins-workspace/plugins/briefing')
    packages.inspect(operation.operation_id)
    packages.prepare(operation.operation_id)
    packages.commit(operation.operation_id)
    package = packages.current_package('stella.briefing')
    store.set_intent('stella.briefing', 'local-default', 'enabled')
    monkeypatch.setattr('core.plugins.management.default_store', lambda: store)
    write_catalog(core_layout / 'desktop/resources/plugin-catalog.json', 'stella.briefing', package['digest'])
    write_catalog(core_layout.parent / 'Stella-Plugins/catalog/official.json', 'stella.briefing', '0' * 64)
    monkeypatch.setenv('STELLA_BRIEFING_PLUGIN', '1')
    monkeypatch.setenv('STELLA_BRIEFING_HOST_ADAPTERS', '0')
    assert RuntimeTrustStore().choice(user_id='local-default', kind='plugin', identifier='stella.briefing', digest=package['digest']) is None
    briefing_gateway.shutdown_briefing_session()
    try:
        result = briefing_gateway.invoke_briefing('list_rules', {}, db_path=tmp_path / 'briefing' / 'rules.sqlite')
        assert result['status'] == 'succeeded', result
        assert isinstance(result['value'], list)
    finally:
        briefing_gateway.shutdown_briefing_session()
