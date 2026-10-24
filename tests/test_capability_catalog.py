"""Unified inventory exposes registered metadata, never arbitrary files or secrets."""
import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def catalog(tmp_path):
    from core.capabilities.catalog import CapabilityCatalog
    from core.plugins.package_ops import _sha256_tree
    package = tmp_path / 'package'
    package.mkdir()
    (package / 'README.md').write_text('# Fixture plugin\n\nReal package documentation.')
    plugin = {'pluginId': 'fixture.plugin', 'name': 'Fixture', 'version': '1.2.3',
              'intent': 'enabled', 'state': 'stopped', 'generation': 0, 'services': [],
              'manifest': {'description': 'Plugin description', 'contributes': {'tools': ['fixture.tool']}},
              'digest': 'a' * 64, 'contributes': {}, 'error': None}
    manager = SimpleNamespace(inventory=lambda user: [{**plugin, 'digest': _sha256_tree(package)}],
        packages=SimpleNamespace(current_package=lambda name: {'path': str(package), 'digest': _sha256_tree(package)}))
    skill = SimpleNamespace(get_skills_config=lambda: {'review': {'name': 'review', 'description': 'Review changes',
        'source': 'custom', 'enabled': True}},
        get_skill=lambda name: SimpleNamespace(skill=SimpleNamespace(content='# Review\nCheck changes.')))
    tools = [dict(id='read', enabled=True, available=True, title={'zh': '读取文件', 'en': 'Read'},
                  intro={'zh': '读取内容'}, detail={'zh': '仅在授权范围读取文件。'}, source={'type': 'builtin', 'plugin_ids': []}),
             dict(id='fixture.tool', enabled=True, available=False, title={'zh': '插件工具'},
                  intro={'zh': '插件功能'}, detail={'zh': '说明'}, source={'type': 'plugin', 'plugin_ids': ['fixture.plugin']})]
    mcp = [dict(name='docs', transport='stdio', enabled=True, state='not_started', tools=[],
                env={'SECRET': 'DO_NOT_EXPOSE'}, url='https://DO_NOT_EXPOSE', headers={'Authorization': 'DO_NOT_EXPOSE'})]
    service = CapabilityCatalog(tmp_path, plugin_manager=manager, skill_manager=skill,
                                tool_rows=lambda: tools, mcp_rows=lambda: mcp)
    return service, package


def test_list_has_four_types_and_does_not_duplicate_plugin_adapters(catalog):
    service, _ = catalog
    result = service.list()
    assert result['errors'] == {}
    assert {(row['kind'], row['id']) for row in result['items']} == {
        ('plugins', 'fixture.plugin'), ('mcp', 'docs'), ('skills', 'review'), ('tools', 'read')}
    assert 'DO_NOT_EXPOSE' not in json.dumps(result)
    assert next(row for row in result['items'] if row['kind'] == 'mcp')['state'] == 'not_started'


def test_builtin_has_only_overview_and_real_localized_copy(catalog):
    detail = catalog[0].detail('tools', 'read')
    assert detail['tabs'] == ['overview']
    assert detail['overview'] == {'zh': '仅在授权范围读取文件。'}
    assert detail['source'] == 'builtin'


def test_plugin_readme_and_features_use_registered_package_without_running_it(catalog):
    service, _ = catalog
    detail = service.detail('plugins', 'fixture.plugin')
    assert detail['overview'].startswith('# Fixture plugin')
    assert detail['state'] == 'stopped' and detail['runtime']['generation'] == 0
    assert detail['tabs'] == ['overview', 'features', 'configuration', 'runtime']
    assert any(item['id'] == 'fixture.tool' for item in detail['features'])


def test_skill_content_and_mcp_runtime_are_real_metadata(catalog):
    service, _ = catalog
    assert service.detail('skills', 'review')['content'] == '# Review\nCheck changes.'
    mcp = service.detail('mcp', 'docs')
    assert mcp['runtime'] == {'transport': 'stdio', 'state': 'not_started', 'toolCount': 0}
    assert 'DO_NOT_EXPOSE' not in json.dumps(mcp)
    assert not mcp['features']


