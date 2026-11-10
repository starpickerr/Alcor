"""Explicit native-owner trust for a particular runtime digest, never a name."""
import json
from pathlib import Path
import re
import sys

from core.security.policy_store import RunPolicyStore


class RuntimeTrustStore(RunPolicyStore):
    @staticmethod
    def _table(db):
        db.execute('''CREATE TABLE IF NOT EXISTS runtime_trust (
            user TEXT NOT NULL, kind TEXT NOT NULL, identifier TEXT NOT NULL, digest TEXT NOT NULL,
            trusted INTEGER NOT NULL, revision INTEGER NOT NULL,
            PRIMARY KEY(user,kind,identifier,digest))''')
        columns = {row[1] for row in db.execute('PRAGMA table_info(runtime_trust)')}
        for name in ('network', 'credentials'):
            if name not in columns:
                db.execute(f'ALTER TABLE runtime_trust ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')

    def set(self, *, user_id, kind, identifier, digest, trusted):
        if not user_id or kind not in {'plugin', 'mcp'} or not identifier or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('runtime identity and SHA256 required')
        if not isinstance(trusted, bool):
            raise ValueError('trusted must be boolean')
        with self.transaction() as db:
            self._table(db)
            db.execute('''INSERT INTO runtime_trust (user,kind,identifier,digest,trusted,revision) VALUES (?,?,?,?,?,1) ON CONFLICT(user,kind,identifier,digest)
                DO UPDATE SET trusted=excluded.trusted,revision=runtime_trust.revision+1''',
                       (user_id, kind, identifier, digest, int(trusted)))
        if not trusted and kind == 'plugin':
            from core.plugins.host import revoke_runtime_trust
            revoke_runtime_trust(user_id, identifier, digest)
        if not trusted and kind == 'mcp':
            from agent.tools.mcp.mcp_client import LIVE_CLIENTS
            key = dict(user_id=user_id, kind=kind, identifier=identifier, digest=digest)
            for client in list(LIVE_CLIENTS):
                if client._runtime_key == key:
                    client.shutdown()

    def choice(self, *, user_id, kind, identifier, digest):
        with self.transaction() as db:
            self._table(db)
            row = db.execute('SELECT trusted FROM runtime_trust WHERE user=? AND kind=? AND identifier=? AND digest=?',
                             (user_id, kind, identifier, digest)).fetchone()
            return bool(row[0]) if row is not None else None

    def capabilities(self, *, user_id, kind, identifier, digest):
        with self.transaction() as db:
            self._table(db)
            row = db.execute('SELECT trusted,network,credentials,revision FROM runtime_trust WHERE user=? AND kind=? AND identifier=? AND digest=?',
                             (user_id, kind, identifier, digest)).fetchone()
            return dict(network=bool(row and row[0] and row[1]), credentials=bool(row and row[0] and row[2]), revision=row[3] if row else 0)

    def set_capabilities(self, *, user_id, identifier, digest, network, credentials, expected_revision):
        from core.security.control import require_owner
        from common.runtime_identity import current_identity
        require_owner()
        if (user_id != (current_identity().user_id or 'local-default') or not identifier
                or not re.fullmatch('[0-9a-f]{64}', digest) or type(network) is not bool or type(credentials) is not bool):
            raise ValueError('Exact local MCP runtime and explicit capabilities required')
        with self.transaction() as db:
            self._table(db)
            row = db.execute('SELECT trusted,revision FROM runtime_trust WHERE user=? AND kind=? AND identifier=? AND digest=?',
                             (user_id, 'mcp', identifier, digest)).fetchone()
            if type(expected_revision) is not int or expected_revision != (row[1] if row else 0):
                raise ValueError('MCP runtime permissions changed; refresh before saving')
            if row and not row[0] and (network or credentials):
                raise PermissionError('Runtime identity was revoked; select a new verified implementation')
            db.execute('''INSERT INTO runtime_trust (user,kind,identifier,digest,trusted,revision,network,credentials)
                VALUES (?, 'mcp', ?, ?, 1, 1, ?, ?) ON CONFLICT(user,kind,identifier,digest)
                DO UPDATE SET network=excluded.network,credentials=excluded.credentials,revision=runtime_trust.revision+1''',
                (user_id, identifier, digest, int(network), int(credentials)))
        from agent.tools.mcp.mcp_client import LIVE_CLIENTS
        key = dict(user_id=user_id, kind='mcp', identifier=identifier, digest=digest)
        for client in list(LIVE_CLIENTS):
            if client._runtime_key == key:
                client.shutdown()
        return self.capabilities(**key)

    def allowed(self, **identity):
        return self.choice(**identity) is True


def verified_first_party(root, plugin_id, digest):
    """Only the Core-shipped catalog is a trust anchor; owner custom catalogs aren't."""
    from core.plugins.package_ops import _sha256_tree
    core = Path(__file__).resolve().parents[2]
    if getattr(sys, 'frozen', False):
        catalogs = [Path(sys.executable).resolve().parents[2] / 'plugin-catalog.json',
                    Path(getattr(sys, '_MEIPASS', core)) / 'resources/plugin-catalog.json',
                    Path(getattr(sys, '_MEIPASS', core)) / 'resources/default-plugins.json']
    else:
        catalogs = [core / 'resources/plugin-catalog.json',
                    core / 'desktop/resources/plugin-catalog.json',
                    core / 'resources/default-plugins.json']
    try:
        for path in catalogs:
            if not path.is_file():
                continue
            pins = json.loads(path.read_text())
            if any(row.get('pluginId') == plugin_id and (row.get('sourceSha256') or row.get('sha256')) == digest
                   for row in pins.get('plugins', [])):
                return _sha256_tree(Path(root)) == digest
    except (OSError, ValueError, KeyError):
        return False
    return False


def plugin_trusted(root, plugin_id, digest, user_id):
    choice = RuntimeTrustStore().choice(user_id=user_id, kind='plugin', identifier=plugin_id, digest=digest)
    return choice is not False and verified_first_party(root, plugin_id, digest)
