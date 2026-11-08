"""Conditional ordinary calendar moves using existing group claims and journals."""
import json
import secrets

from apple_sync import group_scan_claims as scans
from apple_sync.export_reservations import schema as export_schema
from apple_sync.metadata import changed, metadata


def _state(db, key):
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _check_manager(db, doc):
    mapping = db.execute('SELECT * FROM mappings WHERE id=?', (doc['mapping']['id'],)).fetchone()
    if not mapping or dict(mapping) != doc['mapping']:
        raise ValueError('calendar move mapping changed; review required')
    for link in doc['links']:
        row = db.execute('SELECT * FROM category_links WHERE apple_id=?', (link['apple_id'],)).fetchone()
        if not row or dict(row) != link:
            raise ValueError('calendar move category link changed; review required')
    identity = doc['mapping']['alcor_id']
    if (_state(db, 'item-metadata:'+identity) != doc['baseline'] or
            _state(db, 'calendar-binding-generation:'+identity) != doc['guard']):
        raise ValueError('calendar move baseline changed; review required')


def _begin(sync, mapping, links, destinations, expected):
    identity = mapping['alcor_id']
    operation = secrets.token_hex(32)
    tokens = [secrets.token_hex(32), secrets.token_hex(32)]
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        scans.schema(db, create=True)
        exported = export_schema(db)
        doc = {'version': 1, 'kind': 'calendar_move', 'operation_id': operation,
               'mapping': mapping, 'links': links, 'destinations': destinations,
               'owner_expected': expected, 'tokens': tokens, 'stage': 'claimed',
               'baseline': _state(db, 'item-metadata:'+identity),
               'guard': _state(db, 'calendar-binding-generation:'+identity)}
        wanted = {'generation': expected['generation'], 'source_id': destinations[0]['source_id'],
                  'group_id': destinations[0]['id']}
        if doc['guard'] != wanted:
            raise ValueError('calendar move incarnation cannot be verified; review required')
        _check_manager(db, doc)
        for target in destinations:
            group = target['id']
            if db.execute('SELECT 1 FROM calendar_scan_groups WHERE group_id=?', (group,)).fetchone():
                raise scans.ScanHeld('calendar move source or destination is in use')
            if exported and any(db.execute(f'SELECT 1 FROM {table} WHERE group_id=? OR local_id=?',
                                           (group, identity)).fetchone()
                                for table in ('trusted_export_reservations', 'ordinary_export_claims')):
                raise scans.ScanHeld('calendar move source or destination has an unfinished write')
        if db.execute('SELECT 1 FROM calendar_scan_items WHERE local_id=?', (identity,)).fetchone():
            raise scans.ScanHeld('calendar move item is in use')
        encoded = json.dumps(doc, ensure_ascii=False, sort_keys=True)
        for target, token in zip(destinations, tokens):
            db.execute('INSERT INTO calendar_scan_attempts VALUES (?,?,?,?,?,?,?)',
                       (token, target['source_id'], target['id'], 'scanning',
                        json.dumps({'kind': 'calendar_move', 'operation_id': operation}), sync.clock(), encoded))
            db.execute('INSERT INTO calendar_scan_groups VALUES (?,?,?)', (target['id'], target['source_id'], token))
        db.execute('INSERT INTO calendar_scan_items VALUES (?,?)', (identity, tokens[1]))
    return doc


def _check_claims(db, doc):
    for target, token in zip(doc['destinations'], doc['tokens']):
        row = db.execute('SELECT a.state,a.evidence FROM calendar_scan_attempts a JOIN calendar_scan_groups g USING(attempt_id) '
                         'WHERE g.group_id=? AND g.source_id=? AND g.attempt_id=?',
                         (target['id'], target['source_id'], token)).fetchone()
        if not row or row['state'] != 'scanning' or json.loads(row['evidence']) != doc:
            raise ValueError('calendar move claim or evidence changed; review required')
    items = {row['local_id']:row['attempt_id'] for token in doc['tokens']
             for row in db.execute('SELECT local_id,attempt_id FROM calendar_scan_items WHERE attempt_id=?', (token,))}
    if items != {doc['mapping']['alcor_id']:doc['tokens'][1]}:
        raise ValueError('calendar move item claims changed; review required')


def _record(sync, doc, stage, **result):
    updated = {**doc, 'stage': stage, **result}
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _check_claims(db, doc)
        for token in doc['tokens']:
            db.execute('UPDATE calendar_scan_attempts SET evidence=? WHERE attempt_id=?',
                       (json.dumps(updated, ensure_ascii=False, sort_keys=True), token))
    return updated


