"""One concrete deletion over the existing live Host approval and owner outbox."""
import copy
import json
from pathlib import Path
from apple_sync.approved_export import digest
from apple_sync.contracts import native_delete_preview, _operation_claim
from apple_sync.bridge import BridgeError


def prepare(sync, domain, operation_id, local_plan=None, *, category_id=None, prepare_owner=None):
    kind = 'reminders' if domain == 'tasks' else 'calendar'
    if domain not in ('tasks', 'calendar') or not isinstance(operation_id, str) or len(operation_id) != 64 or any(c not in '0123456789abcdef' for c in operation_id):
        raise ValueError('invalid approved deletion request')
    if category_id is not None:
        if local_plan is not None or not isinstance(category_id, str) or not category_id or prepare_owner is None:
            raise ValueError('invalid category deletion preparation')
        links = [row for row in sync._stored_links() if row['alcor_id'] == category_id and row['enabled']]
        if len(links) != 1:
            raise ValueError('category deletion needs one current enabled association')
        if links[0]['direction'] == 'import':
            owner = sync.todos if kind == 'reminders' else sync.calendar
            binding = owner.get_category(category_id).get('apple') or {}
            if binding.get('apple_id') != links[0]['apple_id'] or not binding.get('source_id'):
                raise ValueError('import category source changed')
            local_plan = prepare_owner('prepare_import_category_delete', domain=domain, category_id=category_id, operation_id=operation_id)
            if local_plan.get('replayed'):
                raise ValueError('completed import removal is not a new approval')
            plan = {'version':1, 'domain':domain, 'kind':kind, 'operation_id':operation_id, 'mode':'local-import-unlink',
                    'local_plan':local_plan, 'link_snapshot':links[0], 'source_id':binding['source_id']}
            return {**plan, 'plan_fingerprint':digest(plan)}
        local_plan = prepare_owner('prepare_apple_delete', domain=domain, method='delete_category',
                                   arguments={'category_id' if domain=='tasks' else 'id':category_id}, operation_id=operation_id)
    if not isinstance(local_plan, dict) or local_plan.get('replayed'):
        raise ValueError('existing deletion must be reconciled, not newly approved')
    capability = sync.bridge.call('reminder_status' if kind == 'reminders' else 'status')
    if capability.get('authorization') != 'full_access' or capability.get('delete_write_version') != 1:
        raise PermissionError('当前 Apple helper 不支持条件删除回执，请先更新组件')
    apple = local_plan.get('apple') or {}
    owner = sync.todos if kind == 'reminders' else sync.calendar
    native = native_delete_preview(sync, owner, kind, apple.get('entity'), local_plan.get('id'), apple.get('scope'), apple.get('occurrence_on'))
    links = [row for row in sync._stored_links() if row['apple_id'] == native['group_id'] and row['enabled']]
    if len(links) != 1 or links[0]['direction'] not in ('export', 'bidirectional'):
        raise ValueError('此关联仅从 Apple 导入，不能向 Apple 删除；请选择仅处理 Alcor 本地关联')
    plan = {'version': 1, 'domain': domain, 'kind': kind, 'operation_id': operation_id,
            'local_plan': copy.deepcopy(local_plan), 'native': native}
    return {**plan, 'plan_fingerprint': digest(plan)}


def _dispatch_ready(sync,plan):
    if not sync._get('connected',False):raise ValueError('Apple 同步已暂停，具体删除待办已保留')
    links=[row for row in sync._stored_links() if row['apple_id']==plan['native']['group_id'] and row['enabled']]
    if len(links)==1 and links[0]['direction']=='import':
        raise ValueError('此关联仅从 Apple 导入，不能向 Apple 删除；请选择仅处理 Alcor 本地关联')
    if len(links)!=1 or links[0]['direction'] not in ('export','bidirectional'):
        raise ValueError('Apple 关联方向或启用状态变化，删除已暂停')
    status=sync.bridge.call('reminder_status' if plan['kind']=='reminders' else 'status')
    if status.get('authorization')!='full_access' or status.get('delete_write_version')!=1:
        raise PermissionError('Apple 删除所需权限或组件能力已变化，待办已保留')


