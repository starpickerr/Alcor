"""Narrow conditional rebind of an existing calendar after native ID reset.

This is an isolated candidate, not an arbitrary cross-database transaction. Claims
and evidence survive any interrupted or uncertain step; they are not leases.
"""
import hashlib
import json
import secrets
from datetime import datetime, timedelta
from apple_sync import group_scan_claims as scans
from apple_sync.export_reservations import schema as export_schema

class UnverifiedCategoryIdentity(ValueError):
    """Native identity evidence is absent or ambiguous, not a database failure."""


class EmptyCategoryIdentity(UnverifiedCategoryIdentity):
    """No mapped native item exists to prove a container's replacement identity."""


SCHEMA_KEY = '_calendar_rebind_schema'
SCHEMA_VERSION = 'calendar-rebind-v1'
TABLE_SQL = 'CREATE TABLE calendar_rebind_intents (operation_id TEXT PRIMARY KEY, state TEXT NOT NULL, document TEXT NOT NULL)'


def _schema(db):
    row=db.execute('SELECT value FROM settings WHERE key=?',(SCHEMA_KEY,)).fetchone()
    table=db.execute("SELECT type,sql FROM sqlite_master WHERE name='calendar_rebind_intents'").fetchone()
    if not row:
        if table: raise ValueError('unrecognized calendar rebind schema')
        db.execute(TABLE_SQL);db.execute('INSERT INTO settings VALUES (?,?)',(SCHEMA_KEY,json.dumps(SCHEMA_VERSION)))
    elif json.loads(row[0])!=SCHEMA_VERSION or not table or table[0]!='table' or ' '.join(table[1].split())!=TABLE_SQL:
        raise ValueError('incompatible calendar rebind schema')
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='calendar_rebind_intents'").fetchone():
        raise ValueError('unexpected calendar rebind trigger')


def _instant(value):
    if not isinstance(value,str) or not value: raise ValueError('calendar occurrence identity unavailable')
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None: raise ValueError('calendar occurrence timezone unavailable')
    return parsed


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def _begin(sync, link, snapshot, new_category, source_id):
    old_group=link['apple_id'];new_group=new_category['id']
    if not source_id or old_group==new_group or new_category.get('source_id')!=source_id:
        raise ValueError('calendar rebind source identity is not verified')
    operation_id=secrets.token_hex(32); tokens=[secrets.token_hex(32),secrets.token_hex(32)]
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE');scans.schema(db,create=True);exported=export_schema(db);_schema(db)
        current=db.execute('SELECT * FROM category_links WHERE apple_id=?',(old_group,)).fetchone()
        saved=db.execute('SELECT value FROM settings WHERE key=?',('category-snapshot:'+old_group,)).fetchone()
        if not current or dict(current)!=link or not saved or json.loads(saved[0])!=snapshot:
            raise ValueError('calendar link changed before rebind')
        rows=[dict(row) for row in db.execute('SELECT * FROM mappings WHERE calendar_id=? ORDER BY id',(old_group,))]
        if not rows: raise EmptyCategoryIdentity('calendar identity cannot be verified from an empty category')
        for group in (old_group,new_group):
            if db.execute('SELECT 1 FROM calendar_scan_groups WHERE group_id=?',(group,)).fetchone():
                raise ValueError('calendar container is already being reconciled')
            if exported and db.execute('SELECT 1 FROM trusted_export_reservations WHERE group_id=?',(group,)).fetchone():
                raise ValueError('approved export holds a calendar container')
            if exported and db.execute('SELECT 1 FROM ordinary_export_claims WHERE group_id=?',(group,)).fetchone():
                raise ValueError('ordinary calendar write is in progress')
        for row in rows:
            if db.execute('SELECT 1 FROM calendar_scan_items WHERE local_id=?',(row['alcor_id'],)).fetchone():
                raise ValueError('calendar item is already being reconciled')
            if exported and (db.execute('SELECT 1 FROM trusted_export_reservations WHERE local_id=?',(row['alcor_id'],)).fetchone()
                             or db.execute('SELECT 1 FROM ordinary_export_claims WHERE local_id=?',(row['alcor_id'],)).fetchone()):
                raise ValueError('calendar item write is already in progress')
        doc={'version':1,'operation_id':operation_id,'old_group':old_group,'new_group':new_group,'source_id':source_id,
             'link':link,'snapshot':snapshot,'new_category':new_category,'mappings':rows,'tokens':tokens}
        for group,token in zip((old_group,new_group),tokens):
            db.execute('INSERT INTO calendar_scan_attempts VALUES (?,?,?,?,?,?,?)',
                       (token,source_id,group,'scanning',json.dumps({'kind':'calendar_rebind','operation_id':operation_id}),sync.clock(),'{}'))
            db.execute('INSERT INTO calendar_scan_groups VALUES (?,?,?)',(group,source_id,token))
        for row in rows:db.execute('INSERT INTO calendar_scan_items VALUES (?,?)',(row['alcor_id'],tokens[1]))
        db.execute('INSERT INTO calendar_rebind_intents VALUES (?,?,?)',(operation_id,'preparing',json.dumps(doc,ensure_ascii=False,sort_keys=True)))
    return doc


