"""Cooperative manager claims; existing table names remain a storage contract.

Calendar scans/moves/rebind and both domains' link changes/queued deletions share
one manager SQLite protocol. Unknown effects stay held except exact receipt-only
local finalization. Unconverted reminder scans and writes do not participate.
"""
import hashlib
import json
import secrets

TABLES = {
    'calendar_scan_attempts': 'CREATE TABLE calendar_scan_attempts (attempt_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, group_id TEXT NOT NULL, state TEXT NOT NULL, window TEXT NOT NULL, started_at TEXT NOT NULL, evidence TEXT NOT NULL)',
    'calendar_scan_groups': 'CREATE TABLE calendar_scan_groups (group_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, attempt_id TEXT NOT NULL UNIQUE)',
    'calendar_scan_items': 'CREATE TABLE calendar_scan_items (local_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL)',
}
KEY = '_calendar_scan_claim_schema'
VERSION = 'calendar-scan-v1'


class ScanHeld(ValueError):
    pass


class ScanUnavailable(ValueError):
    pass


def schema(db, *, create=False):
    marker = db.execute('SELECT value FROM settings WHERE key=?', (KEY,)).fetchone()
    found = {name: db.execute('SELECT type,sql FROM sqlite_master WHERE name=?', (name,)).fetchone() for name in TABLES}
    if not marker:
        if any(found.values()):
            raise ValueError('unrecognized calendar scan tables; supervised migration required')
        if not create:
            return False
        for sql in TABLES.values():
            db.execute(sql)
        db.execute('INSERT INTO settings VALUES (?,?)', (KEY, json.dumps(VERSION)))
        return True
    if json.loads(marker[0]) != VERSION:
        raise ValueError('unsupported calendar scan claim version')
    for name, sql in TABLES.items():
        row = found[name]
        if not row or row[0] != 'table' or ' '.join(row[1].split()) != sql:
            raise ValueError('incompatible calendar scan claim schema')
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (name,)).fetchone():
            raise ValueError('unexpected calendar scan claim trigger')
    return True


def identity(source_id, group_id):
    if any(not isinstance(value, str) or not value.strip() or '\0' in value for value in (source_id, group_id)):
        raise ValueError('calendar source and container identity must be explicit')


def destination(inventory, group_id):
    matches = [row for row in inventory.get('calendars', []) if row.get('id') == group_id]
    if not matches:
        raise ScanUnavailable('calendar container unavailable; no missing-object inference permitted')
    if len(matches) != 1:
        raise ValueError('calendar container identity ambiguous')
    row = matches[0]
    identity(row.get('source_id'), group_id)
    if row['source_id'] not in inventory.get('source_ids', []):
        raise ValueError('calendar source is not in the trusted live inventory')
    return row


def guard_scan_reservations(db, approved):
    # Called inside reserve's existing BEGIN IMMEDIATE, never a preflight check.
    for item in approved['plan']['items']:
        identity(item.get('source_id'), item.get('group_id'))
        target = item.get('destination_snapshot') or {}
        if target.get('source_id') != item['source_id'] or target.get('id') != item['group_id']:
            raise ValueError('approved container and source identity disagree')
    if not schema(db):
        return
    for item in approved['plan']['items']:
        if db.execute('SELECT 1 FROM calendar_scan_groups WHERE group_id=?', (item['group_id'],)).fetchone():
            raise ScanHeld('calendar scan is unfinished; approved export must wait')
        if db.execute('SELECT 1 FROM calendar_scan_items WHERE local_id=?', (item['local_id'],)).fetchone():
            raise ScanHeld('calendar scan holds this local item; approved export must wait')


def acquire(sync, source_id, group_id, window):
    identity(source_id, group_id)
    token = secrets.token_hex(32)
    from apple_sync.export_reservations import schema as export_schema
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        schema(db, create=True)
        exported = export_schema(db)
        if exported and db.execute('SELECT 1 FROM trusted_export_reservations WHERE group_id=?', (group_id,)).fetchone():
            raise ScanHeld('approved export holds this calendar container')
        if exported and db.execute('SELECT 1 FROM ordinary_export_claims WHERE group_id=?', (group_id,)).fetchone():
            raise ScanHeld('ordinary Apple write is unfinished in this calendar container')
        if db.execute('SELECT 1 FROM calendar_scan_groups WHERE group_id=?', (group_id,)).fetchone():
            raise ScanHeld('calendar scan is unfinished; reconcile before rescanning')
        db.execute('INSERT INTO calendar_scan_attempts VALUES (?,?,?,?,?,?,?)', (token, source_id, group_id, 'scanning', json.dumps(window), sync.clock(), '{}'))
        db.execute('INSERT INTO calendar_scan_groups VALUES (?,?,?)', (group_id, source_id, token))
    return token


