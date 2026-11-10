"""Persistent per-item hold for the unregistered trusted export prototype.

All creation paths already hold their manager instance's lock. One manager owns
one Profile. This is not a multiprocess lock or an EventKit transaction.
"""
import hashlib
import json


TABLES = {
    'trusted_export_reservations': 'CREATE TABLE trusted_export_reservations (local_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, approval_fingerprint TEXT NOT NULL, group_id TEXT NOT NULL, source_id TEXT NOT NULL)',
    'trusted_export_mappings': 'CREATE TABLE trusted_export_mappings (item_key TEXT PRIMARY KEY, operation_id TEXT NOT NULL, document TEXT NOT NULL)',
    'ordinary_export_claims': 'CREATE TABLE ordinary_export_claims (local_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, attempt_id TEXT NOT NULL UNIQUE, expected TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN (\'sending\',\'uncertain\')))',
}
VERSION_KEY = '_trusted_export_prototype_schema'
VERSION = 'calendar-only-ordinary-proof-v2-cas'


def schema(db, *, create=False):
    marker = db.execute('SELECT value FROM settings WHERE key=?',(VERSION_KEY,)).fetchone()
    found = {name:db.execute('SELECT type,sql FROM sqlite_master WHERE name=?',(name,)).fetchone() for name in TABLES}
    if not marker:
        if any(found.values()):
            raise ValueError('unrecognized trusted export tables; supervised migration required')
        if not create:
            return False
        for sql in TABLES.values():
            db.execute(sql)
        db.execute('INSERT INTO settings VALUES (?,?)',(VERSION_KEY,json.dumps(VERSION)))
        return True
    if json.loads(marker[0]) != VERSION:
        raise ValueError('unsupported trusted export schema version')
    for name,expected in TABLES.items():
        row = found[name]
        if not row or row[0] != 'table' or ' '.join(row[1].split()) != expected:
            raise ValueError('incompatible trusted export table schema')
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=?",(name,)).fetchone():
            raise ValueError('unexpected trusted export table trigger')
    return True


def guard_write(sync, local_id):
    with sync._connect() as db:
        if not schema(db):
            return
        if db.execute('SELECT 1 FROM trusted_export_reservations WHERE local_id=?',(local_id,)).fetchone():
            raise ValueError('approved Apple export is unfinished; reconcile before ordinary native writes')


def reserve(sync, approved):
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        schema(db, create=True)
        from apple_sync.group_scan_claims import guard_scan_reservations
        guard_scan_reservations(db, approved)
        for item in approved['plan']['items']:
            if db.execute('SELECT 1 FROM ordinary_export_claims WHERE local_id=?',(item['local_id'],)).fetchone():
                raise ValueError('ordinary Apple write is in flight; review before approved export')
            value = (item['local_id'],approved['operation_id'],approved['approval_fingerprint'],item['group_id'],item['source_id'])
            row = db.execute('SELECT * FROM trusted_export_reservations WHERE local_id=?',(item['local_id'],)).fetchone()
            if row and tuple(row) != value:
                raise ValueError('local item is already held by a different approved export')
            db.execute('INSERT OR IGNORE INTO trusted_export_reservations VALUES (?,?,?,?,?)',value)


def release(sync, approved, item_key, local_id):
    # Only the trusted sender calls this after rechecking owner and manager halves.
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        schema(db)
        receipt = db.execute('SELECT document FROM trusted_export_mappings WHERE item_key=?',(item_key,)).fetchone()
        if not receipt or json.loads(receipt[0])['approval_fingerprint'] != approved['approval_fingerprint']:
            raise ValueError('manager finalization receipt unavailable; hold remains')
        db.execute('DELETE FROM trusted_export_reservations WHERE local_id=? AND operation_id=? AND approval_fingerprint=?',
                   (local_id,approved['operation_id'],approved['approval_fingerprint']))