def _save_evidence(sync, doc, owner_expected, native_items):
    complete={**doc,'owner_expected':owner_expected,'native_items':native_items}
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE');scans.schema(db);_schema(db)
        row=db.execute('SELECT * FROM calendar_rebind_intents WHERE operation_id=?',(doc['operation_id'],)).fetchone()
        current=db.execute('SELECT * FROM category_links WHERE apple_id=?',(doc['old_group'],)).fetchone()
        maps=[dict(r) for r in db.execute('SELECT * FROM mappings WHERE calendar_id=? ORDER BY id',(doc['old_group'],))]
        if not row or row['state']!='preparing' or json.loads(row['document'])!=doc or not current or dict(current)!=doc['link'] or maps!=doc['mappings']:
            raise ValueError('calendar rebind manager state changed')
        for token,group in zip(doc['tokens'],(doc['old_group'],doc['new_group'])):
            if not db.execute('SELECT 1 FROM calendar_scan_groups WHERE group_id=? AND attempt_id=? AND source_id=?',(group,token,doc['source_id'])).fetchone():
                raise ValueError('calendar rebind claim lost')
        db.execute('UPDATE calendar_rebind_intents SET state=?,document=? WHERE operation_id=?',('evidence',json.dumps(complete,ensure_ascii=False,sort_keys=True),doc['operation_id']))
    return complete


def _transition(sync, doc, *, committing=False, reason=''):
    # No owner commit may start until this durable transition succeeds.
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE');scans.schema(db);_schema(db)
        journal = db.execute('SELECT state,document FROM calendar_rebind_intents WHERE operation_id=?', (doc['operation_id'],)).fetchone()
        expected_state = 'evidence' if 'owner_expected' in doc else 'preparing'
        if not journal or journal['state'] != expected_state or json.loads(journal['document']) != doc:
            raise ValueError('calendar rebind evidence changed before transition')
        if committing and expected_state != 'evidence':
            raise ValueError('calendar rebind owner evidence is missing')
        for group,token in zip((doc['old_group'],doc['new_group']),doc['tokens']):
            row = db.execute('SELECT a.state,a.evidence FROM calendar_scan_attempts a JOIN calendar_scan_groups g USING(attempt_id) WHERE g.group_id=? AND g.source_id=? AND g.attempt_id=?', (group,doc['source_id'],token)).fetchone()
            if not row or row['state'] != 'scanning' or json.loads(row['evidence']) != {}:
                raise ValueError('calendar rebind claim changed before transition')
        items = {row['local_id']:row['attempt_id'] for token in doc['tokens']
                 for row in db.execute('SELECT local_id,attempt_id FROM calendar_scan_items WHERE attempt_id=?', (token,))}
        if items != {row['alcor_id']:doc['tokens'][1] for row in doc['mappings']}:
            raise ValueError('calendar rebind item claims changed before transition')
        db.execute('UPDATE calendar_rebind_intents SET state=? WHERE operation_id=?', ('committing' if committing else 'not_sent',doc['operation_id']))
        if not committing:
            for token in doc['tokens']:
                db.execute("UPDATE calendar_scan_attempts SET state='not_sent',evidence=? WHERE attempt_id=?", (json.dumps({'kind':'calendar_rebind','operation_id':doc['operation_id'],'reason':reason}),token))
                db.execute('DELETE FROM calendar_scan_items WHERE attempt_id=?', (token,))
                db.execute('DELETE FROM calendar_scan_groups WHERE attempt_id=?', (token,))


