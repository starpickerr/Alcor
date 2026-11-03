"""Fixed Core-pinned business modules shared by the two managed account connectors."""
import sys
import json
from pathlib import Path
import threading
from types import ModuleType

from core.mcp.managed import ManagedMcpConfig, _installed_digest
from core.runtime.paths import source_digest
from core.security.mcp_runtime import _launch_inputs

_MODULES = {}
_LOCK = threading.Lock()
# Only these stdlib-only business modules are trusted. server.py and arbitrary
# extension code are never imported into Core. SQL is from the same snapshot.
_TRUSTED_MODULES = {
    'email': ('hosts', 'imap', 'schema', 'service'),
    'elearning': ('errors', 'locking', 'utils', 'models', 'store', 'client', 'courses',
                  'documents', 'source', 'file_sync', 'service'),
}


def load_business(config):
    domain = config.get('name')
    if (not isinstance(config, ManagedMcpConfig) or domain not in _TRUSTED_MODULES or
            config.data_root is None or not config.package_digest or
            config.package_digest != _installed_digest(domain)):
        raise PermissionError('Managed account connector requires the Core-pinned provider')
    _, root = _launch_inputs(config)
    snapshot = {}
    expected = config.package_digest
    if getattr(sys, 'frozen', False):
        resources = Path(sys._MEIPASS) / 'resources'
        catalog = json.loads((resources / 'mcp-providers.json').read_text())
        expected = catalog['providers'][domain]['connectorSha256']
        root = resources / 'trusted-connectors' / domain
    digest = source_digest(root, snapshot=snapshot)
    if digest != expected:
        raise PermissionError('Managed account connector source does not match the Core distribution')
    with _LOCK:
        if digest not in _MODULES:
            package_name = '_stella_trusted_' + domain + '_' + digest
            package = ModuleType(package_name)
            package.__path__ = []
            sys.modules[package_name] = package
            loaded = {}
            try:
                for name in _TRUSTED_MODULES[domain]:
                    path = domain + '_mcp/' + name + '.py'
                    module = ModuleType(package_name + '.' + name)
                    module.__package__ = package_name
                    module.__file__ = str(root / path)
                    sys.modules[module.__name__] = module
                    loaded[name] = module
                    exec(compile(snapshot[path], module.__file__, 'exec'), module.__dict__)
                    if name == 'schema':
                        sql = snapshot['email_mcp/schema.sql'].decode('utf-8')
                        module.load_schema_sql = lambda: sql
                _MODULES[digest] = loaded
            except BaseException:
                for name in [package_name, *[package_name + '.' + name for name in loaded]]:
                    sys.modules.pop(name, None)
                raise
        return _MODULES[digest]
