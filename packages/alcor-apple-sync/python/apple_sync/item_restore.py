"""The existing keep-Apple choice for one missing local item; no native writes."""
import json
import secrets
from apple_sync.group_scan_claims import WorkClaim,digest
from apple_sync.contracts import locate_remote
from apple_sync.metadata import metadata


def _keys(kind):
    return ('todo_id','list_id') if kind=='reminders' else ('alcor_id','calendar_id')


def pending(sync):
    from apple_sync.group_scan_claims import schema
    with sync._connect() as db:
        held=[]
        if schema(db):
            held=[json.loads(row[0]).get('evidence') for row in db.execute(
                'SELECT a.evidence FROM calendar_scan_attempts a JOIN calendar_scan_groups g USING(attempt_id)')]
        result=set()
        for row in db.execute("SELECT value FROM settings WHERE key LIKE 'manual-item-restore:%'"):
            journal=json.loads(row[0])
            if journal['state']=='owner_pending' or journal['state']=='done' and journal['evidence'] in held:
                result.add(journal['plan']['local_id'])
        return result


def _baseline(sync,kind,identity,connection=None):
    if kind=='reminders':return sync._reminder_baselines(identity,connection)
    if connection is None:
        with sync._connect() as db:return _baseline(sync,kind,identity,db)
    result={}
    for prefix in ('item-metadata:','calendar-binding-generation:'):
        row=connection.execute('SELECT value FROM settings WHERE key=?',(prefix+identity,)).fetchone()
        result[prefix]=json.loads(row[0]) if row else None
    return result


def prepare(sync,owner,kind,identity,operation_id=None):
    from apple_sync.category_rebuild import guard_retained
    from apple_sync.calendar import _payload as calendar_payload
    from apple_sync.reminders import _payload as reminder_payload
    guard_retained(sync,identity)
    local_key,group=_keys(kind)
    mapping=next((row for row in sync._maps() if row[local_key]==identity),None)
    journal=sync._get('manual-item-restore:'+identity)
    if journal and (journal['state']=='owner_pending' or journal['state']=='done' and mapping==journal['result_mapping']):
        _check_plan(journal['plan'],kind)
        if journal['plan']['local_id']!=identity:raise ValueError('restore journal target changed')
        return {'mode':'receipt_finish','plan':journal['plan']}
    if not mapping or mapping['state']!='conflict' or sync._local(identity) is not None:
        raise ValueError('仅支持本地已删除项目的保留 Apple 恢复，请重新核对')
    native=locate_remote(sync,mapping,kind)
    if not native:raise ValueError('Apple 项目不可用，无法恢复；双方证据已保留')
    inventory=sync.bridge.call('reminder_lists' if kind=='reminders' else 'calendars')
    groups={row['id']:row for row in inventory['lists' if kind=='reminders' else 'calendars']}
    involved={mapping[group],native[group]}
    sources={key:groups[key]['source_id'] for key in involved if key in groups and groups[key].get('source_id') in inventory.get('source_ids',[])}
    if set(sources)!=involved or any(source!=native.get('source_id') for source in sources.values()):
        raise ValueError('原关联与 Apple 恢复来源无法一致核实，请核对')
    links=sync._stored_links();category=next((row['alcor_id'] for row in links if row['apple_id']==native[group] and row['enabled']),None)
    if not category:raise ValueError('恢复目标分类尚未明确选择，请核对关联')
    payload=(reminder_payload if kind=='reminders' else calendar_payload)(native,category)
    expected=owner.apple_capture_item_restore(identity,payload,category,native,mapping['source_key'] if mapping['origin']=='apple' else None)
    plan={'version':1,'kind':kind,'operation_id':operation_id or secrets.token_hex(32),'local_id':identity,
          'mapping':mapping,'native':native,'owner_expected':expected,'links':links,'groups':sources,
          'inventory':inventory,'baseline':_baseline(sync,kind,identity)}
    return {**plan,'plan_fingerprint':digest(plan)}


def _check_plan(plan,kind):
    fields={'version','kind','operation_id','local_id','mapping','native','owner_expected','links','groups','inventory','baseline','plan_fingerprint'}
    if (not isinstance(plan,dict) or set(plan)!=fields or type(plan.get('version')) is not int or plan['version']!=1 or plan.get('kind')!=kind or
            plan.get('plan_fingerprint')!=digest({k:v for k,v in plan.items() if k!='plan_fingerprint'})):
        raise ValueError('missing-local restore plan changed')
    if plan['mapping'].get(_keys(kind)[0])!=plan['local_id'] or plan['owner_expected'].get('local_id')!=plan['local_id']:
        raise ValueError('missing-local restore target changed')


def _check_manager(sync,kind,plan,db,claim):
    claim._check(db);local_key,_=_keys(kind)
    row=db.execute('SELECT * FROM mappings WHERE '+local_key+'=?',(plan['local_id'],)).fetchone()
    links=[dict(row) for row in db.execute('SELECT * FROM category_links ORDER BY apple_id')]
    if not row or dict(row)!=plan['mapping'] or links!=plan['links'] or _baseline(sync,kind,plan['local_id'],db)!=plan['baseline']:
        raise ValueError('missing-local mapping or selection changed; review required')


