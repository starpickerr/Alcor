"""Plugin install/enable is independent from the retired grant system."""
from pathlib import Path
import json
from core.plugins.lifecycle import PluginManager
from core.plugins.management import ManagementStore
from core.plugins.package_ops import _sha256_tree


def test_install_enable_and_disable_do_not_issue_plugin_grants(tmp_path, monkeypatch):
    from core.plugins import management
    from core.plugins.authz import DEFAULT_AUTHZ
    root = Path('stella-plugins-workspace/plugins/bills').resolve()
    manifest = json.loads((root / 'plugin.json').read_text())
    store = ManagementStore(tmp_path / 'packages')
    monkeypatch.setattr(management, 'default_store', lambda: store)
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps({'coreApi': '1.0.0', 'plugins': [{'pluginId': 'stella.bills',
        'version': manifest['version'], 'hostApi': manifest['hostApi'], 'path': str(root), 'sha256': _sha256_tree(root)}]}))
    manager = PluginManager(store, catalog_path=catalog)
    before = DEFAULT_AUTHZ.scoped_grants('local-default')
    manager.install('stella.bills', user_id='local-default', data_handle={
        'namespace': 'stella.bills', 'dbPath': str(tmp_path / 'bills.db')})
    manager.set_intent('stella.bills', 'local-default', 'disabled')
    manager.set_intent('stella.bills', 'local-default', 'enabled')
    assert store.instance('stella.bills', 'local-default')['intent'] == 'enabled'
    assert DEFAULT_AUTHZ.scoped_grants('local-default') == before