@pytest.mark.parametrize('kind,name', [('unknown', 'read'), ('plugins', '../../outside'),
                                     ('tools', '../secret'), ('skills', '/etc/passwd'), ('mcp', 'missing')])
def test_unknown_or_traversal_ids_do_not_resolve_files(catalog, kind, name):
    with pytest.raises((KeyError, ValueError)):
        catalog[0].detail(kind, name)


def test_one_inventory_failure_keeps_other_categories(catalog):
    service, _ = catalog
    def fail():
        raise RuntimeError('DO_NOT_EXPOSE password /private/user')
    service.mcp_rows = fail
    result = service.list()
    assert {row['kind'] for row in result['items']} == {'plugins', 'skills', 'tools'}
    assert 'mcp' in result['errors']
    assert 'DO_NOT_EXPOSE' not in json.dumps(result)


def test_plugin_readme_symlink_does_not_read_outside_package(catalog, tmp_path):
    service, package = catalog
    secret = tmp_path / 'outside.md'
    secret.write_text('DO_NOT_EXPOSE')
    (package / 'README.md').unlink()
    (package / 'README.md').symlink_to(secret)
    assert 'DO_NOT_EXPOSE' not in json.dumps(service.detail('plugins', 'fixture.plugin'))


def test_plugin_readme_is_bounded(catalog):
    service, package = catalog
    (package / 'README.md').write_text('x' * 300_000)
    detail = service.detail('plugins', 'fixture.plugin')
    assert len(detail['overview']) <= 262_144


def test_handler_authenticates_before_inventory(monkeypatch):
    from channel.desktop import desktop_channel as desktop
    def deny():
        raise PermissionError('Authentication required')
    monkeypatch.setattr(desktop, '_require_auth', deny)
    with pytest.raises(PermissionError, match='Authentication required'):
        desktop.CapabilitiesHandler().GET()


def test_handler_list_and_detail_contract(monkeypatch, catalog):
    from channel.desktop import desktop_channel as desktop
    monkeypatch.setattr(desktop, '_require_auth', lambda: None)
    monkeypatch.setattr(desktop.web, 'header', lambda *args: None)
    monkeypatch.setattr(desktop, '_capability_catalog', lambda: catalog[0])
    monkeypatch.setattr(desktop.web, 'input', lambda **kwargs: SimpleNamespace(kind='', id=''))
    assert len(json.loads(desktop.CapabilitiesHandler().GET())['items']) == 4
    monkeypatch.setattr(desktop.web, 'input', lambda **kwargs: SimpleNamespace(kind='tools', id='read'))
    response = json.loads(desktop.CapabilitiesHandler().GET())
    assert response['status'] == 'success' and response['detail']['tabs'] == ['overview']


def test_plugin_adapters_appear_as_features_without_duplicate_builtin_rows(catalog):
    service, _ = catalog
    service.plugin_manager.inventory('local-default')[0]['manifest']['contributes'] = {}
    assert any(feature['id'] == 'fixture.tool' for feature in service.detail('plugins', 'fixture.plugin')['features'])


def test_http_plugin_store_failure_does_not_hide_builtin_inventory(monkeypatch, tmp_path):
    from channel.desktop import desktop_channel as desktop
    from core.capabilities.catalog import CapabilityCatalog
    monkeypatch.setattr(desktop, '_require_auth', lambda: None)
    monkeypatch.setattr(desktop.web, 'header', lambda *args: None)
    monkeypatch.setattr(desktop.web, 'input', lambda **kwargs: SimpleNamespace(kind='', id=''))
    monkeypatch.setattr(desktop, '_get_state_root', lambda: str(tmp_path))
    def fail():
        raise RuntimeError('DO_NOT_EXPOSE')
    monkeypatch.setattr('core.plugins.management.default_store', fail)
    monkeypatch.setattr(CapabilityCatalog, '_tools', staticmethod(lambda: [dict(id='read', title='Read', intro='Read', enabled=True, available=True)]))
    monkeypatch.setattr(CapabilityCatalog, '_mcp', staticmethod(lambda: []))
    monkeypatch.setattr(CapabilityCatalog, '_skills', lambda self: [])
    result = json.loads(desktop.CapabilitiesHandler().GET())
    assert result['status'] == 'success'
    assert result['items'][0]['id'] == 'read'
    assert 'plugins' in result['errors']
    assert 'DO_NOT_EXPOSE' not in json.dumps(result)