def claim_items(sync, token, local_ids):
    from apple_sync.export_reservations import schema as export_schema
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        schema(db)
        if not db.execute('SELECT 1 FROM calendar_scan_groups WHERE attempt_id=?', (token,)).fetchone():
            raise ValueError('scan claim lost')
        exported = export_schema(db)
        for local_id in sorted(set(local_ids)):
            identity(local_id, token)
            if exported and db.execute('SELECT 1 FROM trusted_export_reservations WHERE local_id=?', (local_id,)).fetchone():
                raise ScanHeld('approved export holds a scanned local identity')
            if exported and db.execute('SELECT 1 FROM ordinary_export_claims WHERE local_id=?', (local_id,)).fetchone():
                raise ScanHeld('ordinary Apple write holds a scanned local identity')
            prior = db.execute('SELECT attempt_id FROM calendar_scan_items WHERE local_id=?', (local_id,)).fetchone()
            if prior and prior[0] != token:
                raise ScanHeld('another scan holds this local identity')
            db.execute('INSERT OR IGNORE INTO calendar_scan_items VALUES (?,?)', (local_id, token))


def cancel_unstarted_scan(sync, token, source_id, group_id, window, reason):
    """Finish only the exact initial scan claim before item/effect work began."""
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE');schema(db)
        attempt=db.execute('SELECT * FROM calendar_scan_attempts WHERE attempt_id=?',(token,)).fetchone()
        groups=[dict(row) for row in db.execute('SELECT * FROM calendar_scan_groups WHERE attempt_id=?',(token,))]
        if (not attempt or attempt['source_id']!=source_id or attempt['group_id']!=group_id or
                attempt['state']!='scanning' or json.loads(attempt['window'])!=window or
                json.loads(attempt['evidence'])!={} or
                groups!=[{'group_id':group_id,'source_id':source_id,'attempt_id':token}] or
                db.execute('SELECT 1 FROM calendar_scan_items WHERE attempt_id=?',(token,)).fetchone()):
            return False
        db.execute("UPDATE calendar_scan_attempts SET state='not_sent',evidence=? WHERE attempt_id=?",
                   (json.dumps({'reason':str(reason),'no_item_phase_started':True}),token))
        db.execute('DELETE FROM calendar_scan_groups WHERE attempt_id=?',(token,))
    return True


def uncertain(sync, token):
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        schema(db)
        db.execute("UPDATE calendar_scan_attempts SET state='uncertain' WHERE attempt_id=? AND state='scanning'", (token,))


def complete(sync, token):
    # No RPC under the SQLite write transaction. Evidence is for manual review,
    # not permission to replay side effects after interruption.
    with sync._connect() as db:
        schema(db)
        held = db.execute('SELECT * FROM calendar_scan_groups WHERE attempt_id=?', (token,)).fetchone()
    if not held:
        raise ValueError('scan completion lost its claim')
    maps = [row for row in sync._maps() if row['calendar_id'] == held['group_id']]
    owner = [sync._local(row['alcor_id']) for row in maps]
    evidence = {'completed_at': sync.clock(), 'mapping_count': len(maps),
                'mapping_hash': digest(maps), 'owner_hash': digest(owner)}
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        schema(db)
        row = db.execute('SELECT state FROM calendar_scan_attempts WHERE attempt_id=?', (token,)).fetchone()
        if not row or row[0] != 'scanning':
            raise ValueError('uncertain scans cannot be released automatically')
        current = [dict(row) for row in db.execute('SELECT * FROM mappings WHERE calendar_id=? ORDER BY id', (held['group_id'],))]
        if digest(current) != evidence['mapping_hash']:
            raise ValueError('scan mappings changed before safe completion')
        db.execute("UPDATE calendar_scan_attempts SET state='done', evidence=? WHERE attempt_id=?", (json.dumps(evidence, sort_keys=True), token))
        db.execute('DELETE FROM calendar_scan_items WHERE attempt_id=?', (token,))
        db.execute('DELETE FROM calendar_scan_groups WHERE attempt_id=?', (token,))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def status(sync):
    with sync._connect() as db:
        if not schema(db):
            return []
        rows=[]
        for row in db.execute("SELECT a.* FROM calendar_scan_attempts a JOIN calendar_scan_groups g USING(attempt_id) ORDER BY a.started_at"):
            evidence=json.loads(row['evidence'])
            rows.append({key:row[key] for key in ('attempt_id','source_id','group_id','state','window','started_at')})
            rows[-1].update(stage=evidence.get('stage'),reason=evidence.get('reason',''))
        return rows


