"""Owner-controlled MCP dependency maintenance over existing lifecycle facts."""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import shutil
import tempfile

from common.runtime_identity import current_identity
from core.mcp.managed import ManagedMcpConfig
from core.plugins.locks import package_lock
from core.plugins.management import default_store
from core.runtime.environments import EnvironmentStore
from core.runtime.paths import source_digest
from core.security.control import require_owner
from core.security.mcp_runtime import launch_identity, environment_identity, _verify_bound_implementation, _fixed_executable_entry
from core.security.runtime_trust import RuntimeTrustStore


class McpMaintenanceConfig(ManagedMcpConfig):
    """Core-only candidate launch; JSON fields cannot request maintenance."""
    def __init__(self, config, scratch):
        self.original_launch_digest = environment_identity(config)
        values = deepcopy(dict(config))
        bound = config if isinstance(config, ManagedMcpConfig) else None
        values['env'] = {key: value for key, value in (values.get('env') or {}).items()
                         if bound is None or key not in bound.credential_env}
        data = None
        if bound is not None and bound.data_root is not None:
            data = Path(scratch) / 'business'
            args = values.get('args', [])
            if args.count('--data-dir') != 1 or '--data-dir=' in ' '.join(args):
                raise ValueError('Bound MCP has no explicit candidate data-directory entry')
            index = args.index('--data-dir') + 1
            if index >= len(args) or Path(args[index]).absolute() != bound.data_root:
                raise PermissionError('Bound MCP data directory does not match its launch entry')
            args[index] = str(data)
            while '--legacy-dir' in args:
                index = args.index('--legacy-dir')
                if index + 1 >= len(args):
                    raise ValueError('Invalid bound MCP migration entry')
                del args[index:index + 2]
        super().__init__(values, data_root=data, package_digest=bound.package_digest if bound else None)
        self.maintenance_storage = Path(scratch) / 'runtime'


def namespace(name):
    # Plugin manifest IDs cannot contain ':'. No second operation database.
    return 'mcp:' + name


def admission_key(name, user, launch_digest=None):
    return namespace(name) + ':' + sha256(user.encode()).hexdigest() + ':admission' + (':' + launch_digest if launch_digest else '')


def pending(name, user):
    return [row for row in default_store().unfinished(namespace(name)) if row['userId'] == user]


def check_admission(name, user, *, config=None, launch_digest=None):
    operations = [row for row in pending(name, user) if row['stage'] in {'draining', 'selected'}]
    if not operations:
        return
    if launch_digest is None and config and config.get('command'):
        launch_digest = environment_identity(config)
    if any(launch_digest is None or row['facts'].get('environment_digest') == launch_digest for row in operations):
        raise PermissionError('MCP dependency rebuild is in maintenance; resume it before use')


@contextmanager
def admission(config, *, key=None, environment=None):
    if config.get('type', 'stdio') != 'stdio' or isinstance(config, McpMaintenanceConfig):
        yield
        return
    user = key['user_id'] if key else current_identity().user_id or 'local-default'
    digest = (environment.get('launch_digest') if environment else
              environment_identity(config) if config.get('command') else None)
    with package_lock(default_store().root, admission_key(config['name'], user, digest), exclusive=False):
        check_admission(config['name'], user, launch_digest=digest)
        yield


def rebuild_support(config):
    from agent.permission.process_sandbox import capability
    if config.get('type', 'stdio') != 'stdio':
        return False, 'Remote MCP connections have no local dependency environment.'
    if capability()['status'] != 'enabled':
        return False, 'OS isolation is unavailable; dependency validation cannot run.'
    command = Path(shutil.which(config.get('command', '')) or config.get('command', '')).name.lower()
    suffix = '.py' if command.startswith('python') else ('.js', '.mjs', '.cjs') if command in {'node', 'nodejs'} else ()
    if not suffix or not _fixed_executable_entry(config):
        return False, 'Select an explicit local Python or Node source entry to rebuild dependencies.'
    if not any(isinstance(arg, str) and arg.endswith(suffix) and Path(arg).is_file() for arg in config.get('args', [])):
        return False, 'The local source entry is unavailable; a frozen executable cannot be rebuilt here.'
    return True, ''