def held_groups(sync):
    with sync._connect() as db:
        held = {row[0] for row in db.execute('SELECT DISTINCT group_id FROM trusted_export_reservations')} if schema(db) else set()
        from apple_sync.group_scan_claims import schema as work_schema
        if work_schema(db):
            for row in db.execute('SELECT g.group_id,a.evidence FROM calendar_scan_groups g JOIN calendar_scan_attempts a USING(attempt_id)'):
                if json.loads(row['evidence']).get('evidence', {}).get('kind') == 'approved_export':
                    held.add(row['group_id'])
        return held


def guard_group(sync, group_id):
    if group_id in held_groups(sync):
        raise ValueError('Apple container has an unfinished approved export; imports require reconciliation')


def guard_link_changes(sync, links):
    held = held_groups(sync)
    if not held:
        return held
    old = {row['apple_id']:row for row in sync._stored_links()}
    new = {row.get('apple_id'):row for row in links if isinstance(row,dict)}
    for key in held:
        before, after = old.get(key), new.get(key)
        if before is None and after is None:
            continue
        if not before or not after or any(before[field] != after.get(field) for field in ('alcor_id','direction','enabled')):
            raise ValueError('held Apple container links cannot change before reconciliation')
    return held


def ordinary_begin(sync, local_id, group_id, attempt_id, expected, *, scan_token=None, scan_source=None):
    """Prototype only: claim immediately before native create, on manager DB."""
    if not all(isinstance(x,str) and x for x in (local_id,group_id,attempt_id)):
        raise ValueError('invalid ordinary claim')
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        schema(db,create=True)
        if db.execute('SELECT 1 FROM trusted_export_reservations WHERE local_id=?',(local_id,)).fetchone():
            raise ValueError('approved Apple export is unfinished; ordinary write stopped')
        from apple_sync.group_scan_claims import schema as scan_schema
        available = scan_schema(db)
        group = db.execute('SELECT attempt_id,source_id FROM calendar_scan_groups WHERE group_id=?',(group_id,)).fetchone() if available else None
        item = db.execute('SELECT attempt_id FROM calendar_scan_items WHERE local_id=?',(local_id,)).fetchone() if available else None
        if (group and group[0] != scan_token) or (item and item[0] != scan_token):
            raise ValueError('another calendar scan holds this target')
        if scan_token and (not group or not item or group[0] != scan_token or item[0] != scan_token or group[1] != scan_source):
            raise ValueError('current calendar scan claim is missing')
        if db.execute('SELECT 1 FROM ordinary_export_claims WHERE local_id=?',(local_id,)).fetchone():
            raise ValueError('ordinary Apple write is unresolved')
        fingerprint = hashlib.sha256(json.dumps(expected,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        proof = {'generation':expected.get('generation'),'snapshot_hash':fingerprint}
        db.execute("INSERT INTO ordinary_export_claims VALUES (?,?,?,?,'sending')",(local_id,group_id,attempt_id,json.dumps(proof,sort_keys=True)))


def ordinary_finish_calendar(sync, local_id, group_id, attempt_id, native):
    """Release only after matching mapping has committed; errors leave a hold."""
    if not isinstance(native,dict) or not isinstance(native.get('event_id'),str) or not native['event_id'] or native.get('calendar_id') != group_id:
        raise ValueError('ordinary native result needs reconciliation')
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE');schema(db)
        claim = db.execute('SELECT * FROM ordinary_export_claims WHERE local_id=?',(local_id,)).fetchone()
        mapping = db.execute('SELECT calendar_id,event_id FROM mappings WHERE alcor_id=?',(local_id,)).fetchall()
        if not claim or (claim['local_id'],claim['group_id'],claim['attempt_id'],claim['state']) != (local_id,group_id,attempt_id,'sending') or len(mapping)!=1 or tuple(mapping[0]) != (group_id,native['event_id']):
            raise ValueError('ordinary mapping or claim changed; reconciliation required')
        db.execute('DELETE FROM ordinary_export_claims WHERE local_id=? AND attempt_id=?',(local_id,attempt_id))
