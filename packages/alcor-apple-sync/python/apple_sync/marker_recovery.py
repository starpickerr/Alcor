"""Existing marker reattachment: owner CAS, then checked manager mapping/baseline commit."""
import json
from apple_sync.group_scan_claims import WorkClaim,claim_items,digest,uncertain
from apple_sync.category_rebuild import guard_retained
from apple_sync.metadata import metadata


class MarkerRecoveryError(RuntimeError):
    pass

def pending(sync):
    with sync._connect() as db:
        return [json.loads(row[0]) for row in db.execute("SELECT value FROM settings WHERE key LIKE 'marker-conflict:%' ORDER BY key")]


def status(sync):
    return [{key:row[key] for key in ('local_id','title','source_id','group_id','state','reason','observed_at')} for row in pending(sync)]


def pending_ids(sync):
    return {row['local_id'] for row in pending(sync)}


def pending_native(sync,native):
    for row in pending(sync):
        original=row['native'];group='list_id' if row['kind']=='reminders' else 'calendar_id'
        identity='reminder_id' if row['kind']=='reminders' else 'event_id'
        if native.get('alcor_id')==row['local_id'] or all(native.get(key)==original.get(key) for key in (group,identity,'source_id')):
            return True
    return False


def guard_pending(sync,identity):
    if identity in pending_ids(sync):
        raise ValueError('旧关联来源或当前内容尚未核实；项目与 Apple 内容保留待核对，不自动重新关联或发送')


def recover(sync,owner,kind,local,native,*,scan_token=None,work_claim=None,scan_safe=False):
    reminder=kind=='reminders';group='list_id' if reminder else 'calendar_id';key='todo_id' if reminder else 'alcor_id'
    identity=local['id'];guard_retained(sync,identity);guard_pending(sync,identity)
    if scan_token is None and work_claim is None:
        with WorkClaim(sync,{native[group]:native.get('source_id')},[identity],{'kind':'marker_recovery','local':local,'native':native}) as claim:
            return recover(sync,owner,kind,local,native,work_claim=claim)
    if scan_token:claim_items(sync,scan_token,[identity])
    expected=owner.apple_capture_marker_binding(identity)
    if expected is None or any(local.get(k)!=v for k,v in expected['local'].items()):
        raise ValueError('MARKER_CHANGED: marker target is no longer the observed item')
    category=owner.apple_capture_category_absence(local.get('category_id'))
    prefixes=['item-metadata:']+(['reminder-local-remind-at:','reminder-binding-generation:'] if reminder else ['calendar-binding-generation:'])
    def maps(db):return [dict(row) for row in db.execute('SELECT * FROM mappings ORDER BY '+('todo_id' if reminder else 'id'))]
    def baselines(db):
        return {prefix:(lambda row:json.loads(row[0]) if row else None)(db.execute('SELECT value FROM settings WHERE key=?',(prefix+identity,)).fetchone()) for prefix in prefixes}
    def scan_state(db):
        row=db.execute('SELECT * FROM calendar_scan_attempts WHERE attempt_id=?',(scan_token,)).fetchone()
        groups=[dict(x) for x in db.execute('SELECT * FROM calendar_scan_groups WHERE attempt_id=?',(scan_token,))]
        items=sorted(x[0] for x in db.execute('SELECT local_id FROM calendar_scan_items WHERE attempt_id=?',(scan_token,)))
        return {'attempt':dict(row) if row else None,'groups':groups,'items':items}
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if work_claim:work_claim._check(db)
        stamp=scan_state(db) if scan_token else None
        if scan_token and (not stamp['attempt'] or stamp['attempt']['state']!='scanning' or json.loads(stamp['attempt']['evidence'])!={}
                or stamp['groups']!=[{'group_id':native[group],'source_id':native['source_id'],'attempt_id':scan_token}]
                or identity not in stamp['items']):raise ValueError('marker scan claim changed')
        before=maps(db);baseline=baselines(db)
        if any(row[key]==identity for row in before):raise ValueError('marker already mapped')
    operation=digest({'tokens':[scan_token] if scan_token else work_claim.tokens,'expected':expected,'native':native})
    def check(db):
        if work_claim:work_claim._check(db)
        if scan_token and scan_state(db)!=stamp:raise ValueError('marker scan claim or evidence changed')
        if maps(db)!=before or baselines(db)!=baseline:raise ValueError('marker mapping or baseline changed')
    def stage(value):
        nonlocal stamp
        if work_claim:work_claim.started(value)
        if scan_token:
            with sync._connect() as db:
                db.execute('BEGIN IMMEDIATE');check(db)
                evidence={'stage':value,'kind':'marker_recovery','expected':expected,'native':native,'operation_id':operation}
                db.execute('UPDATE calendar_scan_attempts SET evidence=? WHERE attempt_id=?',(json.dumps(evidence,ensure_ascii=False,sort_keys=True),scan_token))
                stamp=scan_state(db)
    from apple_sync.reminders import _supported,_hash
    issues=([] if _supported(native) else ['此提醒包含复杂重复或位置/多重提醒，完整保留；请在 Apple 中编辑']) if reminder else (['包含多条原生重复规则，请在 Apple 中编辑'] if (native.get('recurrence') or {}).get('rule_count',0)>1 else [])
    stage('owner_claiming')
    try:
        saved=owner.apple_bind_marker_if_unchanged(operation,expected,category,native,issues)
        stage('owner_committed')
        mapping=sync._mapping_from_native(saved,native,'alcor')
        if reminder and _hash(local)!=_hash(native,remote=True):mapping.update(state='conflict',conflict='Alcor and Apple differ after an interrupted export')
        with sync._connect() as db:
            db.execute('BEGIN IMMEDIATE');check(db)
            sync._save_map(mapping,connection=db)
            values={'item-metadata:':[metadata(saved),metadata(native,True)],
                    ('reminder-binding-generation:' if reminder else 'calendar-binding-generation:'):{'generation':expected['generation'],'source_id':native['source_id'],'group_id':native[group]}}
            if reminder:values['reminder-local-remind-at:']={'value':saved.get('remind_at')}
            db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)',[(prefix+identity,json.dumps(value,ensure_ascii=False)) for prefix,value in values.items()])
            if scan_token:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE attempt_id=?",(scan_token,))
        return mapping
    except BaseException as error:
        if work_claim and isinstance(error,ValueError) and str(error).startswith('MARKER_CHANGED:') and work_claim.document.get('stage')=='owner_claiming':
            work_claim.owner_rejected()
        if (scan_token and scan_safe and isinstance(error,ValueError) and str(error).startswith('MARKER_CHANGED:')
                and json.loads(stamp['attempt']['evidence']).get('stage')=='owner_claiming'):
            # This private owner contract rejects only before writes and rolls back its
            # transaction. Resume the same scan, not an unknown/committed operation.
            with sync._connect() as db:
                db.execute('BEGIN IMMEDIATE');check(db)
                document={'kind':kind,'local_id':identity,'title':local.get('title',''),
                          'source_id':native['source_id'],'group_id':native[group],
                          'state':'marker_conflict','reason':str(error),'observed_at':sync.clock(),
                          'expected':expected,'native':native,'operation_id':operation,'attempt_id':scan_token}
                db.execute('INSERT INTO settings VALUES (?,?)',('marker-conflict:'+identity,json.dumps(document,ensure_ascii=False)))
                db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE attempt_id=?",(scan_token,))
                db.execute('DELETE FROM calendar_scan_items WHERE attempt_id=? AND local_id=?',(scan_token,identity))
            return None
        if scan_token:uncertain(sync,scan_token)
        raise