class McpLifecycle:
    def __init__(self, manager):
        self.manager = manager
        self.user = manager.identity.user_id or 'local-default'
        self.store = default_store()

    def _owner(self):
        require_owner()
        if (current_identity().user_id or 'local-default') != self.user:
            raise PermissionError('MCP environment belongs to another user')

    def _config(self, name):
        matches = [row for row in self.manager._load_mcp_configs() if row.get('name') == name]
        if len(matches) != 1:
            raise ValueError('Select one configured MCP provider')
        return matches[0]

    def pending(self, name):
        self._owner()
        return pending(name, self.user)

    def _facts(self, config):
        digest, package = launch_identity(config)
        _verify_bound_implementation(config, package)
        if RuntimeTrustStore().choice(user_id=self.user, kind='mcp', identifier=config['name'], digest=digest) is False:
            raise PermissionError('MCP runtime identity was revoked')
        return {'launch_digest': digest, 'environment_digest': environment_identity(config),
                'package_digest': source_digest(package),
                'workspace': str(self.manager.workspace_root), 'enabled': self.manager._mcp_enabled(config),
                'switch_revision': self.manager.capability_settings.revision('mcp', config['name'])}

    def rebuild(self, name, *, checkpoint=None):
        self._owner()
        with package_lock(self.store.root, admission_key(name, self.user) + ':management', exclusive=True, timeout=130):
            if self.pending(name):
                raise PermissionError('A pending MCP operation needs recovery first')
            config = self._config(name)
            supported, reason = rebuild_support(config)
            if not supported:
                raise ValueError(reason)
            operation = self.store.begin_operation(namespace(name), self.user, 'mcp-rebuild', self._facts(config))
            return self._complete(name, operation, checkpoint=checkpoint)

    def resume(self, name, operation_id, *, checkpoint=None):
        self._owner()
        with package_lock(self.store.root, admission_key(name, self.user) + ':management', exclusive=True, timeout=130):
            operation = self.store.operation(operation_id)
            if (operation['pluginId'], operation['userId'], operation['kind']) != (namespace(name), self.user, 'mcp-rebuild'):
                raise PermissionError('MCP rebuild operation does not belong to this user and provider')
            if operation['facts'].get('workspace') != str(self.manager.workspace_root):
                raise PermissionError('MCP rebuild belongs to another workspace')
            if operation['stage'] not in {'staged', 'prepared', 'draining', 'selected'}:
                raise ValueError('MCP rebuild operation cannot be resumed')
            return self._complete(name, operation, checkpoint=checkpoint)

    def _stop_connections(self, name, digest):
        from agent.tools.mcp.mcp_client import LIVE_CLIENTS
        from agent.tools.tool_manager import ToolManager
        for manager in ToolManager.instances():
            if (manager.identity.user_id or 'local-default') != self.user:
                continue
            with manager._mcp_lock:
                config = manager._mcp_active_configs.get(name)
                if config is not None and environment_identity(config) == digest:
                    manager._teardown_mcp_server(name)
                    manager._mcp_loaded = False
        for client in list(LIVE_CLIENTS):
            key = client._runtime_key
            if key and (key['user_id'], key['identifier']) == (self.user, name) and (
                    client.environment and client.environment.get('launch_digest') == digest):
                client.shutdown()

    def _complete(self, name, operation, *, checkpoint):
        environment = operation['facts'].get('environment')
        selection_attempted = False
        environments = EnvironmentStore()
        facts = operation['facts']
        expected = {key: value for key, value in facts.items() if key != 'environment'}

        def fixed():
            return self._facts(self._config(name)) == expected

        def advance(stage, values=None):
            nonlocal operation
            operation = self.store.advance(operation['operationId'], stage, values)
            if checkpoint:
                checkpoint(stage, operation)

        try:
            if any(row['operationId'] != operation['operationId'] for row in self.pending(name)):
                raise PermissionError('Another pending MCP operation needs recovery first')
            if not fixed():
                raise PermissionError('MCP source, configuration or enablement changed; rebuild was cancelled')
            if operation['stage'] == 'staged':
                from agent.tools.mcp.mcp_client import McpClient
                with tempfile.TemporaryDirectory(prefix='stella-mcp-validation-') as scratch:
                    client = McpClient(McpMaintenanceConfig(self._config(name), scratch))
                    try:
                        if not client.initialize():
                            raise RuntimeError('MCP candidate validation failed; the previous environment remains selected')
                        client.list_tools()
                    finally:
                        environment = client.environment
                        client.shutdown()
                if environment is None:
                    raise ValueError('MCP did not prepare an independent dependency environment')
                advance('prepared', {'environment': environment})
            if environment is None or (environment['kind'], environment['identifier'], environment['user'],
                    environment['package_digest'], environment.get('launch_digest')) != (
                        'mcp', name, self.user, facts['package_digest'], facts['environment_digest']):
                raise PermissionError('MCP candidate does not belong to this owner and source')
            if not fixed():
                raise PermissionError('MCP source, configuration or enablement changed during validation')
            advance('draining')
            self._stop_connections(name, facts['environment_digest'])
            with package_lock(self.store.root, admission_key(name, self.user, facts['environment_digest']), exclusive=True, timeout=15):
                if not fixed():
                    raise PermissionError('MCP source, configuration or enablement changed during maintenance')
                self._stop_connections(name, facts['environment_digest'])
                selection_attempted = True
                environments.activate(environment, validate=lambda candidate: fixed())
                advance('selected')
                advance('completed')
            return operation
        except Exception as error:
            if selection_attempted:
                # The environment pointer and operation journal use different
                # stores. An uncertain selection must stay blocked and resumable.
                raise RuntimeError('MCP environment selection requires recovery; resume the rebuild') from error
            if environment is not None:
                try:
                    environments.fail_candidate(environment)
                except (ValueError, OSError):
                    pass
            self.store.advance(operation['operationId'], 'failed', error=str(error))
            raise
