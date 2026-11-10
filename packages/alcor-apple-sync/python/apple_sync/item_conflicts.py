"""Existing manual conflict choices share normal manager ownership and owner CAS."""
from apple_sync.group_scan_claims import WorkClaim
from apple_sync.contracts import locate_remote, resolve_pending_deletion


def resolve(sync,owner,kind,identity,choice):
    if choice not in ('keep_alcor','keep_apple'):
        raise ValueError('invalid conflict resolution')
    from apple_sync.category_rebuild import guard_retained
    guard_retained(sync,identity)
    from apple_sync import item_restore
    if choice=='keep_apple' and item_restore.finish_pending(sync,owner,kind,identity):
        return sync.status()
    reminder=kind=='reminders';local_key='todo_id' if reminder else 'alcor_id';group='list_id' if reminder else 'calendar_id'
    before=next((row for row in sync._maps() if row[local_key]==identity),None)
    if not before or before['state']!='conflict':raise KeyError('conflict not found')
    capture=owner.apple_capture_reminder_sync if reminder else owner.apple_capture_calendar_sync
    local=sync._local(identity);initial=capture(identity) if local else None
    missing=owner.apple_capture_reminder_missing(identity) if reminder and local else None
    remote=locate_remote(sync,before,kind)
    if choice=='keep_apple' and local is None and remote is not None:
        plan=item_restore.prepare(sync,owner,kind,identity)
        if plan.get('mode')=='receipt_finish':
            return item_restore.finish_prepared(sync,owner,kind,plan['plan'])
        return item_restore.run(sync,owner,kind,plan,lambda:True)
    if not reminder and remote is None:
        observed=sync._get('calendar-inbound-conflict:'+identity) or {}
        if (observed.get('remote') or {}).get('absence_evidence')=='bounded_lookup_null':
            raise ValueError('无法确认 Apple 日程已删除或移动；请先核对来源与关联，本地记录已保留')
    inventory=sync.bridge.call('reminder_lists' if reminder else 'calendars')
    groups={row['id']:row for row in inventory['lists' if reminder else 'calendars']}
    links=sync._stored_links()
    target=next((row['apple_id'] for row in links if local and row['alcor_id']==local.get('category_id')),before[group])
    involved={before[group],target}|({remote[group]} if remote else set())
    sources={key:groups[key]['source_id'] for key in involved if key in groups and groups[key].get('source_id') in inventory.get('source_ids',[])}
    if set(sources)!=involved:raise ValueError('冲突来源不可用或身份不完整，已保留内容')
    if remote and remote.get('source_id')!=sources[remote[group]]:raise ValueError('冲突项目来源不一致，已保留内容')
    evidence={'kind':'manual_item_conflict','choice':choice,'mapping':before,'owner':initial,'native':remote}
    with WorkClaim(sync,sources,[identity],evidence) as claim:
        def owner_commit(action):
            prior_effects=claim.started_effects
            claim.started('owner_committing' if prior_effects else 'owner_claiming')
            try:
                result=action()
            except ValueError as exc:
                if not prior_effects and str(exc).startswith('INBOUND_CHANGED:'):
                    claim.owner_rejected()
                raise
            claim.started('owner_committed')
            return result
        if next((row for row in sync._maps() if row[local_key]==identity),None)!=before or sync._stored_links()!=links:
            raise ValueError('conflict mapping or links changed')
        if sync.bridge.call('reminder_lists' if reminder else 'calendars')!=inventory or locate_remote(sync,before,kind)!=remote:
            raise ValueError('conflict source or native content changed')
        if (capture(identity) if sync._local(identity) else None)!=initial:
            raise ValueError('conflict owner changed before applying choice')
        mapping=dict(before)
        if any(row['entity']=='item' and row['local_id']==identity and row['action']=='delete' for row in owner.apple_operations()):
            claim.started('owner_committing')
            if resolve_pending_deletion(sync,owner,mapping,kind,choice,remote):return sync.status()
            current=capture(identity)
            if not initial or any(current[key]!=initial[key] for key in ('generation','row')):
                raise ValueError('owner changed during deletion cancellation')
            initial=current
            if reminder:missing=owner.apple_capture_reminder_missing(identity)
        if choice=='keep_apple' and remote is None:
            if not reminder:
                raise ValueError('无法确认 Apple 日程已删除或移动；本地内容已保留，请核对来源')
            if local:
                absence=sync._reminder_absence_evidence(before)
                owner_commit(lambda:owner.apple_remove_reminder_inbound(missing['capture'],absence['source_id'],before[group],before['reminder_id'],missing['capture']['generation'],missing['deletion_snapshot']))
            with sync._connect() as db:
                db.execute('BEGIN IMMEDIATE');claim._check(db)
                row=db.execute('SELECT * FROM mappings WHERE todo_id=?',(identity,)).fetchone()
                if not row or dict(row)!=before:raise ValueError('conflict deletion mapping changed')
                sync._save_map({**before,'state':'remote_deleted','conflict':''},connection=db)
            return sync.status()
        if choice=='keep_alcor' and local is None:
            raise ValueError('旧版删除需要先核对来源并重新获得具体删除授权，Apple 内容已保留')
        category=next((row['alcor_id'] for row in links if row['apple_id']==(remote[group] if choice=='keep_apple' else target)),None)
        expected=capture(identity,category) if local else None
        if local and any(expected[key]!=initial[key] for key in ('generation','row','local')):
            raise ValueError('owner changed before conflict commit')
        baseline=sync._reminder_baselines(identity) if reminder else sync._get('item-metadata:'+identity)
        if choice=='keep_alcor':
            from apple_sync.reminders import _supported
            if remote and (not remote.get('writable') or reminder and not _supported(remote)):
                raise PermissionError('Apple 来源只读或包含不支持的内容')
            payload=sync._write_payload(local,before if remote else None,**({'list_id':target,'allow_ambiguous':True} if reminder else {'calendar_id':target}))
            payload['expected_source_id']=sources[target]
            if remote:payload.update(**{'lookup_'+group:remote[group]},expected_snapshot=remote)
            else:payload['expected_destination_snapshot']=groups[target]
            if capture(identity,category)!=expected:raise ValueError('owner changed before native conflict write')
            claim.started('sending')
            remote=sync.bridge.call('reminder_upsert' if reminder else 'upsert',**payload)['reminder' if reminder else 'event']
            sync._set('manual-conflict-receipt:'+identity,{'evidence':evidence,'saved':remote})
        from apple_sync.reminders import _payload as reminder_payload
        from apple_sync.calendar import _payload as calendar_payload
        if local is None:
            raise ValueError('missing local item requires its conditional restore plan')
        else:
            if reminder:
                previous=(expected['local'].get('apple') or {}).get('native') or {}
                if previous.get('list_id')!=remote['list_id']:
                    applied=owner_commit(lambda:owner.apple_apply_reminder_move(expected,reminder_payload(remote),remote,previous))
                    sync._commit_reminder_snapshot(mapping,before,baseline,applied,remote,expected['generation'],move_from={'generation':expected['generation'],'source_id':previous.get('source_id'),'group_id':previous.get('list_id')},claim=claim)
                else:
                    applied=owner_commit(lambda:owner.apple_apply_reminder_inbound(expected,reminder_payload(remote),remote))
                    sync._commit_reminder_snapshot(mapping,before,baseline,applied,remote,expected['generation'],claim=claim)
            else:
                applied=owner_commit(lambda:owner.apple_apply_calendar_inbound(expected,sync._series_payload(local,remote,category),remote))
                sync._commit_inbound_snapshot(mapping,before,baseline,applied,remote,expected['generation'],claim=claim)
        return sync.status()