def _finish(sync, doc, receipt, fingerprint):
    mapping, remote = doc['mapping'], doc['native_result']
    expected = doc['owner_expected']
    if (receipt.get('operation_id') != doc['operation_id'] or
            receipt.get('generation') != expected['generation']):
        raise ValueError('calendar move owner receipt is unavailable')
    current = sync.calendar.apple_capture_calendar_sync(mapping['alcor_id'])
    if not current or current['generation'] != expected['generation']:
        raise ValueError('calendar move local incarnation changed after owner commit')
    local = receipt['local']
    updated = {**mapping, 'calendar_id': remote['calendar_id'], 'event_id': remote['event_id'],
               'external_id': remote.get('external_id') or '',
               'occurrence_date': remote.get('occurrence_date') or remote['start_at'],
               'local_hash': fingerprint(local, mapping, False), 'remote_hash': fingerprint(remote, mapping, True),
               'state': 'active', 'conflict': '', 'missing_since': '', 'updated_at': sync.clock()}
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _check_claims(db, doc)
        _check_manager(db, doc)
        if db.execute('SELECT 1 FROM mappings WHERE id<>? AND calendar_id=? AND event_id=? AND occurrence_date=?',
                      (mapping['id'], updated['calendar_id'], updated['event_id'], updated['occurrence_date'])).fetchone():
            raise ValueError('calendar move result is already mapped')
        sync._save_map(updated, connection=db)
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                   ('item-metadata:'+mapping['alcor_id'], json.dumps([metadata(local), metadata(remote, True)])))
        guard = {'generation': expected['generation'], 'source_id': remote['source_id'], 'group_id': remote['calendar_id']}
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                   ('calendar-binding-generation:'+mapping['alcor_id'], json.dumps(guard, sort_keys=True)))
        complete = json.dumps({**doc, 'stage': 'done', 'owner_receipt': receipt}, ensure_ascii=False, sort_keys=True)
        for token in doc['tokens']:
            db.execute("UPDATE calendar_scan_attempts SET state='done',evidence=? WHERE attempt_id=?", (complete, token))
            db.execute('DELETE FROM calendar_scan_items WHERE attempt_id=?', (token,))
            db.execute('DELETE FROM calendar_scan_groups WHERE attempt_id=?', (token,))


def _cancel_unstarted(sync, doc, reason):
    if doc['stage'] != 'claimed':
        return False
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _check_claims(db, doc)
        current = db.execute('SELECT * FROM mappings WHERE id=?', (doc['mapping']['id'],)).fetchone()
        if current and dict(current) == doc['mapping']:
            sync._save_map({**doc['mapping'], 'state': 'conflict', 'conflict': reason}, connection=db)
        evidence = json.dumps({**doc, 'stage': 'not_sent', 'reason': reason}, ensure_ascii=False, sort_keys=True)
        for token in doc['tokens']:
            db.execute("UPDATE calendar_scan_attempts SET state='not_sent',evidence=? WHERE attempt_id=?", (evidence, token))
            db.execute('DELETE FROM calendar_scan_items WHERE attempt_id=?', (token,))
            db.execute('DELETE FROM calendar_scan_groups WHERE attempt_id=?', (token,))
    return True


def move(sync, service, mapping, original, target, fingerprint):
    inventory = sync.bridge.call('calendars')
    destinations = [scans.destination(inventory, link['apple_id']) for link in (original, target)]
    if (destinations[0]['source_id'] != destinations[1]['source_id'] or
            not all(row.get('writable') for row in destinations)):
        raise ValueError('calendar move requires two writable calendars in the same verified source')
    expected = service.apple_capture_calendar_sync(mapping['alcor_id'], original['alcor_id'])
    if not expected or expected['local']['category_id'] != target['alcor_id']:
        raise ValueError('calendar move local destination changed')
    doc = _begin(sync, dict(mapping), [original, target], destinations, expected)
    try:
        local = expected['local']
        lookup = {key: mapping[key] for key in ('calendar_id', 'event_id', 'external_id', 'occurrence_date')}
        remote = sync.bridge.call('lookup', **lookup).get('event')
        if (not remote or remote.get('calendar_id') != mapping['calendar_id'] or
                remote.get('source_id') != destinations[0]['source_id'] or
                fingerprint(remote, mapping, True) != mapping['remote_hash'] or changed(sync, local, remote)[1]):
            raise ValueError('Apple changed or moved the calendar item; review required')
        fresh = sync.bridge.call('calendars')
        if [scans.destination(fresh, row['id']) for row in destinations] != destinations:
            raise ValueError('calendar move source or destination changed before send')
        service.apple_verify_calendar_move(expected, destinations[0]['id'], destinations[1])
        receipt_file = sync.db_path.parent / 'native-receipts' / ('calendar-move-'+doc['operation_id']+'.json')
        receipt_file.parent.mkdir(parents=True, exist_ok=True)
        payload = sync._write_payload(local, mapping, calendar_id=target['apple_id'], include_receipt=False)
        payload.update(lookup_calendar_id=mapping['calendar_id'], expected_snapshot=remote,
                       expected_source_id=destinations[1]['source_id'], expected_destination_snapshot=destinations[1],
                       receipt_binding=doc['operation_id'], receipt_file=str(receipt_file))
        doc = _record(sync, doc, 'sending', native_before=remote, payload=payload)
        remote = sync.bridge.call('upsert', **payload)['event']
        doc = _record(sync, doc, 'native_result', native_result=remote)
        if (remote.get('calendar_id') != target['apple_id'] or remote.get('source_id') != destinations[1]['source_id']
                or not remote.get('event_id') or fingerprint(remote, mapping, True) != fingerprint(local, mapping, False)):
            raise ValueError('calendar move result differs from the frozen target or content')
        receipt = service.apple_commit_calendar_move(doc['operation_id'], expected, destinations[0]['id'], destinations[1], remote)
        _finish(sync, doc, receipt, fingerprint)
        return 1
    except BaseException as error:
        released = False
        try:
            released = _cancel_unstarted(sync, doc, str(error))
        except Exception:
            pass  # An altered journal or failed transaction is not proof of no dispatch.
        if not released:
            for token in doc['tokens']:
                scans.uncertain(sync, token)
        raise