def _finish(sync,owner,kind,plan,claim,journal):
    verified=owner.apple_verify_item_restore(plan['operation_id'],plan['owner_expected'])
    if verified is None:raise ValueError('missing-local owner commit is not proven; no retry')
    local,remote=verified['local'],plan['native'];_,group=_keys(kind)
    updated={**plan['mapping'],**sync._mapping_from_native(local,remote,plan['mapping']['origin'],plan['mapping']['source_key'])}
    values={'item-metadata:':[metadata(local),metadata(remote,True)],
            ('reminder-binding-generation:' if kind=='reminders' else 'calendar-binding-generation:'):
                {'generation':verified['generation'],'source_id':remote['source_id'],'group_id':remote[group]}}
    if kind=='reminders':values['reminder-local-remind-at:']={'value':local.get('remind_at')}
    key='manual-item-restore:'+plan['local_id']
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE');_check_manager(sync,kind,plan,db,claim)
        saved=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        if not saved or json.loads(saved[0])!=journal:raise ValueError('restore receipt journal changed')
        sync._save_map(updated,connection=db)
        for prefix,value in values.items():db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(prefix+plan['local_id'],json.dumps(value)))
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps({**journal,'state':'done','result_mapping':updated,'receipt':verified['receipt']})))


def finish_pending(sync,owner,kind,identity):
    journal=sync._get('manual-item-restore:'+identity)
    if not journal:return False
    plan=journal['plan'];_check_plan(plan,kind)
    local_key,_=_keys(kind)
    if journal['state']=='done' and next((row for row in sync._maps() if row[local_key]==identity),None)!=journal['result_mapping']:
        return False
    verified=owner.apple_verify_item_restore(plan['operation_id'],plan['owner_expected'])
    if verified is None:return False
    with WorkClaim(sync,plan['groups'],[identity],journal['evidence'],recovery=True,completed_owner_receipt=True) as claim:
        if journal['state']=='done':
            with sync._connect() as db:
                db.execute('BEGIN IMMEDIATE');claim._check(db)
                row=db.execute('SELECT * FROM mappings WHERE '+local_key+'=?',(identity,)).fetchone()
                if not row or dict(row)!=journal['result_mapping']:raise ValueError('completed restore mapping changed')
        else:_finish(sync,owner,kind,plan,claim,journal)
    return True


def run(sync,owner,kind,plan,authorize):
    _check_plan(plan,kind);identity=plan['local_id']
    journal=sync._get('manual-item-restore:'+identity)
    if journal and journal['state']=='owner_pending' and journal['plan']!=plan:
        raise ValueError('unfinished restore plan differs; review required')
    if finish_pending(sync,owner,kind,identity):return sync.status()
    evidence={'kind':'manual_item_restore','operation_id':plan['operation_id'],'plan':plan}
    with WorkClaim(sync,plan['groups'],[identity],evidence) as claim:
        with sync._connect() as db:
            db.execute('BEGIN IMMEDIATE');_check_manager(sync,kind,plan,db,claim)
        if (sync.bridge.call('reminder_lists' if kind=='reminders' else 'calendars')!=plan['inventory'] or
                locate_remote(sync,plan['mapping'],kind)!=plan['native']):
            raise ValueError('Apple 恢复内容或来源已变化，请重新核对')
        expected=plan['owner_expected']
        if owner.apple_capture_item_restore(identity,expected['payload'],expected['category_id'],plan['native'],expected['source_key'])!=expected:
            raise ValueError('INBOUND_CHANGED: restore owner changed before commit')
        if authorize() is not True:raise PermissionError('恢复需要本次具体选择的有效确认')
        journal={'version':1,'state':'owner_pending','plan':plan,'evidence':evidence}
        with sync._connect() as db:
            db.execute('BEGIN IMMEDIATE');_check_manager(sync,kind,plan,db,claim)
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('manual-item-restore:'+identity,json.dumps(journal)))
        claim.started('owner_claiming')
        try:owner.apple_restore_item(plan['operation_id'],expected)
        except ValueError as error:
            if str(error).startswith('INBOUND_CHANGED:'):
                with sync._connect() as db:
                    db.execute('BEGIN IMMEDIATE');claim._check(db)
                    key='manual-item-restore:'+identity
                    saved=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
                    if not saved or json.loads(saved[0])!=journal:raise ValueError('restore rejection journal changed')
                    db.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps({**journal,'state':'owner_rejected'}),key))
                claim.owner_rejected()
            raise
        claim.started('owner_committed')
        _finish(sync,owner,kind,plan,claim,journal)
    return sync.status()


def finish_prepared(sync,owner,kind,plan):
    _check_plan(plan,kind)
    journal=sync._get('manual-item-restore:'+plan['local_id'])
    if not journal or journal['plan']!=plan or not finish_pending(sync,owner,kind,plan['local_id']):
        raise ValueError('恢复是否提交尚无法证实；已保留证据，不会重复恢复')
    return sync.status()