def _reset_unsent(sync,claim,owner_action,operation):
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE');claim._check(db)
    reset=owner_action('apple_reset_approved_delete',operation_id=operation['id'],document=operation['document'],binding=operation['binding'])
    if reset!={'state':'pending','not_sent':True}:raise ValueError('owner did not confirm the unsent deletion reset')
    claim.native_rejected()


def operation_status(sync,operations):
    result=[]
    for operation in operations:
        row=dict(operation)
        if row['action'] in ('delete','truncate','exclude'):
            phase=row['state']
            if phase=='pending':
                journal=sync._get('approved-delete:'+row['id']) or {}
                phase='not_sent' if journal.get('state')=='not_sent' else 'queued' if row['document'].get('_confirmed_intent') else 'needs_confirmation'
                if phase=='not_sent':row['error']=journal.get('error') or row.get('error','')
            row['deletion_phase']=phase
        result.append(row)
    return result


def _operation(sync, owner, plan, previous=None):
    rows = [row for row in owner.apple_operations() if row['id'] == plan['operation_id']]
    if previous and previous.get('operation'):
        saved=previous['operation']
        if rows and any(rows[0][key]!=saved[key] for key in ('id','entity','local_id','action','document')):
            raise ValueError('owner deletion evidence changed after native receipt')
        rows=[saved]
    if len(rows) != 1:
        raise ValueError('approved deletion is cancelled, completed or unavailable')
    operation = rows[0]
    if (operation['local_id'] != plan['local_plan']['id'] or operation['entity'] != plan['native']['entity']
            or operation['action'] not in ('delete', 'truncate', 'exclude')
            or operation['document'].get('_approval') != {'snapshot': plan['local_plan']['snapshot'], 'native': plan['native']}):
        raise ValueError('approved deletion plan differs from the queued owner snapshot')
    return operation


def _payload(plan):
    native = plan['native'];row = native['native_snapshot'];reminder = plan['kind'] == 'reminders'
    category = native['entity'] == 'category'
    op = ('reminder_list_delete' if reminder else 'delete_calendar') if category else ('reminder_delete' if reminder else 'delete')
    payload = {'list_id' if reminder else 'calendar_id': native['group_id'], 'expected_source_id': native['source_id'],
               'expected_snapshot': row, 'require_match': True, 'delete_write_version': 1}
    if not category:
        payload.update({'reminder_id' if reminder else 'event_id': native['instance_apple_id'], 'require_exact_identity': True})
        if not reminder:
            payload.update(occurrence_date=row.get('occurrence_date') or row['start_at'], series=native['scope'] == 'future')
    return op, payload


def _receipt(plan, op, payload):
    return {'version': 1, 'binding': payload['receipt_binding'], 'op': op, 'source_id': plan['native']['source_id'],
            'expected_snapshot': plan['native']['native_snapshot'], 'series': payload.get('series', False)}


def _native_receipt(sync,plan):
    op,payload=_payload(plan)
    payload['receipt_binding']=digest({'plan':plan,'op':op,'payload':payload})
    path=sync.db_path.parent/'native-receipts'/('delete-'+plan['operation_id']+'.json')
    payload['receipt_file']=str(path)
    proof=_receipt(plan,op,payload)
    expected={'binding':payload['receipt_binding'],'deleted':True,'delete_receipt':proof}
    receipt=json.loads(path.read_text()) if path.exists() else None
    if receipt is not None and receipt!=expected:
        raise ValueError('Apple 删除发送或回执状态未知，请核对；不会重复提交')
    return op,payload,path,proof,expected,receipt


def _held_operation(sync,operation_id):
    from apple_sync.group_scan_claims import schema
    with sync._connect() as db:
        if not schema(db):return False
        for row in db.execute('SELECT a.evidence FROM calendar_scan_attempts a JOIN calendar_scan_groups g USING(attempt_id)'):
            evidence=json.loads(row['evidence'])
            work=evidence.get('evidence') or {}
            if work.get('id')==operation_id or (work.get('plan') or {}).get('operation_id')==operation_id:return True
    return False


