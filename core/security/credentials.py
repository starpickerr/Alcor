"""Owner-only projection of declared credential references and public status.

No arbitrary extension files are inspected. Values returned by owner adapters
are projected here; neither secret values nor internal references leave Core.
"""
from hashlib import sha256
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


def credential_metadata():
    from agent.permission.center import secrets
    from config import MODEL_CONFIG_TO_ENV
    for row in secrets():
        name = row['name']
        model = 'model' in row['source'] or name in MODEL_CONFIG_TO_ENV.values() or name.startswith('CUSTOM:') or name == 'BOCHA_API_KEY'
        url = '/models?credential=' + quote(name, safe='') if model else '/security?tab=resources&credential=' + quote(name, safe='')
        yield dict(reference='store:' + name, owner='model' if model else 'connection',
            account=row.get('display_name') or name, kind='api-key', configured=row['configured'],
            configure_url=url, reason='')
    yield from plugin_credentials()
    from agent.tools.tool_manager import ToolManager
    from core.mcp.managed import ManagedMcpConfig
    from application.api import Application
    configs = ToolManager()._load_mcp_configs()
    from agent.tools.mcp.mcp_oauth import credential_statuses
    known = {config['name'] for config in configs}
    for status in credential_statuses():
        name = status['server']
        yield dict(reference=f'mcp:{name}:oauth', owner='mcp:' + name, account='', kind='oauth',
            configured=status['configured'], configure_url=_configuration('mcp', name),
            reason=status.get('reason') or ('' if name in known else 'Connection is no longer configured; saved credentials are preserved.'))
    for config in configs:
        name = config['name']
        owner, url = 'mcp:' + name, _configuration('mcp', name)
        for field, value in (config.get('env') or {}).items():
            if _SECRET_NAME.search(field):
                yield dict(reference=f'mcp:{name}:env:{field}', owner=owner, account='', kind=field,
                    configured=bool(value) if config.get('enabled', True) else None,
                    configure_url=url, reason='' if config.get('enabled', True) else 'Provider is disabled; its saved configuration is preserved.')
        if not isinstance(config, ManagedMcpConfig) or name not in {'email', 'elearning'}:
            continue
        if not config.get('enabled', True):
            yield dict(reference=f'mcp:{name}:owner', owner=owner, account='', kind='password', configured=None,
                configure_url=url, reason='Provider is disabled; open its configuration to inspect saved accounts.')
            continue
        try:
            if name == 'email':
                rows = Application.port('email').list_accounts().get('mailboxes', [])
                for account in rows:
                    yield dict(reference=f'mcp:email:{account["id"]}:password', owner=owner,
                        account=account.get('user') or account['id'], kind='password',
                        configured=account.get('password_set'), configure_url=url, reason='')
            else:
                account = Application.port('elearning').public_settings()
                yield dict(reference=f'mcp:elearning:{account.get("account_id") or "owner"}:password', owner=owner,
                    account=account.get('username') or '', kind='password', configured=account.get('password_set'),
                    configure_url=url, reason='')
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
