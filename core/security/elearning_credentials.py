"""Declared credentials in original eLearning settings and account session files."""
from hashlib import sha256
import json
from pathlib import Path

from core.plugins.locks import package_lock
from core.security import credential_values, env_store
from core.security.control import require_owner
from core.security.keychain import KeychainUnavailable
from core.security.storage import private_atomic_write

_FIELDS = ('password', 'token')
_DEFAULT_BASE = 'https://elearning.fudan.edu.cn'


class ElearningCredentialStore:
    def __init__(self, root, *, user_id):
        self.root = Path(root).resolve()
        self.path = self.root / 'elearning.json'
        self.user_id = user_id

    def _lock(self):
        return package_lock(self.root, 'configuration-credentials', exclusive=True)

    @staticmethod
    def _read(path):
        value = json.loads(path.read_text()) if path.is_file() else {}
        if not isinstance(value, dict):
            raise ValueError('eLearning configuration must be an object; original file retained')
        return value

    @staticmethod
    def _identity(config):
        base, username = config.get('base_url', _DEFAULT_BASE), config.get('username', '')
        if not isinstance(base, str) or not isinstance(username, str):
            raise ValueError('eLearning account identity is invalid; original file retained')
        return base.rstrip('/'), username

    def account_id(self, config):
        base, username = self._identity(config)
        return 'canvas-' + sha256((base + '|' + username).encode()).hexdigest()[:16]

    def session_path(self, config):
        return self.root / 'elearning-sessions' / (self.account_id(config) + '.json')

    def _name(self, config, field):
        return 'ELEARNING:' + json.dumps([self.user_id, *self._identity(config), field], ensure_ascii=False)

    def _revision(self):
        return sha256(self.path.read_bytes() if self.path.is_file() else b'').hexdigest()

    def _session_revision(self, config):
        path = self.session_path(config)
        content = path.read_bytes() if path.is_file() else b''
        return sha256(self._revision().encode() + content).hexdigest()

    @staticmethod
    def _write(path, data):
        private_atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))

    @staticmethod
    def _state(path, name, value):
        if not isinstance(value, str):
            return 'repair_required'
        if not value:
            return 'not_configured'
        if not value.startswith(credential_values.REFERENCE):
            return 'configured'
        try:
            credential_values.account(name, value)
        except OSError:
            return 'repair_required'
        found = credential_values.statuses(path, {name: value}, env_store.keychain_for)[name]
        return 'unavailable' if found is None else 'configured' if found else 'repair_required'

    @staticmethod
    def _session_valid(data):
        return (isinstance(data, dict) and isinstance(data.get('cookies'), list) and
                all(isinstance(cookie, dict) and all(isinstance(cookie.get(key), str)
                    for key in ('domain', 'name', 'value')) for cookie in data['cookies']))

    def inventory(self):
        require_owner()
        with self._lock():
            config = self._read(self.path)
            fields = [{'key': field, 'state': self._state(self.path, self._name(config, field), config.get(field, ''))}
                      for field in _FIELDS]
            path = self.session_path(config)
            if not path.exists():
                state = 'not_configured'
            else:
                try:
                    data = self._read(path)
                    if 'credential' in data:
                        reference = data['credential']
                        state = (self._state(path, self._name(config, 'session'), reference)
                                 if isinstance(reference, str) and reference.startswith(credential_values.REFERENCE)
                                 else 'repair_required')
                    else:
                        state = 'configured' if self._session_valid(data) else 'repair_required'
                except (ValueError, UnicodeError):
                    state = 'repair_required'
            return {'account_id': self.account_id(config), 'account': config.get('username', ''),
                    'revision': self._revision(), 'session_revision': self._session_revision(config),
                    'fields': [*fields, {'key': 'session', 'state': state}]}

    def migrate_settings(self):
        require_owner()
        with self._lock():
            config = self._read(self.path)
            selected = {self._name(config, field): config[field] for field in _FIELDS
                        if isinstance(config.get(field), str)}
            def commit(values):
                changed = {field: values[self._name(config, field)] for field in _FIELDS
                           if self._name(config, field) in values}
                if any(config[field] != value for field, value in changed.items()):
                    self._write(self.path, {**config, **changed})
            credential_values.publish(self.path, selected, selected, env_store.keychain_for, commit)

    def save_field(self, field, value, *, expected_revision):
        require_owner()
        if field not in _FIELDS or not isinstance(value, str) or value.startswith(credential_values.REFERENCE):
            raise ValueError('Enter an eLearning credential through its original settings')
        with self._lock():
            if self._revision() != expected_revision:
                raise ValueError('eLearning settings changed; reload before saving')
            config = self._read(self.path)
            name = self._name(config, field)
            before = {name: config[field]} if isinstance(config.get(field), str) else {}
            credential_values.publish(self.path, {name: value}, before, env_store.keychain_for,
                                      lambda saved: self._write(self.path, {**config, field: saved[name]}))

    def publish_settings(self, config, *, expected_revision):
        """Original business store publishes a complete validated settings edit."""
        require_owner()
        with self._lock():
            if self._revision() != expected_revision:
                raise ValueError('eLearning settings changed; reload before saving')
            previous = self._read(self.path)
            before = {self._name(previous, field): previous[field] for field in _FIELDS
                      if isinstance(previous.get(field), str)}
            values = {self._name(config, field): config[field] for field in _FIELDS
                      if isinstance(config.get(field), str)}
            def commit(saved):
                result = dict(config)
                for field in _FIELDS:
                    if self._name(config, field) in saved:
                        result[field] = saved[self._name(config, field)]
                self._write(self.path, result)
            credential_values.publish(self.path, values, before, env_store.keychain_for, commit)

    def migrate_session(self):
        require_owner()
        with self._lock():
            config = self._read(self.path)
            path = self.session_path(config)
            if not path.is_file():
                return
            data = self._read(path)
            if 'credential' in data:
                return  # Unavailable/corrupt refs remain repairable; never reinterpret as cookies.
            if not self._session_valid(data):
                raise ValueError('Saved eLearning login state needs repair; original file retained')
            name = self._name(config, 'session')
            credential_values.publish(path, {name: json.dumps(data, ensure_ascii=False)}, {}, env_store.keychain_for,
                                      lambda saved: self._write(path, {'credential': saved[name]}))

    def resolve(self, field):
        """Trusted connector only: resolve the one field actually needed."""
        if field not in _FIELDS:
            raise ValueError('Unknown eLearning credential field')
        with self._lock():
            config = self._read(self.path)
            value = config.get(field, '')
            if value == '':
                return ''
            return self._resolve(self.path, self._name(config, field), value)

    @staticmethod
    def _resolve(path, name, value):
        if not isinstance(value, str) or not value.startswith(credential_values.REFERENCE):
            raise KeychainUnavailable('eLearning credential needs repair in its original settings')
        values, _ = credential_values.resolve(path, {name: value}, env_store.keychain_for)
        return values[name]

    def load_session(self):
        """Trusted connector only; no cached or plaintext session fallback."""
        with self._lock():
            config = self._read(self.path)
            path = self.session_path(config)
            data = self._read(path)
            value = self._resolve(path, self._name(config, 'session'), data.get('credential'))
            decoded = json.loads(value)
            if not self._session_valid(decoded):
                raise KeychainUnavailable('Saved eLearning login state needs repair')
            return decoded

    def save_session(self, data, *, expected_revision):
        """Trusted authentication connector publishes only a verified fresh login."""
        if not self._session_valid(data):
            raise ValueError('Invalid eLearning session; existing login retained')
        with self._lock():
            config = self._read(self.path)
            if self._session_revision(config) != expected_revision:
                raise ValueError('eLearning account or session changed; stale login was not saved')
            path = self.session_path(config)
            previous = self._read(path)
            name = self._name(config, 'session')
            before = {name: previous['credential']} if isinstance(previous.get('credential'), str) else {}
            credential_values.publish(path, {name: json.dumps(data, ensure_ascii=False)}, before, env_store.keychain_for,
                                      lambda saved: self._write(path, {'credential': saved[name]}))

    def clear_session(self, *, expected_revision):
        require_owner()
        with self._lock():
            config = self._read(self.path)
            if self._session_revision(config) != expected_revision:
                raise ValueError('eLearning account or session changed; reload before removing')
            path = self.session_path(config)
            data = self._read(path)
            name = self._name(config, 'session')
            before = {name: data['credential']} if isinstance(data.get('credential'), str) else {}
            credential_values.publish(path, {}, before, env_store.keychain_for,
                                      lambda saved: path.unlink(missing_ok=True))
