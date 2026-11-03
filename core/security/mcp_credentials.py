"""Declared static MCP credentials in their original JSON document, not a second account store."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

from core.security import credential_values, env_store
from core.security.control import require_owner

_SECRET = re.compile(r'(?:^|[_-])(?:PASSWORD|PASSWD|TOKEN|SECRET|CREDENTIAL|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY)$', re.I)
_HEADER = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def _secret(area, key):
    return bool(_SECRET.search(key) or area == 'headers' and key.lower() in {'authorization', 'proxy-authorization', 'cookie'})


def _declared(row, area):
    return row.get('secretHeaders' if area == 'headers' else 'secretEnv') or []


def _header_value(value):
    if not isinstance(value, str) or any(character in value for character in '\r\n\0'):
        raise ValueError('MCP header value is invalid; repair it through the original configuration')
    try:
        value.encode('latin-1')
    except UnicodeError:
        raise ValueError('MCP header value contains unsupported characters') from None


def _rows(data, root_key):
    if root_key is not None:
        raw = data.get(root_key, []) if isinstance(data, dict) else None
    elif isinstance(data, dict) and 'mcpServers' in data:
        raw = data['mcpServers']
    elif isinstance(data, dict) and 'mcp_servers' in data:
        raw = data['mcp_servers']
    else:
        raw = data
    if isinstance(raw, dict):
        rows = list(raw.items())
    elif isinstance(raw, list):
        rows = [(row.get('name') if isinstance(row, dict) else None, row) for row in raw]
    else:
        raise ValueError('MCP configuration must contain a server list or mapping; original document retained')
    seen = set()
    for name, row in rows:
        if (not isinstance(name, str) or not name or len(name) > 512 or '\0' in name or name in seen
                or not isinstance(row, dict) or row.get('name', name) != name):
            raise ValueError('MCP server identities are missing or ambiguous; original document retained')
        seen.add(name)
        for area in ('headers', 'env'):
            values = row.get(area) or {}
            if not isinstance(values, dict) or any(not isinstance(key, str) or not key for key in values):
                raise ValueError('MCP headers and environment must be named objects')
            if area == 'headers' and len({key.lower() for key in values}) != len(values):
                raise ValueError('MCP header identities are duplicated')
            if area == 'headers' and any(not _HEADER.fullmatch(key) for key in values):
                raise ValueError('MCP header name is invalid')
            declared = _declared(row, area)
            if (not isinstance(declared, list) or any(not isinstance(key, str) or not key for key in declared)
                    or len(set(key.lower() if area == 'headers' else key for key in declared)) != len(declared)
                    or area == 'headers' and any(not _HEADER.fullmatch(key) for key in declared)):
                raise ValueError('MCP secret field declarations must be unique field names')
    return rows


def _config(name, row):
    result = {'name': name, **deepcopy(row)}
    result.setdefault('type', 'sse' if 'url' in row else 'stdio')
    result.pop('settingsUrl', None)  # Public JSON cannot assign a native settings entry.
    return result


def _identifier(name, row, area, key):
    receiver = row.get('url') or [row.get('command'), row.get('args') or []]
    return 'MCP:' + json.dumps([name, receiver, area, key.lower() if area == 'headers' else key], separators=(',', ':'))


def _fields(name, row):
    result = {}
    for area in ('headers', 'env'):
        values = row.get(area) or {}
        declared = {key.lower() if area == 'headers' else key for key in _declared(row, area)}
        keys = {key.lower() if area == 'headers' else key: key for key in (*_declared(row, area), *values)}
        for normalized, key in keys.items():
            value = values.get(key, '')
            if (_secret(area, key) or normalized in declared
                    or isinstance(value, str) and value.startswith(credential_values.REFERENCE)):
                result[(area, key)] = _identifier(name, row, area, key)
    return result


class StoredMcpConfig(dict):
    def __init__(self, values, store):
        super().__init__(values)
        self.credential_store = store
        self.credential_source = (str(store.path.resolve()), store.root_key)


class McpCredentialStore:
    def __init__(self, path, *, root_key=None):
        self.path, self.root_key = Path(path), root_key

    def _read(self):
        content = self.path.read_bytes()
        data = json.loads(content)
        return data, dict(_rows(data, self.root_key)), sha256(content).hexdigest()

    def configs(self):
        _, rows, _ = self._read()
        return [StoredMcpConfig(_config(name, row), self) for name, row in rows.items()]

    def _publish(self, data, name, row, changes):
        from core.security.storage import private_atomic_write
        fields = _fields(name, row)
        before = {fields[pair]: row[pair[0]][pair[1]] for pair in changes
                  if pair in fields and isinstance((row.get(pair[0]) or {}).get(pair[1]), str)}
        for area, key in changes:
            if area == 'headers':
                _header_value(changes[(area, key)])
            fields.setdefault((area, key), _identifier(name, row, area, key))
        selected = {fields[pair]: value for pair, value in changes.items() if value}
        if sys.platform != 'darwin' and any(not value.startswith(credential_values.REFERENCE) for value in selected.values()):
            raise PermissionError('Protected MCP credential storage requires macOS Keychain')
        def commit(saved):
            for (area, key), identifier in fields.items():
                if (area, key) not in changes:
                    continue
                if identifier in saved:
                    row.setdefault(area, {})[key] = saved[identifier]
                else:
                    row.get(area, {}).pop(key, None)
            private_atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2))
        credential_values.publish(self.path, selected, before, env_store.keychain_for, commit)

    def inventory(self, name, *, migrate=False):
        require_owner()
        from core.plugins.locks import package_lock
        with package_lock(self.path.parent, 'configuration-credentials', exclusive=True):
            data, rows, revision = self._read()
            row = rows.get(name)
            if row is None:
                raise ValueError('MCP server is no longer configured')
            fields = _fields(name, row)
            legacy = {pair: (row.get(pair[0]) or {}).get(pair[1]) for pair in fields
                      if isinstance((row.get(pair[0]) or {}).get(pair[1]), str) and row[pair[0]][pair[1]]
                      and not row[pair[0]][pair[1]].startswith(credential_values.REFERENCE)}
            if migrate and legacy:
                try:
                    self._publish(data, name, row, legacy)
                except (OSError, PermissionError, ValueError):
                    pass  # Return repair metadata from the actual file, never the unpublished candidate.
                _, rows, revision = self._read()
                row = rows[name]
            result = []
            for (area, key), identifier in fields.items():
                value = (row.get(area) or {}).get(key, '')
                state = 'not_configured' if value == '' else 'repair_required'
                if isinstance(value, str) and value.startswith(credential_values.REFERENCE):
                    status = credential_values.statuses(self.path, {identifier: value}, env_store.keychain_for)[identifier]
                    try:
                        credential_values.account(identifier, value)
                        state = 'unavailable' if status is None else 'configured' if status else 'repair_required'
                    except OSError:
                        pass
                result.append(dict(area=area, key=key, state=state))
            return dict(revision=revision, fields=result)

    def save(self, name, area, key, value, *, expected_revision):
        require_owner()
        if (area not in {'headers', 'env'} or not isinstance(key, str) or not isinstance(value, str)
                or area == 'headers' and not _HEADER.fullmatch(key)):
            raise ValueError('Invalid MCP credential update')
        from core.plugins.locks import package_lock
        with package_lock(self.path.parent, 'configuration-credentials', exclusive=True):
            data, rows, revision = self._read()
            if revision != expected_revision:
                raise ValueError('MCP configuration changed; refresh and retain the draft')
            row = rows.get(name)
            if row is not None and area == 'headers':
                key = next((existing for existing in (*(row.get(area) or {}), *_declared(row, area))
                            if existing.lower() == key.lower()), key)
            if row is None or not (_secret(area, key) or (area, key) in _fields(name, row)):
                raise ValueError('Unknown declared MCP credential')
            self._publish(data, name, row, {(area, key): value})
        return self.inventory(name)


def runtime_headers(config, url):
    """Trusted HTTP adapter only. Resolve just the headers needed by this request."""
    from core.security.connections import service_id
    if service_id(url)[1] != service_id(config.get('url') or '')[1]:
        raise PermissionError('MCP authentication cannot change the configured receiving service')
    headers = dict(config.get('headers') or {})
    if not isinstance(config, StoredMcpConfig):
        if any(isinstance(value, str) and value.startswith(credential_values.REFERENCE) for value in headers.values()):
            raise PermissionError('MCP credential reference has no trusted configuration binding')
        for value in headers.values():
            _header_value(value)
        return headers
    store = config.credential_store
    _, rows, _ = store._read()
    name = config['name']
    if name not in rows or _config(name, rows[name]) != dict(config):
        raise PermissionError('MCP configuration changed; reload this connection before sending authentication')
    fields = {pair: identifier for pair, identifier in _fields(name, rows[name]).items() if pair[0] == 'headers'}
    values = {identifier: headers.get(pair[1], '') for pair, identifier in fields.items()}
    if any(not isinstance(value, str) for value in values.values()):
        raise ValueError('Saved MCP authentication needs repair')
    decoded, _ = credential_values.resolve(store.path, values, env_store.keychain_for)
    for (_, key), identifier in fields.items():
        headers[key] = decoded[identifier]
    for value in headers.values():
        _header_value(value)
    return {key: value for key, value in headers.items() if value != ''}


def has_authentication(config, headers):
    names = {key.lower() for area, key in _fields(config.get('name', ''), config) if area == 'headers'}
    names.add('authorization')  # Includes a bearer supplied by the fixed OAuth adapter.
    return any(key.lower() in names and value for key, value in headers.items())
