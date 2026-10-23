"""Normalize existing registries; inventory never starts an extension or tool."""
from pathlib import Path

KINDS = ('plugins', 'mcp', 'skills', 'tools')
DOCUMENT_LIMIT = 262_144


def _document(root):
    root = Path(root).resolve()
    for name in ('README.md', 'readme.md', 'README.MD'):
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            continue
        with path.open('rb') as stream:
            return stream.read(DOCUMENT_LIMIT).decode('utf-8', errors='replace')
    return ''


class CapabilityCatalog:
    def __init__(self, workspace_root, *, plugin_manager=None, skill_manager=None,
                 tool_rows=None, mcp_rows=None):
        self.workspace_root = Path(workspace_root)
        self.plugin_manager = plugin_manager
        self.skill_manager = skill_manager
        self.tool_rows = tool_rows or self._tools
        self.mcp_rows = mcp_rows or self._mcp

    @staticmethod
    def _tools():
        from agent.tools.catalog import tools_payload
        return tools_payload()['tools']

    @staticmethod
    def _mcp():
        from agent.tools.tool_manager import ToolManager
        return ToolManager().mcp_catalog()

    def _skills(self):
        if self.skill_manager is None:
            from agent.skills.manager import SkillManager
            self.skill_manager = SkillManager(custom_dir=str(self.workspace_root / 'skills'))
        return list(self.skill_manager.get_skills_config().values())

    def _plugins(self):
        if self.plugin_manager is None:
            from core.plugins.bootstrap import _default_catalog_path
            from core.plugins.lifecycle import PluginManager
            from core.plugins.management import default_store
            self.plugin_manager = PluginManager(default_store(), catalog_path=_default_catalog_path())
        from common.runtime_identity import current_identity
        return self.plugin_manager.inventory(current_identity().user_id or 'local-default')

    def _raw(self, kind):
        if kind not in KINDS:
            raise ValueError('Unknown capability type')
        return {'plugins': self._plugins, 'tools': self.tool_rows,
                'skills': self._skills, 'mcp': self.mcp_rows}[kind]()

    @staticmethod
    def _summary(kind, row):
        if kind == 'plugins':
            return dict(kind=kind, id=row['pluginId'], name=row['name'],
                        description=row.get('manifest', {}).get('description', ''),
                        enabled=row['intent'] == 'enabled', state=row['state'] if row['intent'] == 'enabled' else row['intent'],
                        source='installed', version=row.get('version'))
        if kind == 'tools':
            return dict(kind=kind, id=row['id'], name=row['title'], description=row['intro'],
                        enabled=row['enabled'], state=('available' if row['available'] else 'unavailable') if row['enabled'] else 'disabled',
                        source=row.get('source', {}).get('type', 'builtin'), version=None)
        if kind == 'mcp':
            return dict(kind=kind, id=row['name'], name=row['name'], description=row.get('description') or
                        {'zh': '通过 MCP 连接提供工具与服务。', 'en': 'Tools and services from an MCP connection.'},
                        enabled=row['enabled'], state=row['state'], source=row['transport'], version=None)
        return dict(kind=kind, id=row['name'], name=row.get('display_name') or row['name'],
                    description=row.get('description', ''), enabled=row['enabled'],
                    state='enabled' if row['enabled'] else 'disabled', source=row.get('source', 'custom'), version=None)

    def list(self):
        items, errors = [], {}
        for kind in KINDS:
            try:
                rows = self._raw(kind)
                items.extend(self._summary(kind, row) for row in rows if kind != 'tools' or
                             row.get('source', {}).get('type', 'builtin') == 'builtin')
            except Exception:
                # Error strings from configuration/registries may contain secrets.
                errors[kind] = 'Capability inventory is unavailable. Retry this category.'
        return dict(status='success', items=items, errors=errors)

    def detail(self, kind, identifier):
        if not isinstance(identifier, str) or not identifier or len(identifier) > 512 or '\0' in identifier:
            raise ValueError('Invalid capability ID')
        keys = {'plugins': 'pluginId', 'tools': 'id', 'skills': 'name', 'mcp': 'name'}
        if kind not in keys:
            raise ValueError('Unknown capability type')
        row = next((item for item in self._raw(kind) if item.get(keys[kind]) == identifier), None)
        if row is None:
            raise KeyError('Capability not found')
        detail = {**self._summary(kind, row), 'tabs': ['overview'], 'overview': '', 'features': [], 'runtime': {}}
        detail['overview'] = detail['description']
        if kind == 'tools':
            detail['overview'] = row.get('detail') or detail['description']
            detail['reason'] = row.get('reason')
        elif kind == 'skills':
            entry = self.skill_manager.get_skill(identifier)
            detail.update(tabs=['overview', 'content'], content=entry.skill.content[:DOCUMENT_LIMIT] if entry else '')
        elif kind == 'mcp':
            configurable = identifier in {'email', 'elearning'} and row.get('settings_url') == '/' + identifier
            detail['tabs'] = ['overview', 'features'] + (['configuration'] if configurable else []) + ['runtime']
            detail['features'] = [dict(id=tool['name'], name=tool['name'], description=tool.get('description', '')) for tool in row.get('tools', [])]
            detail['runtime'] = dict(transport=row['transport'], state=row['state'], toolCount=len(detail['features']))
        else:
            detail['tabs'] = ['overview', 'features', 'configuration', 'runtime']
            detail['runtime'] = dict(state=detail['state'], generation=row.get('generation', 0), services=row.get('services', []))
            if row.get('digest'):
                current = self.plugin_manager.packages.current_package(identifier)
                if current:
                    try:
                        from core.plugins.package_ops import _sha256_tree
                        if current.get('digest') == row['digest'] and _sha256_tree(Path(current['path'])) == row['digest']:
                            detail['overview'] = _document(current['path']) or detail['description']
                    except OSError:
                        pass
            # Contract contributions are metadata, not a reason to start workers.
            contributions = row.get('manifest', {}).get('contributes', {})
            for category in ('tools', 'skills', 'services', 'pages'):
                for item in contributions.get(category, []):
                    name = item if isinstance(item, str) else item.get('id') or item.get('name')
                    if name:
                        detail['features'].append(dict(id=name, name=name,
                            description=item.get('description', '') if isinstance(item, dict) else '', type=category))
            try:
                for tool in self.tool_rows():
                    if identifier in tool.get('source', {}).get('plugin_ids', []):
                        feature = dict(id=tool['id'], name=tool['title'], description=tool['intro'],
                                       type='tools', target=dict(kind='tools', id=tool['id']))
                        detail['features'] = [item for item in detail['features'] if item['id'] != tool['id']]
                        detail['features'].append(feature)
            except Exception:
                pass  # Registered manifest contributions remain visible.
        return detail
