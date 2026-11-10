"""Owner-only projection of declared credential references and public status.

No arbitrary extension files are inspected. Values returned by owner adapters
are projected here; neither secret values nor internal references leave Core.
"""
from hashlib import sha256
import json
from urllib.parse import quote, urlsplit
import re

from core.security.control import require_owner

_SECRET_NAME = re.compile(r'(?:PASSWORD|TOKEN|SECRET|API_KEY|ACCESS_KEY|PRIVATE_KEY)$', re.I)


def _configuration(kind, identifier):
    return f'/skills/{kind}/{quote(identifier, safe="")}?section=configuration'


def plugin_credentials():
    require_owner()
    from common.runtime_identity import current_identity
    from core.plugins.management import default_store
    from core.plugins.settings import PluginSettingsService
    service = PluginSettingsService(default_store())
    user = current_identity().user_id or 'local-default'
    identifiers = {pointer.parent.name for pointer in service.packages.packages.glob('*/current.json')}
    identifiers.update(service.management.credential_plugins(user))
    for identifier in sorted(identifiers):
        try:
            for item in service.credentials(identifier, user):
                yield dict(reference=f'plugin:{identifier}:{user}:{item["settingsId"]}:{item["category"]}:{item["field"]}',
                    owner='plugin:' + identifier, account=user, kind=item['title'],
                    configured={'configured': True, 'not_configured': False, 'repair_required': False}.get(item['state']),
                    configure_url=_configuration('plugins', identifier),
                    reason=('Declaration removed; the saved credential can be removed in the original configuration.' if item['retired'] else
                            'Saved credential needs repair.' if item['state'] == 'repair_required' else
                            'Credential storage is unavailable.' if item['state'] == 'unavailable' else ''))
        except (OSError, ValueError, KeyError):
            yield dict(reference=f'plugin:{identifier}:status', owner='plugin:' + identifier,
                account='', kind='credential', configured=None,
                configure_url=_configuration('plugins', identifier), reason='Credential declarations are unavailable.')


def mcp_configuration_credentials(config):
    require_owner()
    from core.security.mcp_credentials import StoredMcpConfig
    name = config['name']
    if isinstance(config, StoredMcpConfig):
        for item in config.credential_store.inventory(name)['fields']:
            state = item['state']
            yield dict(reference='mcp-config:' + json.dumps([name, item['area'], item['key']]), owner='mcp:' + name,
                account=name, kind=item['key'], configured={'configured': True, 'not_configured': False, 'repair_required': False}.get(state),
                configure_url=_configuration('mcp', name), reason='Saved credential needs repair.' if state == 'repair_required' else
                'Credential storage is unavailable.' if state == 'unavailable' else '')
    else:
        for field, value in (config.get('env') or {}).items():
            if _SECRET_NAME.search(field):
                yield dict(reference=f'mcp:{name}:env:{field}', owner='mcp:' + name, account='', kind=field,
                    configured=bool(value) if config.get('enabled', True) else None,
                    configure_url=_configuration('mcp', name),
                    reason='' if config.get('enabled', True) else 'Provider is disabled; its saved configuration is preserved.')


