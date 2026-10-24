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

    def set(self, *, user_id, kind, identifier, digest, trusted):
        if not user_id or kind not in {'plugin', 'mcp'} or not identifier or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('runtime identity and SHA256 required')
        if not isinstance(trusted, bool):
            raise ValueError('trusted must be boolean')
        with self.transaction() as db:
            self._table(db)
            db.execute('''INSERT INTO runtime_trust VALUES (?,?,?,?,?,1) ON CONFLICT(user,kind,identifier,digest)
                DO UPDATE SET trusted=excluded.trusted,revision=runtime_trust.revision+1''',
                       (user_id, kind, identifier, digest, int(trusted)))
        if not trusted and kind == 'plugin':
            from core.plugins.host import revoke_runtime_trust
            revoke_runtime_trust(user_id, identifier, digest)
        if not trusted and kind == 'mcp':
            from agent.tools.mcp.mcp_client import LIVE_CLIENTS
            key = dict(user_id=user_id, kind=kind, identifier=identifier, digest=digest)
            for client in list(LIVE_CLIENTS):
                if client._runtime_key == key and client.sandbox_state.get('mode') == 'trusted':
                    client.shutdown()

    def choice(self, *, user_id, kind, identifier, digest):
        with self.transaction() as db:
            self._table(db)
            row = db.execute('SELECT trusted FROM runtime_trust WHERE user=? AND kind=? AND identifier=? AND digest=?',
                             (user_id, kind, identifier, digest)).fetchone()
            return bool(row[0]) if row is not None else None

    def allowed(self, **identity):
        return self.choice(**identity) is True


def verified_first_party(root, plugin_id, digest):
    """Only the Core-shipped catalog is a trust anchor; owner custom catalogs aren't."""
    from core.plugins.package_ops import _sha256_tree
    core = Path(__file__).resolve().parents[2]
    if getattr(sys, 'frozen', False):
        catalogs = [Path(sys.executable).resolve().parents[2] / 'plugin-catalog.json',
                    Path(getattr(sys, '_MEIPASS', core)) / 'resources/plugin-catalog.json']
    else:
        catalogs = [core / 'resources/plugin-catalog.json',
                    core / 'desktop/resources/plugin-catalog.json',
                    core.parent / 'Stella-Plugins/catalog/official.json']
    try:
        for path in catalogs:
            if not path.is_file():
                continue
            pins = json.loads(path.read_text())
            if any(row.get('pluginId') == plugin_id and row.get('sha256') == digest
                   for row in pins.get('plugins', [])):
                return _sha256_tree(Path(root)) == digest
    except (OSError, ValueError, KeyError):
        return False
    return False


def plugin_trusted(root, plugin_id, digest, user_id):
    choice = RuntimeTrustStore().choice(user_id=user_id, kind='plugin', identifier=plugin_id, digest=digest)
    return choice if choice is not None else verified_first_party(root, plugin_id, digest)
