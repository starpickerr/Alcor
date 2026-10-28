"""Normalize existing registries; inventory never starts an extension or tool."""
from pathlib import Path

from core.capabilities.descriptions import clean_copy, description_for
from core.capabilities.features import mcp_features, plugin_features

KINDS = ('plugins', 'mcp', 'skills', 'tools')
DOCUMENT_LIMIT = 262_144


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
        from agent.tools.provenance import plugin_tool_available, tool_source
        from agent.tools.tool_manager import ToolManager
        rows = list(tools_payload()['tools'])
        manager = ToolManager()
        known = {row['id'] for row in rows}
        for identifier, definition in manager.tool_classes.copy().items():
            source = tool_source(identifier)
            if identifier in known or source['type'] != 'plugin':
                continue
            description = clean_copy(getattr(definition, 'description', ''))
            text = description if isinstance(description, str) else next(iter(description.values()), '')
            head, separator, _ = text.partition('. ')
            rows.append(dict(id=identifier, title=clean_copy(getattr(definition, 'title', '')) or (head + '.' if separator else head) or identifier,
                             intro=description, detail=description, toolDescription=description,
                             enabled=manager.capability_settings.enabled('tool', identifier), available=plugin_tool_available(identifier),
                             source=source, **({'inputSchema': definition.params} if isinstance(getattr(definition, 'params', None), dict) else {})))
        return rows

    @staticmethod
    def _mcp():
        from agent.tools.tool_manager import ToolManager
        from agent.tools.mcp.mcp_oauth import credential_statuses
        rows = ToolManager().mcp_catalog()
        known = {row['name'] for row in rows}
        return [*rows, *[dict(name=item['server'], transport='retained', enabled=False, state='removed', tools=[], retained=True,
            description={'zh': '连接已移除，已保存的 OAuth 授权可在原配置页删除。',
                         'en': 'Connection removed. Saved OAuth authorization can be removed from its configuration.'})
            for item in credential_statuses() if item['server'] not in known]]

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
        rows = {'plugins': self._plugins, 'tools': self.tool_rows,
                'skills': self._skills, 'mcp': self.mcp_rows}[kind]()
        if kind == 'plugins':
            from common.runtime_identity import current_identity
            from core.plugins.management import default_store
            from core.security.access_settings import retained_plugins
            known = {row['pluginId'] for row in rows}
            retained = set(retained_plugins()) | set(default_store().credential_plugins(current_identity().user_id or 'local-default'))
            rows = [*rows, *[dict(pluginId=key, name=key, intent='removed', state='stopped', version=None, retained=True,
                manifest={'description': {'zh': '插件已移除。保留的配置、凭据及访问限制可通过原入口处理。',
                                          'en': 'Plugin removed. Retained configuration, credentials and access restrictions remain accessible.'}})
                for key in sorted(retained) if key not in known]]
        if kind == 'mcp':
            from core.security.access_settings import retained_connections
            known = {row['name'] for row in rows}
            rows = [*rows, *[dict(name=key[4:], enabled=False, state='disabled', transport='retained', tools=[], retained=True,
                description={'zh': '连接已移除。现有访问限制仍继续生效。',
                             'en': 'Connection removed. Existing access restrictions remain in effect.'})
                for key in sorted(retained_connections()) if key.startswith('mcp:') and key[4:] not in known]]
        return rows

    @staticmethod
    def _summary(kind, row):
        description = description_for(kind, row)
        if kind == 'plugins':
            return dict(kind=kind, id=row['pluginId'], name=row['name'],
                        description=description,
                        enabled=row['intent'] == 'enabled', state=row['state'] if row['intent'] == 'enabled' else row['intent'],
                        source='installed', version=row.get('version'), **({'retained': True} if row.get('retained') else {}))
        if kind == 'tools':
            return dict(kind=kind, id=row['id'], name=row['title'], description=description,
                        enabled=row['enabled'], state=('available' if row['available'] else 'unavailable') if row['enabled'] else 'disabled',
                        source=row.get('source', {}).get('type', 'builtin'), version=None)
        if kind == 'mcp':
            return dict(kind=kind, id=row['name'], name=row['name'], description=description,
                        enabled=row['enabled'], state=row['state'], source=row['transport'], version=row.get('version'),
                        **({'retained': True} if row.get('retained') else {}))
        return dict(kind=kind, id=row['name'], name=row.get('display_name') or row['name'],
                    description=description, enabled=row['enabled'],
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
        from core.security.access_settings import owner_connection_settings
        connection_settings = [row['id'] for row in owner_connection_settings() if not row['id'].startswith('mcp:')]
        return dict(status='success', items=items, errors=errors, connectionSettings=connection_settings)

    def detail(self, kind, identifier):
        if not isinstance(identifier, str) or not identifier or len(identifier) > 512 or '\0' in identifier:
            raise ValueError('Invalid capability ID')
        keys = {'plugins': 'pluginId', 'tools': 'id', 'skills': 'name', 'mcp': 'name'}
        if kind not in keys:
            raise ValueError('Unknown capability type')
        row = next((item for item in self._raw(kind) if item.get(keys[kind]) == identifier), None)
        if row is None:
            raise KeyError('Capability not found')
        detail = {**self._summary(kind, row), 'tabs': [], 'overview': '', 'features': [], 'runtime': {}}
        detail['overview'] = detail['description']
        if kind == 'tools':
            detail['overview'] = clean_copy(row.get('detail')) or detail['description']
            detail['reason'] = row.get('reason')
        elif kind == 'skills':
            entry = self.skill_manager.get_skill(identifier)
            detail.update(tabs=['content'], content=entry.skill.content[:DOCUMENT_LIMIT] if entry else '')
        elif kind == 'mcp':
            detail['tabs'] = ['features', 'configuration', 'about']
            detail['features'] = mcp_features(row)
            if row.get('settings_url'):
                detail['configuration'] = dict(settingsUrl=row['settings_url'])
            detail['runtime'] = dict(state=row['state'])
        else:
            detail['tabs'] = ['features', 'configuration', 'about']
            detail['runtime'] = dict(state=row['state'])
            if row.get('error'):
                from core.log_redaction import redact
                detail['runtime']['error'] = redact(row['error'])[:12000]
            try:
                adapters = self.tool_rows()
            except Exception:
                adapters = []  # A broken adapter catalog must not hide declared pages.
            detail['features'] = plugin_features(row, adapters)
        if row.get('retained'):
            detail.update(features=[], runtime={})
        return detail