def credential_metadata():
    from agent.permission.center import secrets
    from config import MODEL_CONFIG_TO_ENV
    for row in secrets():
        name = row['name']
        model = 'model' in row['source'] or name in MODEL_CONFIG_TO_ENV.values() or name.startswith('CUSTOM:') or name == 'BOCHA_API_KEY'
        url = '/models?credential=' + quote(name, safe='') if model else '/settings'
        yield dict(reference='store:' + name, owner='model' if model else 'connection',
            account=row.get('display_name') or name, kind='api-key', configured=row['configured'],
            configure_url=url, reason='')
    yield from plugin_credentials()
    from agent.tools.tool_manager import ToolManager
    from core.mcp.managed import ManagedMcpConfig
    configuration_unavailable = False
    try:
        configs = ToolManager()._load_mcp_configs()
    except (OSError, ValueError, KeyError):
        configuration_unavailable = True
        configs = []
        yield dict(reference='mcp-config:unavailable', owner='mcp', account='', kind='credential', configured=None,
            configure_url='/skills?tab=mcp', reason='MCP configuration is unavailable; saved credentials are preserved.')
    from agent.tools.mcp.mcp_oauth import credential_statuses
    known = {config['name'] for config in configs}
    for status in credential_statuses():
        name = status['server']
        yield dict(reference=f'mcp:{name}:oauth', owner='mcp:' + name, account='', kind='oauth',
            configured=status['configured'], configure_url=_configuration('mcp', name),
            reason=status.get('reason') or ('Connection configuration is unavailable; saved credentials are preserved.' if configuration_unavailable
                else '' if name in known else 'Connection is no longer configured; saved credentials are preserved.'))
    for config in configs:
        name = config['name']
        owner, url = 'mcp:' + name, _configuration('mcp', name)
        yield from mcp_configuration_credentials(config)
        if not isinstance(config, ManagedMcpConfig) or name not in {'email', 'elearning'}:
            continue
        if name == 'email':
            from core.security.email_credentials import EmailCredentialStore
            import sqlite3
            from common.runtime_identity import current_identity
            try:
                if config.data_root is None:
                    raise ValueError('Mailbox has no bound account store')
                path = config.data_root / 'email.db'
                rows = (EmailCredentialStore(path, user_id=current_identity().user_id or 'local-default').inventory()
                        if path.is_file() else [])
                for account in rows:
                    state = account['state']
                    yield dict(reference=f'mcp:email:{account["id"]}:password', owner=owner,
                               account=account['account'], kind='password', configure_url=url,
                               configured=None if state == 'unavailable' else state == 'configured',
                               reason='Saved credential needs repair in its original settings.' if state == 'repair_required'
                               else 'Saved credential is unavailable.' if state == 'unavailable' else '')
            except (OSError, ValueError, sqlite3.Error):
                yield dict(reference='mcp:email:owner', owner=owner, account='', kind='credential', configured=None,
                           configure_url=url, reason='Mailbox credential status is unavailable; saved data is preserved.')
            continue
        try:
            from common.runtime_identity import current_identity
            from core.security.elearning_credentials import ElearningCredentialStore
            if config.data_root is None:
                raise ValueError('eLearning has no bound account store')
            account = ElearningCredentialStore(config.data_root, user_id=current_identity().user_id or 'local-default').inventory()
            for field in account['fields']:
                state = field['state']
                yield dict(reference=f'mcp:elearning:{account["account_id"]}:{field["key"]}', owner=owner,
                    account=account['account'], kind=field['key'], configured=None if state == 'unavailable' else state == 'configured',
                    configure_url=url, reason='Saved credential needs repair in its original settings.' if state == 'repair_required'
                    else 'Saved credential is unavailable.' if state == 'unavailable' else '')
        except (ValueError, RuntimeError, OSError, PermissionError):
            yield dict(reference=f'mcp:{name}:owner', owner=owner, account='', kind='credential', configured=None,
                configure_url=url, reason='The owner credential status is unavailable; saved data has not been changed.')


class CredentialDirectory:
    def __init__(self, *, metadata=None):
        self.metadata = metadata or credential_metadata

    def inventory(self):
        require_owner()
        indexed = {}
        for item in self.metadata():
            reference, owner = item.get('reference'), item.get('owner')
            if not isinstance(reference, str) or not reference or not isinstance(owner, str) or not owner:
                continue
            identifier = sha256(reference.encode()).hexdigest()
            url = item.get('configure_url')
            parsed = urlsplit(url) if isinstance(url, str) else None
            url = (url if parsed and url.startswith('/') and not url.startswith('//') and not parsed.scheme
                   and not parsed.netloc and '\\' not in url else None)
            configured = item.get('configured')
            configured = configured if type(configured) is bool else None
            current = indexed.get(identifier)
            if current is None:
                indexed[identifier] = dict(id=identifier, owners=[owner], account=str(item.get('account') or '')[:240],
                    kind=str(item.get('kind') or 'credential')[:120], configured=configured, configure_url=url,
                    reason=str(item.get('reason') or '')[:500])
            else:
                current['owners'] = sorted(set([*current['owners'], owner]))
                if current['configured'] is not configured:
                    current['configured'] = None  # Conflicting reports never claim a configured credential.
        return {'credentials': list(indexed.values())}
