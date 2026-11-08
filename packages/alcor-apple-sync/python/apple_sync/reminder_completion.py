"""One queued reminder completion: common claim, exact owner CAS, no unknown resend."""
import json
from apple_sync.group_scan_claims import WorkClaim

PREFIX='reminder-completion:'


def evidence(operation):
    return {key:operation[key] for key in ('id','entity','local_id','action','document','binding')}


def operations(sync, pending):
    """A receipt may still need manager finalization after owner committed done."""
    result=list(pending);ids={row['id'] for row in result}
    with sync._connect() as db:
        saved=[json.loads(row[0]) for row in db.execute('SELECT value FROM settings WHERE key LIKE ?',(PREFIX+'%',))]
    for entry in saved:
        if entry.get('stage')=='receipt' and entry['operation']['id'] not in ids:
            result.append({**entry['operation'],'state':'uncertain'})
    return result


def _save(sync, old, new):
    key=PREFIX+new['operation']['id']
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        if (json.loads(row[0]) if row else None)!=old:
            raise ValueError('reminder completion journal changed; review required')
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps(new,ensure_ascii=False)))
    return new


def process(sync, service, operation):
    from apple_sync.reminders import _payload, _hash
    from apple_sync.metadata import metadata
    approved=evidence(operation);binding=approved['binding'];group=binding.get('group_id');source=binding.get('source_id')
    if not source:
        raise ValueError('完成操作缺少已核验来源；请取消尚未发送的操作，核对原提醒后重试')
    journal=sync._get(PREFIX+operation['id'])
    recover=bool(journal and journal.get('stage')=='receipt' and journal.get('operation')==approved)
    if journal and not recover:
        raise ValueError('提醒完成结果待核对，已保留内容且不会重复发送')
    if operation.get('state') in ('sending','uncertain') and not recover:
        raise ValueError('提醒完成结果不确定，缺少可验证回执；请核对 Apple')
    with WorkClaim(sync,{group:source},[operation['local_id']],approved,recovery=recover) as claim:
        if not recover:
            mapping=next((row for row in sync._maps() if row['todo_id']==operation['local_id']),None)
            if not mapping or mapping['list_id']!=group or mapping['reminder_id']!=binding['apple_id']:
                raise ValueError('reminder completion mapping changed')
            capture=service.apple_capture_reminder_completion(operation['local_id'],operation)
            current=sync.bridge.call('reminder_lookup',list_id=group,reminder_id=binding['apple_id'])['reminder']
            native=binding.get('native') or {}
            if (not current or current.get('source_id')!=source or current.get('list_id')!=group or
                    current.get('reminder_id')!=binding['apple_id'] or not current.get('writable') or
                    _hash(current,remote=True)!=_hash(native,remote=True) or metadata(current,True)!=metadata(native,True)):
                raise ValueError('reminder completion native target changed; review required')
            prepared={'version':1,'stage':'prepared','operation':approved,'capture':capture,
                      'mapping':mapping,'baselines':sync._reminder_baselines(operation['local_id'])}
            # Owner capture + exact operation are checked in one owner transaction.
            claim.started('owner_claiming')
            try:service.apple_begin_reminder_completion(capture,operation)
            except (ValueError,KeyError):
                claim.owner_rejected();raise
            journal=_save(sync,None,prepared)
            claim.started()
            try:
                response=sync.bridge.call('reminder_complete',list_id=group,reminder_id=binding['apple_id'],
                    expected_source_id=source,require_exact_identity=True,expected_snapshot=current,**operation['document'])
                completed=response.get('completed_snapshot') or response['reminder']
            except Exception as exc:
                from apple_sync.bridge import BridgeError
                if isinstance(exc,BridgeError) and exc.native_no_side_effects:
                    service.apple_reject_reminder_completion(capture,operation,str(exc))
                    with sync._connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        row=db.execute('SELECT value FROM settings WHERE key=?',(PREFIX+operation['id'],)).fetchone()
                        if not row or json.loads(row[0])!=journal:raise ValueError('completion rejection journal changed')
                        db.execute('DELETE FROM settings WHERE key=?',(PREFIX+operation['id'],))
                    claim.native_rejected()
                raise
            if (completed.get('reminder_id')!=binding['apple_id'] or completed.get('list_id')!=group or
                    completed.get('source_id')!=source or completed.get('completed')!=operation['document'].get('completed')):
                raise ValueError('reminder completion response identity cannot be verified')
            journal=_save(sync,journal,{**journal,'stage':'receipt','completed':completed})
        completed=journal['completed'];remote=journal.get('remote')
        recurring=(binding.get('native',{}).get('recurrence') or {}).get('frequency','none')!='none'
        if remote is None:
            remote=completed
            if recurring and approved['document'].get('completed'):
                inventory=sync.bridge.call('reminders',list_ids=[group])
                if group not in inventory.get('successful_list_ids',[]):
                    raise ValueError('当前提醒已完成，等待完整原生库存确认下一实例')
                external=completed.get('external_id')
                candidates=[row for row in inventory.get('reminders',[]) if external and row.get('external_id')==external and
                            row.get('list_id')==group and row.get('source_id')==source and not row.get('completed') and
                            (row.get('due_on') or '')>(completed.get('due_on') or '')]
                if len(candidates)!=1:
                    raise ValueError('当前提醒已完成；下一实例身份不明确，不猜测或重复创建')
                remote=candidates[0]
            journal=_save(sync,journal,{**journal,'remote':remote})
        previous=completed
        result=service.apple_finalize_reminder_completion(journal['capture'],approved,_payload(remote),remote,
                                                         previous_completion=previous)
        if not result.get('committed') or result.get('operation_id')!=operation['id']:
            raise ValueError('reminder completion owner receipt cannot be verified')
        mapping=dict(journal['mapping'])
        sync._commit_reminder_snapshot(mapping,journal['mapping'],journal['baselines'],result['local'],remote,
                                      journal['capture']['capture']['generation'],completion_journal=journal)
        return True