def _finish(sync, doc, owner_receipt):
    if not isinstance(owner_receipt,dict) or owner_receipt.get('operation_id')!=doc['operation_id']:
        raise ValueError('calendar rebind owner receipt unavailable')
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE');scans.schema(db);_schema(db)
        journal=db.execute('SELECT * FROM calendar_rebind_intents WHERE operation_id=?',(doc['operation_id'],)).fetchone()
        link=db.execute('SELECT * FROM category_links WHERE apple_id=?',(doc['old_group'],)).fetchone()
        maps=[dict(r) for r in db.execute('SELECT * FROM mappings WHERE calendar_id=? ORDER BY id',(doc['old_group'],))]
        if not journal or journal['state']!='committing' or json.loads(journal['document'])!=doc or not link or dict(link)!=doc['link'] or maps!=doc['mappings']:
            raise ValueError('calendar rebind manager state changed after owner commit')
        for token,group in zip(doc['tokens'],(doc['old_group'],doc['new_group'])):
            if not db.execute('SELECT 1 FROM calendar_scan_groups WHERE group_id=? AND attempt_id=? AND source_id=?',(group,token,doc['source_id'])).fetchone():
                raise ValueError('calendar rebind claim lost after owner commit')
        for mapping in doc['mappings']:
            held=db.execute('SELECT attempt_id FROM calendar_scan_items WHERE local_id=?',(mapping['alcor_id'],)).fetchone()
            if not held or held[0]!=doc['tokens'][1]: raise ValueError('calendar rebind item claim lost')
        for mapping,native in zip(doc['mappings'],doc['native_items']):
            conflict=db.execute('SELECT 1 FROM mappings WHERE id<>? AND calendar_id=? AND event_id=? AND occurrence_date=?',
                                (mapping['id'],doc['new_group'],native['event_id'],native['occurrence_date'])).fetchone()
            if conflict: raise ValueError('new native calendar item is already mapped')
        db.execute('UPDATE category_links SET apple_id=? WHERE apple_id=?',(doc['new_group'],doc['old_group']))
        for mapping,native,owner_item in zip(doc['mappings'],doc['native_items'],doc['owner_expected']['items']):
            if owner_item['id'] != mapping['alcor_id']:
                raise ValueError('calendar rebind owner evidence order changed')
            db.execute('UPDATE mappings SET calendar_id=?,event_id=?,external_id=?,occurrence_date=? WHERE id=?',
                       (doc['new_group'],native['event_id'],native['external_id'],native['occurrence_date'],mapping['id']))
            guard={'generation':owner_item['generation'],'source_id':doc['source_id'],'group_id':doc['new_group']}
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('calendar-binding-generation:'+mapping['alcor_id'],json.dumps(guard,sort_keys=True)))
        db.execute('UPDATE calendar_rebind_intents SET state=? WHERE operation_id=?',('committed',doc['operation_id']))
        for token in doc['tokens']:
            db.execute("UPDATE calendar_scan_attempts SET state='done',evidence=? WHERE attempt_id=?",(json.dumps({'kind':'calendar_rebind','operation_id':doc['operation_id']}),token))
            db.execute('DELETE FROM calendar_scan_items WHERE attempt_id=?',(token,))
            db.execute('DELETE FROM calendar_scan_groups WHERE attempt_id=?',(token,))


