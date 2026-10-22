"""Materialize legacy connection authority in Security's existing control database."""
from hashlib import sha256
import json
from pathlib import Path

from core.security.resource_grants import ResourceGrantStore


def inputs(db, user):
    from config import conf
    from core.plugins.management import default_store
    from core.plugins.package_ops import PackageStore
    from core.security.runtime_trust import verified_first_party
    from agent.permission.security_policy import validate_patch
    policy = conf().get('security_policy')
    original_connections = policy.get('connections', {}) if isinstance(policy, dict) else {}
    try:
        connections = validate_patch({'connections': original_connections})['connections']
    except ValueError:
        # A damaged legacy scope must not lock its owner out of the repair UI,
        # nor turn into an unrestricted connection during migration.
        from agent.tools.tool_manager import ToolManager
        names = {'email', 'elearning', 'mcp:email', 'mcp:elearning'}
        names.update('mcp:' + row['name'] for row in ToolManager()._load_mcp_configs())
        if isinstance(original_connections, dict):
            names.update(key for key in original_connections if isinstance(key, str) and key)
        connections = {name: {'allowed': False, 'scopes': []} for name in sorted(names)}
    parents = []
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='grants' AND type='table'").fetchone():
        parents = [row[0] for row in db.execute("SELECT DISTINCT json_extract(value,'$.plugin_id') FROM grants WHERE json_extract(value,'$.user_id')=?", (user,))]
    store = default_store()
    package = PackageStore(store.root).current_package('stella.briefing')
    native_briefing = bool(package and store.has_instance('stella.briefing', user) and
        store.instance('stella.briefing', user)['intent'] == 'enabled' and verified_first_party(
            Path(package['path']), 'stella.briefing', package['digest']))
    return {'connections': connections, 'original_connections': original_connections,
            'existing_plugin_grants': sorted(parents), 'native_briefing': native_briefing}


def apply(db, user, entries, facts):
    ResourceGrantStore._restrictions(db)
    ResourceGrantStore._rows(db)
    global_connections = facts['connections']
    def restriction(subject, connection, value):
        body = json.dumps(value, sort_keys=True)
        db.execute('INSERT OR IGNORE INTO resource_restrictions VALUES (?,?,?,1,?,?)', (user, subject, connection, body, body))
    for connection, value in global_connections.items():
        restriction('user:' + user, connection, value)
    for entry in entries:
        if entry['kind'] == 'template':
            continue
        key = tuple(entry['key'])
        from core.security.policy_store import RunPolicyStore
        policy = RunPolicyStore._read(db, key) or entry['policy']
        subject = f"{entry['kind']}:{entry['agent']}:{entry['subject']}"
        for connection, value in (policy.get('connections') or {}).items():
            if value != global_connections.get(connection):
                restriction(subject, connection, value)
        _containers(db, user, subject, policy['capabilities'], facts)
    if facts['native_briefing']:
        # Native Briefing previously reused owner reads. Preserve exactly this
        # Core-registered consumer; an installed name alone never qualifies.
        _containers(db, user, 'plugin:stella.briefing',
            {f'mcp:{domain}': {'read': 'allow'} for domain in ('email', 'elearning')}, facts)


def _containers(db, user, subject, capabilities, facts):
    for domain in ('email', 'elearning'):
        if 'stella.' + domain in facts['existing_plugin_grants']:
            continue  # Existing scoped grants (including revocation) remain the authority.
        operations = set()
        for name, grants in capabilities.items():
            if name == 'mcp:' + domain or name.startswith('mcp:' + domain + '/'):
                operations.update(op for op, setting in grants.items() if setting in {'allow', 'confirm'}
                                  and op in {'read', 'write', 'delete', 'external_send', 'system_execute'})
        if not operations:
            continue
        identifier = 'legacy-resource-' + sha256(f'{user}\0{subject}\0{domain}'.encode()).hexdigest()
        if db.execute('SELECT 1 FROM resource_grants WHERE id=?', (identifier,)).fetchone():
            continue
        row = dict(grant_id=identifier, user_id=user, subject=subject,
            resource={'kind': 'connection', 'id': 'mcp:' + domain}, operations=sorted(operations),
            purposes=['interactive', 'background'], valid_until=None, revoked=False, revision=1,
            parent_grant_id=None, legacy_scope='all_owned_resources')
        ResourceGrantStore._write(db, row)
