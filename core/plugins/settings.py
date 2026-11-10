"""Static package settings with Core-owned validation and category revisions."""
from __future__ import annotations

import json
import math
from pathlib import Path

from core.plugins.management import ManagementStore
from core.plugins.manifest import load_manifest
from core.plugins.package_ops import PackageStore
from core.security.control import require_owner
from core.security import credential_values, env_store


class PluginSettingsService:
    def __init__(self, management: ManagementStore):
        self.management = management
        self.packages = PackageStore(management.root)

    def descriptors(self, plugin_id: str) -> list[dict]:
        current = self.packages.current_package(plugin_id)
        if current is None:
            raise FileNotFoundError('plugin not installed')
        root = Path(current['path']).resolve()
        manifest = load_manifest(root)
        result = []
        for settings_id in manifest['contributes'].get('settings') or []:
            path = (root / 'settings' / f'{settings_id}.json').resolve()
            if not path.is_relative_to(root):
                raise PermissionError('settings resource escapes package')
            if not path.is_file():
                raise ValueError(f'package settings descriptor missing: {settings_id}')
            descriptor = json.loads(path.read_text())
            if (descriptor.get('id') != settings_id or descriptor.get('pluginId') != plugin_id or
                    descriptor.get('scope') != 'user'):
                raise ValueError('invalid settings descriptor identity')
            result.append(descriptor)
        return result

    def descriptor(self, plugin_id: str, settings_id: str) -> dict:
        descriptor = next((item for item in self.descriptors(plugin_id) if item['id'] == settings_id), None)
        if descriptor is None:
            raise KeyError(settings_id)
        return descriptor

    def read(self, plugin_id: str, user_id: str, settings_id: str, category: str, *, credential_status=False) -> dict:
        if credential_status:
            require_owner()
        descriptor = self.descriptor(plugin_id, settings_id)
        if category not in {item['id'] for item in descriptor['categories']}:
            raise ValueError('unknown settings category')
        fields = [field for field in descriptor['fields'] if field['category'] == category]
        saved = self.management.settings(plugin_id, user_id, settings_id, category)
        secrets = sorted(set(saved['secretFields']) | {field['key'] for field in fields if field.get('secret')})
        if credential_status:
            legacy = {key: saved['values'][key] for key in secrets if isinstance(saved['values'].get(key), str)
                      and saved['values'][key] and not saved['values'][key].startswith(credential_values.REFERENCE)}
            if legacy:
                try:
                    saved = self.management.save_settings(plugin_id, user_id, settings_id, category, legacy,
                        expected_revision=saved['revision'], secret_fields=secrets)
                except (OSError, PermissionError):
                    pass  # Retain the old document and offer repair/removal without exposing its value.
            saved['secretStates'] = self._states((plugin_id, user_id, settings_id, category), saved, secrets)
        defaults = {field['key']: field['default'] for field in fields if 'default' in field and not field.get('secret')}
        saved['values'] = {**defaults, **saved['values']}
        saved['secretFields'] = secrets
        for secret in saved['secretFields']:
            saved['values'].pop(secret, None)
        return saved

    def _states(self, key, saved, fields):
        values = saved['values']
        names = {field: json.dumps([*key, field], separators=(',', ':')) for field in fields}
        references = {names[field]: values[field] for field in fields
                      if isinstance(values.get(field), str) and values[field].startswith(credential_values.REFERENCE)}
        states = credential_values.statuses(self.management.path, references, env_store.keychain_for)
        result = {}
        for field in fields:
            value = values.get(field)
            if field not in values or value == '':
                result[field] = 'not_configured'
            elif names[field] not in references:
                result[field] = 'repair_required'
            else:
                try:
                    credential_values.account(names[field], value)
                except OSError:
                    result[field] = 'repair_required'
                    continue
                state = states[names[field]]
                result[field] = 'unavailable' if state is None else 'configured' if state else 'repair_required'
        return result

    def credentials(self, plugin_id, user_id):
        """Owner metadata for current declarations and retained credentials, including removed packages."""
        require_owner()
        declared = {}
        try:
            for descriptor in self.descriptors(plugin_id):
                for field in descriptor['fields']:
                    if field.get('secret'):
                        declared[(descriptor['id'], field['category'], field['key'])] = field['title']
        except (OSError, ValueError, KeyError):
            pass
        categories = set(self.management.secret_categories(plugin_id, user_id)) | {key[:2] for key in declared}
        result = []
        for settings_id, category in sorted(categories):
            saved = self.management.settings(plugin_id, user_id, settings_id, category)
            fields = sorted(set(saved['secretFields']) | {key[2] for key in declared if key[:2] == (settings_id, category)})
            states = self._states((plugin_id, user_id, settings_id, category), saved, fields)
            for field in fields:
                key = (settings_id, category, field)
                result.append(dict(settingsId=settings_id, category=category, field=field, revision=saved['revision'],
                    title=declared.get(key, field), state=states[field], retired=key not in declared))
        return result

    def remove_secret(self, plugin_id, user_id, settings_id, category, field, *, expected_revision):
        require_owner()
        saved = self.management.settings(plugin_id, user_id, settings_id, category)
        known = saved['secretFields']
        if field not in known:
            raise ValueError('unknown retained credential')
        self.management.save_settings(plugin_id, user_id, settings_id, category, {field: ''},
            expected_revision=expected_revision, secret_fields=known)
        return self.credentials(plugin_id, user_id)

    def save(self, plugin_id, user_id, settings_id, category, changes, *, expected_revision):
        require_owner()
        descriptor = self.descriptor(plugin_id, settings_id)
        fields = {field['key']: field for field in descriptor['fields'] if field['category'] == category}
        secrets = set(self.management.settings(plugin_id, user_id, settings_id, category)['secretFields'])
        secrets.update(key for key, field in fields.items() if field.get('secret'))
        if not isinstance(changes, dict) or not changes or set(changes) - set(fields):
            raise ValueError('unknown or empty settings changes')
        for key, value in changes.items():
            field = fields[key]
            if key in secrets:
                if not isinstance(value, str):
                    raise ValueError('secret settings must be strings')
                if not value:
                    continue  # An explicit empty draft removes just this credential.
            kind = field['type']
            valid = {'string': isinstance(value, str), 'integer': type(value) is int,
                     'number': type(value) in {int, float} and math.isfinite(value),
                     'boolean': type(value) is bool, 'enum': True,
                     'resourceRef': isinstance(value, dict) and set(value) == {'kind', 'id'} and
                                    all(isinstance(part, str) and part for part in value.values())}.get(kind, False)
            if not valid:
                raise ValueError(f'invalid type for {key}')
            rules = field.get('validation') or {}
            if 'enum' in rules and value not in rules['enum']:
                raise ValueError(f'invalid option for {key}')
            if isinstance(value, str):
                if not rules.get('minLength', 0) <= len(value) <= rules.get('maxLength', 65536):
                    raise ValueError(f'invalid length for {key}')
                if rules.get('pattern'):
                    import regex
                    try:
                        if not regex.fullmatch(rules['pattern'], value, timeout=0.05):
                            raise ValueError(f'invalid format for {key}')
                    except TimeoutError:
                        raise ValueError('settings validation exceeded its time budget') from None
            if type(value) in {int, float} and not rules.get('min', -math.inf) <= value <= rules.get('max', math.inf):
                raise ValueError(f'value out of range for {key}')
        self.management.save_settings(plugin_id, user_id, settings_id, category, changes,
            expected_revision=expected_revision, secret_fields=secrets)
        return self.read(plugin_id, user_id, settings_id, category, credential_status=True)