def verify_binding(sync, local_id, source_id, group_id, remote=None):
    local = sync._local(local_id)
    binding = (local or {}).get('apple') or {}
    if not binding:
        return
    if binding.get('group_id') != group_id:
        raise ValueError('existing local binding belongs to another calendar container')
    if binding.get('source_id') and binding['source_id'] != source_id:
        raise ValueError('existing local binding belongs to another source')
    if not binding.get('source_id'):
        # Resolve only this old binding against the claimed, live native record.
        if not remote or binding.get('apple_id') != remote.get('event_id'):
            raise ValueError('legacy binding source cannot be resolved safely')


class WorkClaim:
    """An existing manager claim shared by link changes and queued operations."""
    def __init__(self, sync, groups, local_ids, evidence, *, recovery=False, category_ids=None, category_kind=None, completed_delete_receipt=False, completed_owner_receipt=False):
        self.sync = sync
        self.groups = dict(sorted(groups.items()))
        self.category_ids=sorted(set(category_ids or []));self.category_kind=category_kind
        if self.category_ids and category_kind not in ('calendar','reminders'):
            raise ValueError('category claim domain required')
        self.items = sorted(set(local_ids)|{'category:'+identity for identity in self.category_ids})
        self.evidence = evidence
        self.recovery = recovery
        self.completed_delete_receipt = completed_delete_receipt
        self.completed_owner_receipt = completed_owner_receipt
        self.started_effects = recovery
        self.tokens = []
        self.document = None

    def __enter__(self):
        from apple_sync.export_reservations import schema as export_schema
        if not self.groups:
            raise ValueError('operation has no verified Apple container')
        for group,source in self.groups.items():
            identity(source,group)
        with self.sync._connect() as db:
            db.execute('BEGIN IMMEDIATE');schema(db,create=True)
            if self.category_ids:
                from apple_sync.category_create import guard_category_ids_in_db
                guard_category_ids_in_db(db,self.category_kind,self.category_ids)
            exported = export_schema(db)
            prior = []
            for group,source in self.groups.items():
                if exported and any(db.execute(f'SELECT 1 FROM {table} WHERE group_id=?', (group,)).fetchone()
                                    for table in ('trusted_export_reservations','ordinary_export_claims')):
                    raise ScanHeld('Apple container has an unfinished native write')
                row = db.execute('SELECT a.*,g.source_id AS held_source FROM calendar_scan_attempts a JOIN calendar_scan_groups g USING(attempt_id) WHERE g.group_id=?', (group,)).fetchone()
                if row:
                    prior.append(dict(row))
            for item in self.items:
                if exported and any(db.execute(f'SELECT 1 FROM {table} WHERE local_id=?', (item,)).fetchone()
                                    for table in ('trusted_export_reservations','ordinary_export_claims')):
                    raise ScanHeld('Apple item has an unfinished native write')
            if prior:
                if not self.recovery or len(prior) != len(self.groups):
                    raise ScanHeld('Apple container is held by another unfinished operation')
                document = json.loads(prior[0]['evidence'])
                old_tokens = {row['attempt_id'] for row in prior}
                old_items = {row[0] for token in old_tokens for row in db.execute('SELECT local_id FROM calendar_scan_items WHERE attempt_id=?', (token,))}
                if (document.get('kind') != 'manager_work' or document.get('evidence') != self.evidence
                        or document.get('groups') != self.groups or old_items != set(self.items)
                        or any((row['state'] != 'uncertain' and not (row['state']=='scanning' and
                            (self.completed_delete_receipt and document.get('stage') in ('sending','receipt_recovery') or
                             self.completed_owner_receipt and (
                                 self.evidence.get('kind') in ('manual_item_restore','approved_import_unlink') and document.get('stage') in ('owner_claiming','owner_committed','receipt_recovery') or
                                 self.evidence.get('kind')=='bound_item_conflict' and document.get('stage') in ('owner_claiming','owner_committing','owner_committed','receipt_recovery'))))) or row['held_source'] != self.groups[row['group_id']]
                               or row['source_id'] != row['held_source'] or json.loads(row['evidence']) != document for row in prior)):
                    raise ScanHeld('unfinished operation evidence differs; reconciliation required')
                # Only an exact durable receipt permits local finalization, never a native resend.
                for token in old_tokens:
                    db.execute("UPDATE calendar_scan_attempts SET state='receipt_recovery' WHERE attempt_id=?", (token,))
                    db.execute('DELETE FROM calendar_scan_items WHERE attempt_id=?', (token,))
                    db.execute('DELETE FROM calendar_scan_groups WHERE attempt_id=?', (token,))
                self.started_effects = True
            for item in self.items:
                if db.execute('SELECT 1 FROM calendar_scan_items WHERE local_id=?', (item,)).fetchone():
                    raise ScanHeld('Apple item is held by another unfinished operation')
            self.tokens = [secrets.token_hex(32) for _ in self.groups]
            self.document = {'kind':'manager_work','groups':self.groups,'items':self.items,
                             'evidence':self.evidence,'stage':'receipt_recovery' if self.started_effects else 'claimed'}
            encoded = json.dumps(self.document,ensure_ascii=False,sort_keys=True)
            for (group,source),token in zip(self.groups.items(),self.tokens):
                db.execute('INSERT INTO calendar_scan_attempts VALUES (?,?,?,?,?,?,?)', (token,source,group,'scanning',json.dumps({'kind':'manager_work'}),self.sync.clock(),encoded))
                db.execute('INSERT INTO calendar_scan_groups VALUES (?,?,?)', (group,source,token))
            for item in self.items:
                db.execute('INSERT INTO calendar_scan_items VALUES (?,?)', (item,self.tokens[0]))
        return self

    def _check(self, db):
        schema(db)
        for (group,source),token in zip(self.groups.items(),self.tokens):
            row = db.execute('SELECT a.state,a.evidence FROM calendar_scan_attempts a JOIN calendar_scan_groups g USING(attempt_id) WHERE g.group_id=? AND g.source_id=? AND g.attempt_id=?', (group,source,token)).fetchone()
            if not row or row['state'] != 'scanning' or json.loads(row['evidence']) != self.document:
                raise ValueError('manager operation claim or evidence changed')
        items = {row[0] for token in self.tokens for row in db.execute('SELECT local_id FROM calendar_scan_items WHERE attempt_id=?', (token,))}
        if items != set(self.items):
            raise ValueError('manager operation item claims changed')

    def started(self, stage='sending'):
        if self.recovery:
            raise ValueError('receipt recovery may finalize locally but cannot dispatch native writes')
        if self.started_effects and self.document['stage'] == stage:
            return
        with self.sync._connect() as db:
            db.execute('BEGIN IMMEDIATE');self._check(db)
            updated = {**self.document,'stage':stage}
            for token in self.tokens:
                db.execute('UPDATE calendar_scan_attempts SET evidence=? WHERE attempt_id=?', (json.dumps(updated,ensure_ascii=False,sort_keys=True),token))
        self.document = updated
        self.started_effects = True

    def owner_rejected(self):
        # apple_begin_operation's typed rejection returns after its transaction rolled back.
        with self.sync._connect() as db:
            db.execute('BEGIN IMMEDIATE');self._check(db)
            if self.document['stage'] != 'owner_claiming':
                raise ValueError('a dispatched operation cannot become unstarted')
            updated = {**self.document,'stage':'claimed'}
            for token in self.tokens:
                db.execute('UPDATE calendar_scan_attempts SET evidence=? WHERE attempt_id=?', (json.dumps(updated,ensure_ascii=False,sort_keys=True),token))
        self.document = updated
        self.started_effects = False

    def native_rejected(self):
        # Caller must have the completion helper's explicit pre-save no-effect response.
        with self.sync._connect() as db:
            db.execute('BEGIN IMMEDIATE');self._check(db)
            if self.recovery or self.document['stage']!='sending':
                raise ValueError('only an unchanged uncommitted native attempt can be released')
            updated={**self.document,'stage':'claimed'}
            for token in self.tokens:
                db.execute('UPDATE calendar_scan_attempts SET evidence=? WHERE attempt_id=?',(json.dumps(updated,ensure_ascii=False,sort_keys=True),token))
        self.document=updated;self.started_effects=False

    def __exit__(self, error_type, error, traceback):
        # No timeout release. A failed or altered journal keeps all available holds.
        try:
            with self.sync._connect() as db:
                db.execute('BEGIN IMMEDIATE');self._check(db)
                state = 'uncertain' if error_type and self.started_effects else 'not_sent' if error_type else 'done'
                result = {**self.document,'result':state,'reason':str(error) if error else ''}
                for token in self.tokens:
                    db.execute('UPDATE calendar_scan_attempts SET state=?,evidence=? WHERE attempt_id=?', (state,json.dumps(result,ensure_ascii=False,sort_keys=True),token))
                    if state != 'uncertain':
                        db.execute('DELETE FROM calendar_scan_items WHERE attempt_id=?', (token,))
                        db.execute('DELETE FROM calendar_scan_groups WHERE attempt_id=?', (token,))
        except BaseException:
            for token in self.tokens:
                uncertain(self.sync, token)
            raise
        return False