def finish_receipts(sync):
    """Existing outbox recovery: a completed native receipt can only finish locally."""
    finish=getattr(sync,'finish_approved_delete',None)
    with sync._connect() as db:
        journals=[json.loads(row[0]) for row in db.execute("SELECT value FROM settings WHERE key LIKE 'approved-delete:%'")]
    handled=set();categories=items=0;errors=[]
    for journal in journals:
        plan=journal.get('plan') or {};operation=journal.get('operation') or {}
        if plan.get('mode')=='local-import-unlink':
            if journal.get('state')=='done' and not _held_operation(sync,plan['operation_id']):continue
            try:
                _finish_import_receipt(sync,plan,journal);handled.add(plan['operation_id'])
            except Exception as error:errors.append(str(error))
            continue
        if not finish or plan.get('mode') or not operation.get('document',{}).get('_confirmed_intent'):continue
        if journal.get('state')=='done' and not _held_operation(sync,plan['operation_id']):continue
        try:
            if not _native_receipt(sync,plan)[-1]:continue
            handled.add(plan['operation_id'])
            result=finish(plan)
            if not result or result.get('state')!='done':raise ValueError('本机删除回执已保留，本地完成待核对')
            if operation['entity']=='category':categories+=1
            else:items+=1
        except Exception as error:errors.append(str(error))
    return handled,categories,items,errors


class RemainingSeriesChanged(ValueError):
    pass