def test_package_selection_change_cannot_read_docs_under_old_metadata(catalog, tmp_path):
    from core.plugins.package_ops import _sha256_tree
    service, package = catalog
    replacement = tmp_path / 'replacement'
    replacement.mkdir()
    (replacement / 'README.md').write_text('DO_NOT_EXPOSE replacement package')
    service.plugin_manager.packages.current_package = lambda name: {'path':str(replacement), 'digest':_sha256_tree(replacement)}
    detail = service.detail('plugins', 'fixture.plugin')
    assert 'DO_NOT_EXPOSE' not in json.dumps(detail)


@pytest.mark.parametrize('plugin_id,keyword', [
    ('stella.bills', '收支'), ('stella.calendar', '日程'),
    ('stella.tasks', '事务'), ('stella.briefing', '简报'),
    ('stella.automation', '定时'), ('stella.agent-hub', '智能体'),
    ('stella.knowledge', '知识'), ('stella.memory', '记忆'),
])
def test_legacy_first_party_plugins_have_useful_descriptions_without_reinstall(catalog, plugin_id, keyword):
    service, package = catalog
    row = service.plugin_manager.inventory('local-default')[0]
    row['pluginId'] = plugin_id
    row['manifest'].pop('description', None)
    service.plugin_manager.inventory = lambda user: [row]
    (package / 'README.md').unlink()
    summary = next(item for item in service.list()['items'] if item['kind'] == 'plugins')
    assert keyword in summary['description']['zh']
    detail = service.detail('plugins', plugin_id)
    assert keyword in detail['overview']['zh']
    assert detail['overview']['en'].strip()


@pytest.mark.parametrize('server,keyword', [('email', '邮件'), ('elearning', '课程')])
def test_managed_mcp_description_is_available_before_connection(catalog, server, keyword):
    service, _ = catalog
    service.mcp_rows = lambda: [dict(name=server, transport='stdio', enabled=False, state='disabled', tools=[])]
    detail = service.detail('mcp', server)
    assert keyword in detail['overview']['zh']
    assert detail['state'] == 'disabled' and detail['features'] == []


def test_custom_mcp_overview_uses_discovered_tool_purpose_when_description_is_missing(catalog):
    service, _ = catalog
    service.mcp_rows = lambda: [dict(name='docs', transport='stdio', enabled=True, state='ready', tools=[
        dict(name='search', description='Search published research documents')])]
    detail = service.detail('mcp', 'docs')
    assert 'Search published research documents' in detail['overview']['en']
    assert 'search' in detail['description']['zh']


def test_empty_or_invalid_localizations_do_not_hide_overview_fallbacks(catalog):
    service, _ = catalog
    row = service.plugin_manager.inventory('local-default')[0]
    row['manifest']['description'] = {'zh': '  ', 'en': None, 'private': 'DO_NOT_EXPOSE'}
    service.plugin_manager.inventory = lambda user: [row]
    summary = next(item for item in service.list()['items'] if item['kind'] == 'plugins')
    assert summary['description']['zh'].strip()
    assert 'DO_NOT_EXPOSE' not in json.dumps(summary)


def test_whitespace_readme_does_not_hide_the_plugin_description(catalog):
    service, package = catalog
    (package / 'README.md').write_text(' \n\t ')
    assert service.detail('plugins', 'fixture.plugin')['overview'] == 'Plugin description'