def _verify_destination(sync, category):
    inventory=sync.bridge.call('calendars')
    matches=[row for row in inventory.get('calendars',[]) if row.get('id')==category.get('id')]
    if len(matches)!=1:
        raise UnverifiedCategoryIdentity('calendar target identity is unavailable or ambiguous')
    current={**matches[0],'default':matches[0]['id']==inventory.get('default_id')}
    if current!=category or not current.get('source_id') or current['source_id'] not in inventory.get('source_ids',[]):
        raise UnverifiedCategoryIdentity('calendar target or source changed; review required')
    groups=[row.get('id') for row in inventory.get('calendars',[]) if row.get('source_id')==current['source_id']]
    if any(not isinstance(group,str) or not group for group in groups) or len(groups)!=len(set(groups)):
        raise UnverifiedCategoryIdentity('calendar source inventory is ambiguous')
    return sorted(groups)


def _match_native(sync, doc, mapping, source_groups):
    external=mapping.get('external_id');occ=_instant(mapping.get('occurrence_date'))
    if not isinstance(external,str) or not external: raise ValueError('calendar external identity unavailable')
    inventory=sync.bridge.call('events',calendar_ids=source_groups,
                               start=(occ-timedelta(days=2)).isoformat(),end=(occ+timedelta(days=2)).isoformat())
    if (not set(source_groups) <= set(inventory.get('successful_calendar_ids',[])) or
            set(source_groups) & set(inventory.get('failed_calendar_ids',[]))):
        raise UnverifiedCategoryIdentity('calendar source inventory incomplete')
    rows=inventory.get('events',[])
    if any(row.get('calendar_id') not in source_groups or row.get('source_id')!=doc['source_id'] for row in rows):
        raise UnverifiedCategoryIdentity('calendar source inventory identity changed')
    matches=[row for row in rows if row.get('external_id')==external and _instant(row.get('occurrence_date'))==occ]
    if len(matches)!=1 or matches[0].get('calendar_id')!=doc['new_group'] or not matches[0].get('event_id'):
        raise UnverifiedCategoryIdentity('calendar native item identity is unavailable or ambiguous across the source')
    return matches[0]


def rebind(sync, service, link, snapshot, new_category):
    """Only uniquely identified ext-id/occurrences in the same source can rebind."""
    source_groups=_verify_destination(sync,new_category)
    source_id=snapshot.get('source_id');doc=_begin(sync,link,snapshot,new_category,source_id)
    try:
        expected=service.apple_prepare_calendar_rebind(link['alcor_id'],doc['old_group'],source_id,doc['mappings'])
        native_items=[];seen=set()
        for mapping in doc['mappings']:
            item=_match_native(sync,doc,mapping,source_groups);key=(item['event_id'],_instant(item['occurrence_date']))
            if key in seen: raise UnverifiedCategoryIdentity('calendar native identity is ambiguous')
            seen.add(key);native_items.append(item)
        doc=_save_evidence(sync,doc,expected,native_items)
        if _verify_destination(sync,new_category)!=source_groups:
            raise UnverifiedCategoryIdentity('calendar source inventory changed during rebind')
        if [_match_native(sync,doc,mapping,source_groups) for mapping in doc['mappings']] != native_items:
            raise UnverifiedCategoryIdentity('calendar native items changed during rebind; review required')
        _transition(sync,doc,committing=True)
        receipt=service.apple_commit_calendar_rebind(doc['operation_id'],expected,new_category,native_items)
        _finish(sync,doc,receipt)
        return {'operation_id':doc['operation_id'],'replayed':False}
    except BaseException as error:
        released = False
        try:
            _transition(sync,doc,reason=str(error))
            released = True
        except Exception:
            pass  # Committing or changed evidence cannot be released on an RPC error.
        if not released:
            for token in doc['tokens']:
                try:scans.uncertain(sync,token)
                except Exception:pass
        raise