def _remaining_series(sync, owner, operation, maps, claim):
    """Refresh only the known post-delete series, without blessing concurrent edits."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from apple_sync.calendar import _fingerprint
    from apple_sync.metadata import metadata
    from apple_sync.contracts import locate_remote
    identity=operation['local_id'];document=operation['document']
    mapping=next(row for row in maps if row['alcor_id']==identity)
    prior=copy.deepcopy(mapping);baseline=sync._get('item-metadata:'+identity)
    capture=owner.apple_capture_calendar_sync(identity)
    expected=copy.deepcopy(document['_expected_local'])
    until=(datetime.fromisoformat(document['occurrence_on']).date()-timedelta(days=1)).isoformat()
    if operation['action']=='truncate':expected['repeat_until']=until
    if not capture or capture['generation']!=document['_approved_generation'] or capture['row']!=expected:
        raise RemainingSeriesChanged('删除已完成，本地重复日程后来有变化，保留内容待核对')
    old=operation['binding']['native'];remote=locate_remote(sync,mapping,'calendar')
    if remote is None and operation['action']=='exclude':
        anchor=datetime.fromisoformat(mapping['occurrence_date'].replace('Z','+00:00'))
        inventory=sync.bridge.call('events',calendar_ids=[mapping['calendar_id']],start=(anchor-timedelta(days=2)).isoformat(),end=(anchor+timedelta(days=370)).isoformat())
        if mapping['calendar_id'] not in inventory.get('successful_calendar_ids',[]) or inventory.get('failed_calendar_ids'):
            raise RemainingSeriesChanged('删除已完成，重复日程剩余实例尚无法完整读取')
        candidates=[row for row in inventory.get('events',[]) if row.get('calendar_id')==mapping['calendar_id']
                    and row.get('source_id')==old['source_id'] and row.get('external_id')==old.get('external_id')
                    and row.get('occurrence_date')!=mapping['occurrence_date'] and not row.get('is_detached')]
        candidates.sort(key=lambda row:row['start_at'])
        if candidates and len({row['occurrence_date'] for row in candidates})==len(candidates):remote=candidates[0]
    if (not remote or not old.get('external_id') or remote.get('external_id')!=old['external_id']
            or remote.get('source_id')!=old['source_id'] or remote.get('calendar_id')!=old['calendar_id'] or remote.get('is_detached')):
        raise RemainingSeriesChanged('删除已完成，剩余实例身份待核对；不会重发删除')
    comparable=copy.deepcopy(remote)
    instant=lambda value:datetime.fromisoformat(value.replace('Z','+00:00'))
    if instant(remote['end_at'])-instant(remote['start_at'])!=instant(old['end_at'])-instant(old['start_at']):
        raise RemainingSeriesChanged('删除后剩余实例时长变化，请核对')
    comparable.update(start_at=old['start_at'],end_at=old['end_at'])
    if operation['action']=='truncate':
        actual=(remote.get('recurrence') or {}).get('until')
        if not actual or instant(actual).astimezone(ZoneInfo(expected.get('repeat_timezone') or 'UTC')).date().isoformat()!=until:
            raise RemainingSeriesChanged('删除后重复截止范围不一致，请核对')
        comparable['recurrence']=dict(comparable.get('recurrence') or {})
        if 'until' in (old.get('recurrence') or {}):comparable['recurrence']['until']=old['recurrence']['until']
        else:comparable['recurrence'].pop('until',None)
    if (_fingerprint(comparable,series=True,remote=True)!=_fingerprint(old,series=True,remote=True)
            or metadata(comparable,True)!=metadata(old,True)):
        raise RemainingSeriesChanged('删除后剩余重复内容变化，请核对')
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE');claim._check(db)
    applied=owner.apple_apply_calendar_inbound(capture,{},remote,metadata_only=True)
    return (mapping,prior,baseline,applied,remote,capture['generation'])


def run(sync, plan, authorize, owner_action):
    frozen = copy.deepcopy(plan)
    fingerprint = frozen.pop('plan_fingerprint', None)
    if fingerprint != digest(frozen) or frozen.get('version') != 1 or frozen.get('domain') not in ('tasks', 'calendar'):
        raise ValueError('approved deletion plan changed')
    plan = {**frozen, 'plan_fingerprint': fingerprint}
    kind = plan['kind'];owner = sync.todos if kind == 'reminders' else sync.calendar
    if kind != ('reminders' if plan['domain'] == 'tasks' else 'calendar'):
        raise ValueError('approved deletion domain changed')
    if plan.get('mode') == 'local-import-unlink':
        return _unlink_import(sync, plan, authorize, owner_action)
    operation_id = plan['operation_id'];key = 'approved-delete:' + operation_id
    with sync._lock:
        previous = sync._get(key)
        if previous and previous.get('plan') != plan:
            raise ValueError('deletion operation ID was reused with a different plan')
        if previous and previous.get('state')=='done' and not (previous.get('operation') and _held_operation(sync,operation_id)):
            return previous['result']
        op,payload,path,proof,expected_receipt,receipt=_native_receipt(sync,plan)
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        operation = _operation(sync, owner, plan, previous if receipt else None)
        if receipt:
            from apple_sync.contracts import delete_receipt
            # Existing manager claim recovery expects the same target-bound receipt marker.
            sync._set('operation-receipt:' + operation_id, delete_receipt(operation, kind))
        elif operation['state'] != 'pending':
            raise ValueError('Apple 删除结果未知，请核对；不会重复提交')
        started = False
        claim_acquired = False
        journal = {'plan':plan, 'operation':operation, 'mappings':(previous or {}).get('mappings', [])}
        try:
            with _operation_claim(sync, owner, kind, operation, completed_delete_receipt=bool(receipt)) as claim:
                claim_acquired = True
                if previous and previous.get('state')=='done':
                    return previous['result']
                local_key = 'todo_id' if kind == 'reminders' else 'alcor_id'
                group_key = 'list_id' if kind == 'reminders' else 'calendar_id'
                maps = previous['mappings'] if receipt and previous else [row for row in sync._maps() if row[group_key] == plan['native']['group_id']]
                journal = {'plan':plan, 'operation':operation, 'mappings':maps}
                if not receipt:
                    _dispatch_ready(sync,plan)
                    fresh = prepare(sync, plan['domain'], operation_id, plan['local_plan'])
                    if fresh != plan:
                        raise ValueError('Apple 删除目标或权限已变化，请重新预览确认')
                    if authorize(operation_id, fingerprint, plan['local_plan']['id']) is not True:
                        raise PermissionError('本次具体删除尚未获得有效会话批准')
                    _dispatch_ready(sync,plan)
                    started = True
                    claim.started('owner_claiming')
                    try:
                        owner_action('apple_begin_approved_delete', operation_id=operation_id, document=operation['document'], binding=operation['binding'])
                    except (ValueError, KeyError):
                        claim.owner_rejected();started = False;raise
                    claim.started('sending')
                    sync._set(key, {**journal, 'state': 'sending'})
                    try:
                        _dispatch_ready(sync,plan)
                    except Exception as error:
                        _reset_unsent(sync,claim,owner_action,operation);started=False
                        sync._set(key,{**journal,'state':'not_sent','error':str(error)})
                        raise
                    try:
                        result = sync.bridge.call(op, **payload)
                    except BridgeError as error:
                        if error.native_no_side_effects:
                            _reset_unsent(sync,claim,owner_action,operation);started=False
                            sync._set(key, {**journal, 'state': 'not_sent', 'error': str(error)})
                        raise
                    if result != {'deleted': True, 'delete_receipt': proof} or not path.is_file() or json.loads(path.read_text()) != expected_receipt:
                        raise ValueError('Apple 删除回执不完整，结果未知；请核对，不会重复提交')
                    with sync._connect() as db:
                        db.execute('BEGIN IMMEDIATE');claim._check(db)
                        from apple_sync.contracts import delete_receipt
                        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('operation-receipt:' + operation_id, json.dumps(delete_receipt(operation, kind))))
                finished = owner_action('apple_finish_approved_delete', operation_id=operation_id)
                if finished.get('state') != 'done':
                    raise ValueError(finished.get('reason', 'Apple 操作后本地版本变化，记录已保留'))
                remaining,remaining_conflict=None,None
                if kind=='calendar' and operation['action'] in ('truncate','exclude'):
                    try:remaining=_remaining_series(sync,owner,operation,maps,claim)
                    except RemainingSeriesChanged as error:remaining_conflict=str(error)
                with sync._connect() as db:
                    db.execute('BEGIN IMMEDIATE');claim._check(db)
                    current = [dict(row) for row in db.execute('SELECT * FROM mappings WHERE '+group_key+'=?', (plan['native']['group_id'],))]
                    if sorted(current, key=lambda row:row[local_key]) != sorted(maps, key=lambda row:row[local_key]):
                        raise ValueError('deletion mappings changed before conditional finalization')
                    if operation['action'] == 'delete':
                        for mapping in maps:
                            if operation['entity'] == 'category' or mapping[local_key] in operation['document'].get('native_range_ids',[operation['local_id']]):
                                sync._save_map({**mapping, 'state': 'deleted', 'conflict': ''}, connection=db)
                        if operation['entity'] == 'category':
                            db.execute('DELETE FROM category_links WHERE apple_id=? AND alcor_id=?', (plan['native']['group_id'], operation['local_id']))
                    if remaining:sync._commit_inbound_snapshot(*remaining,claim=claim,connection=db)
                    if remaining_conflict:
                        mapping=next(row for row in maps if row[local_key]==operation['local_id'])
                        sync._save_map({**mapping,'state':'conflict','conflict':remaining_conflict},connection=db)
                    result = {'state': 'done', 'operation_id': operation_id, 'local_eventkit_completed': True}
                    if remaining_conflict:result['reconciliation_required']=remaining_conflict
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps({**journal, 'state': 'done', 'result': result}, ensure_ascii=False)))
                return result
        except Exception as error:
            if claim_acquired and (started or receipt):
                with sync._connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    saved=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
                    current=json.loads(saved[0]) if saved else {}
                    # A receipt-only finisher may have fenced this older writer and completed.
                    if current.get('state')!='done':
                        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps({**journal,'state':'uncertain','error':str(error)},ensure_ascii=False)))
                owner.apple_operation_error(operation_id, str(error))
            raise


def _unlink_import(sync, plan, authorize, owner_action):
    from apple_sync.group_scan_claims import WorkClaim
    operation_id=plan['operation_id'];key='approved-delete:'+operation_id
    link=plan['link_snapshot'];identity=plan['local_plan']['id'];group=link['apple_id']
    stable=lambda row:{key:value for key,value in dict(row).items() if key!='updated_at'}
    if link['direction']!='import' or not link['enabled'] or link['alcor_id']!=identity:
        raise ValueError('invalid import-only category removal')
    with sync._lock:
        previous=sync._get(key)
        if previous and previous.get('plan')!=plan:raise ValueError('import removal plan changed')
        if previous and previous.get('state')=='done' and not _held_operation(sync,operation_id):return previous['result']
        if previous and previous.get('state') in ('sending','uncertain','done'):
            return _finish_import_receipt(sync,plan,previous)
        group_key='list_id' if plan['kind']=='reminders' else 'calendar_id'
        local_key='todo_id' if plan['kind']=='reminders' else 'alcor_id'
        maps=previous['mappings'] if previous else [row for row in sync._maps() if row[group_key]==group]
        evidence={'kind':'approved_import_unlink','plan':plan,'mappings':maps}
        recovering=bool(previous and previous.get('state') in ('sending','uncertain'))
        with WorkClaim(sync,{group:plan['source_id']},[row[local_key] for row in maps],evidence,
                       recovery=recovering,category_ids=[identity],category_kind=plan['kind']) as claim:
            if authorize(operation_id,plan['plan_fingerprint'],identity) is not True:
                raise PermissionError('停止导入并删除本地分类需要本次具体批准')
            disabled={**link,'enabled':0}
            with sync._connect() as db:
                db.execute('BEGIN IMMEDIATE');claim._check(db)
                current=db.execute('SELECT * FROM category_links WHERE apple_id=?',(group,)).fetchone()
                if not current or stable(current)!=stable(disabled if recovering else link):
                    raise ValueError('import link changed after preview')
                current_maps=[dict(row) for row in db.execute('SELECT * FROM mappings WHERE '+group_key+'=?',(group,))]
                if sorted(current_maps,key=lambda r:r[local_key])!=sorted(maps,key=lambda r:r[local_key]):
                    raise ValueError('import mappings changed after preview')
                db.execute('UPDATE category_links SET enabled=0 WHERE apple_id=? AND alcor_id=?',(group,identity))
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps({'plan':plan,'mappings':maps,'state':'sending'},ensure_ascii=False)))
            if not recovering:claim.started('owner_claiming')
            try:
                result=owner_action('commit_import_category_delete',category_id=identity,operation_id=operation_id,snapshot=plan['local_plan']['snapshot'])
            except (ValueError,KeyError):
                owner=sync.todos if plan['kind']=='reminders' else sync.calendar
                if owner.apple_verify_import_unlink(operation_id,identity,plan['local_plan']['snapshot']) is not None:raise
                if not recovering:
                    with sync._connect() as db:
                        db.execute('BEGIN IMMEDIATE');claim._check(db)
                        current=db.execute('SELECT * FROM category_links WHERE apple_id=?',(group,)).fetchone()
                        if not current or stable(current)!=stable(disabled):raise ValueError('import unlink state changed during owner refusal')
                        db.execute('UPDATE category_links SET enabled=? WHERE apple_id=?',(link['enabled'],group))
                        db.execute('DELETE FROM settings WHERE key=?',(key,))
                    claim.owner_rejected()
                raise
            if result.get('state')!='done' or result.get('targetConflict'):
                raise ValueError('local import removal result needs review')
            with sync._connect() as db:
                db.execute('BEGIN IMMEDIATE');claim._check(db)
                current=db.execute('SELECT * FROM category_links WHERE apple_id=?',(group,)).fetchone()
                if not current or stable(current)!=stable(disabled):raise ValueError('import link changed before finalization')
                current_maps=[dict(row) for row in db.execute('SELECT * FROM mappings WHERE '+group_key+'=?',(group,))]
                if sorted(current_maps,key=lambda r:r[local_key])!=sorted(maps,key=lambda r:r[local_key]):raise ValueError('import mappings changed before finalization')
                db.execute('DELETE FROM mappings WHERE '+group_key+'=?',(group,))
                result={**result,'apple_unchanged':True,'import_stopped':True,'operation_id':operation_id}
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps({'plan':plan,'mappings':maps,'state':'done','result':result},ensure_ascii=False)))
            return result


def _finish_import_receipt(sync,plan,journal):
    """Finish manager metadata only after the owner proves the local transaction committed."""
    from apple_sync.group_scan_claims import WorkClaim
    if journal.get('plan')!=plan or plan.get('mode')!='local-import-unlink':raise ValueError('import receipt plan differs')
    frozen=copy.deepcopy(plan);fingerprint=frozen.pop('plan_fingerprint',None)
    if fingerprint!=digest(frozen):raise ValueError('import receipt plan fingerprint differs')
    owner=sync.todos if plan['kind']=='reminders' else sync.calendar
    identity=plan['local_plan']['id'];group=plan['link_snapshot']['apple_id'];maps=journal['mappings']
    result=owner.apple_verify_import_unlink(plan['operation_id'],identity,plan['local_plan']['snapshot'])
    if not result or result.get('state')!='done' or result.get('targetConflict'):
        raise ValueError('本地停止导入结果未知，原证据已保留；不会重复删除')
    local_key='todo_id' if plan['kind']=='reminders' else 'alcor_id';group_key='list_id' if plan['kind']=='reminders' else 'calendar_id'
    evidence={'kind':'approved_import_unlink','plan':plan,'mappings':maps}
    stable=lambda row:{key:value for key,value in dict(row).items() if key!='updated_at'}
    with WorkClaim(sync,{group:plan['source_id']},[row[local_key] for row in maps],evidence,recovery=True,
                   category_ids=[identity],category_kind=plan['kind'],completed_owner_receipt=True) as claim:
        # A concurrent local recreation must not be accepted as the deleted generation.
        if owner.apple_verify_import_unlink(plan['operation_id'],identity,plan['local_plan']['snapshot'])!=result:
            raise ValueError('import owner receipt changed during cleanup')
        with sync._connect() as db:
            db.execute('BEGIN IMMEDIATE');claim._check(db)
            current=db.execute('SELECT * FROM category_links WHERE apple_id=?',(group,)).fetchone()
            if not current or stable(current)!=stable({**plan['link_snapshot'],'enabled':0}):raise ValueError('import link changed before receipt cleanup')
            current_maps=[dict(row) for row in db.execute('SELECT * FROM mappings WHERE '+group_key+'=?',(group,))]
            expected=[] if journal['state']=='done' else maps
            if sorted(current_maps,key=lambda r:r[local_key])!=sorted(expected,key=lambda r:r[local_key]):raise ValueError('import mappings changed before receipt cleanup')
            row=db.execute('SELECT value FROM settings WHERE key=?',('approved-delete:'+plan['operation_id'],)).fetchone()
            if not row or json.loads(row[0])!=journal:raise ValueError('import journal changed before receipt cleanup')
            db.execute('DELETE FROM mappings WHERE '+group_key+'=?',(group,))
            result={**result,'apple_unchanged':True,'import_stopped':True,'operation_id':plan['operation_id']}
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('approved-delete:'+plan['operation_id'],json.dumps({**journal,'state':'done','result':result},ensure_ascii=False)))
    return result


def completed_import_links(sync):
    """Only finished exact disabled links are exempt from the old-delete warning."""
    stable=lambda row:{key:value for key,value in dict(row).items() if key!='updated_at'}
    links={row['apple_id']:row for row in sync._stored_links()};completed=set()
    with sync._connect() as db:
        for row in db.execute("SELECT value FROM settings WHERE key LIKE 'approved-delete:%'"):
            journal=json.loads(row[0]);plan=journal.get('plan') or {};link=plan.get('link_snapshot') or {}
            if (journal.get('state')=='done' and plan.get('mode')=='local-import-unlink'
                    and link.get('apple_id') in links and stable(links[link['apple_id']])==stable({**link,'enabled':0})):
                completed.add((link['apple_id'],link['alcor_id']))
    return completed
