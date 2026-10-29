"""Declared model fields in the existing config.json, using the shared store."""
from copy import deepcopy
import json
from pathlib import Path

from core.security import credential_values, env_store
from core.security.control import require_owner


class ConfigDocument(dict):
    def __init__(self, data, path, *, original=None):
        super().__init__(data)
        self.original = deepcopy(data if original is None else original)
        self.path = Path(path).resolve()


def read_document(path, *, default=None):
    path = Path(path)
    exists = path.exists()
    data = json.loads(path.read_text()) if exists else deepcopy(default or {})
    if not isinstance(data, dict):
        raise ValueError('Configuration must be an object')
    return ConfigDocument(data, path, original=data if exists else {})


def _locations(data):
    from config import MODEL_CONFIG_TO_ENV
    from models.catalog import PROVIDER_MODELS
    names = {name for name in MODEL_CONFIG_TO_ENV if name.endswith('_api_key')}
    names.update(meta['api_key_field'] for meta in PROVIDER_MODELS.values() if meta.get('api_key_field'))
    names.add('custom_api_key')
    result = {name: (data, name) for name in names if name in data}
    seen = set()
    for provider in data.get('custom_providers') or []:
        if not isinstance(provider, dict) or not provider.get('id'):
            if isinstance(provider, dict) and provider.get('api_key'):
                raise ValueError('Custom provider credential has no stable identity; original configuration retained')
            continue
        identifier = provider['id']
        if not isinstance(identifier, str) or identifier in seen:
            raise ValueError('Custom provider identities are invalid or duplicated; original configuration retained')
        seen.add(identifier)
        if 'api_key' in provider:
            result['CUSTOM:' + identifier] = provider, 'api_key'
    search = (data.get('tools') or {}).get('web_search') or {}
    if 'bocha_api_key' in search:
        result['BOCHA_API_KEY'] = search, 'bocha_api_key'
    for container, key in result.values():
        if not isinstance(container[key], str):
            raise ValueError('Declared model credential must be a string; original configuration retained')
    return result


def _values(locations):
    return {name: container[key] for name, (container, key) in locations.items()}


def reference_fields(data):
    return {name for name, value in _values(_locations(data)).items()
            if value.startswith(credential_values.REFERENCE)}


def resolve(path, data):
    result = deepcopy(data)
    fields = _locations(result)
    values, states = credential_values.resolve(path, _values(fields), env_store.keychain_for, strict=False)
    for name, value in values.items():
        container, key = fields[name]
        container[key] = value
    return result, states


def bind_runtime(path, data, runtime):
    """Retain only declared field names and their original configuration path."""
    if hasattr(runtime, '__dict__'):
        runtime._credential_path = Path(path).resolve()
        runtime._credential_fields = set(getattr(runtime, '_credential_fields', ())) | reference_fields(data)


def runtime_document(name):
    from config import conf
    runtime = conf()
    if name not in getattr(runtime, '_credential_fields', ()):
        return None
    return read_document(runtime._credential_path)


def runtime_value(name, fallback='', *, document=...):
    """Resolve one declared runtime credential; never revive a cached value."""
    document = runtime_document(name) if document is ... else document
    if document is None:
        return fallback
    if name.startswith('CUSTOM:'):
        matches = [item for item in document.get('custom_providers', [])
                   if isinstance(item, dict) and item.get('id') == name[len('CUSTOM:'):]]
        value = matches[0].get('api_key') if len(matches) == 1 else None
    elif name == 'BOCHA_API_KEY':
        value = ((document.get('tools') or {}).get('web_search') or {}).get('bocha_api_key')
    else:
        value = document.get(name)
    if not isinstance(value, str) or not value.startswith(credential_values.REFERENCE):
        from core.security.keychain import KeychainUnavailable
        raise KeychainUnavailable('Model credential was cleared or needs repair in its original settings')
    return credential_values.resolve(document.path, {name: value}, env_store.keychain_for)[0][name]


def model_key(name, default=''):
    from config import conf
    return runtime_value(name, conf().get(name, default) or '')


def statuses(path, data=None):
    data = read_document(path) if data is None else data
    return credential_values.statuses(path, _values(_locations(data)), env_store.keychain_for)


def public_states(path, data=None):
    data = read_document(path) if data is None else data
    values = _values(_locations(data))
    states = credential_values.statuses(path, values, env_store.keychain_for)
    return {name: ('unavailable' if state is None else 'configured' if state else
                   'repair-required' if values[name].startswith(credential_values.REFERENCE) else 'not-configured')
            for name, state in states.items()}


def write_document(path, data, *, runtime=None):
    require_owner()
    from core.plugins.locks import package_lock
    from core.security.storage import private_atomic_write
    path = Path(path)
    with package_lock(path.parent, 'configuration-credentials', exclusive=True):
        before = read_document(path)
        if isinstance(data, ConfigDocument) and (data.path != path.resolve() or data.original != before):
            raise ValueError('Configuration changed; reload before saving')
        result = deepcopy(dict(data))
        fields = _locations(result)
        candidate = deepcopy(dict(data))
        if runtime is not None:
            live_fields = _values(_locations(runtime))
            for name, (container, key) in _locations(candidate).items():
                if container[key].startswith(credential_values.REFERENCE):
                    container[key] = live_fields.get(name, '')
        def commit(saved):
            for name, value in saved.items():
                container, key = fields[name]
                container[key] = value
            private_atomic_write(path, json.dumps(result, ensure_ascii=False, indent=4))
            if runtime is not None:
                runtime.update(candidate)
                bind_runtime(path, result, runtime)
        credential_values.publish(path, _values(fields), _values(_locations(before)), env_store.keychain_for, commit)
    return result


def save_runtime(path, data, runtime):
    """Persist first; preserve live values for unchanged references without reads."""
    return write_document(path, data, runtime=runtime)
