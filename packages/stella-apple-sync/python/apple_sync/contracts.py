"""Shared capability and durable-operation boundary for the native Apple peer."""

def category_binding(apple):
    mutable = not apple['immutable'] if 'immutable' in apple else bool(apple.get('writable'))
    return {'apple_id':apple['id'],'source_id':apple.get('source_id'),'source_name':apple.get('source',''),
            'can_modify':bool(apple.get('writable')),'can_rename':mutable,'can_delete':mutable,
            'title':apple.get('title'),'default':apple.get('default',False)}


def bind_item(service, local, remote, kind, issues=None):
    service.apple_bind('item',local['id'],{'apple_id':remote['reminder_id' if kind=='reminders' else 'event_id'],
        'group_id':remote['list_id' if kind=='reminders' else 'calendar_id'],
        'can_delete':bool(remote.get('writable')),'can_modify':bool(remote.get('writable')) and not issues,'issues':issues or [],
        'native':remote,'series_key':remote.get('external_id'),'floating':remote.get('floating',False)})


def process_operations(sync, service, kind):
    from datetime import datetime,timedelta,timezone
    from zoneinfo import ZoneInfo
    import json
    reminder = kind=='reminders'
    from apple_sync.reminders import _payload as todo_payload
    deleted_categories,errors = 0,[]
    statistics = {'deleted_remote':0,'exported':0}
    for operation in service.apple_operations():
        binding = operation.get('binding') or {}
        if not binding.get('apple_id'):
            service.apple_operation_error(operation['id'],'待处理操作缺少 Apple 身份，请重新关联来源')
            errors.append('待处理操作缺少 Apple 身份，请重新关联来源')
            continue
        try:
            action,document = operation['action'],operation['document']
            payload = {'list_id' if reminder else 'calendar_id':binding.get('group_id',binding['apple_id']),
                       'reminder_id' if reminder else 'event_id':binding['apple_id']}
            native = binding.get('native',{})
            payload['external_id'] = native.get('external_id','')
            if action=='delete':
                if not binding.get('can_delete',binding.get('can_modify',False)):
                    raise PermissionError('Apple 来源不允许删除；本地记录已保留')
                if operation['entity']=='category':
                    op = 'reminder_list_delete' if reminder else 'delete_calendar'
                    payload = {'list_id' if reminder else 'calendar_id':binding['apple_id']}
                else:
                    op = 'reminder_delete' if reminder else 'delete'
                    if not reminder:
                        payload['occurrence_date'] = native.get('occurrence_date','')
                        local = sync._local(operation['local_id']) or {}
                        payload['series'] = document.get('scope',local.get('edit_scope','future' if local.get('repeat_frequency','none')!='none' else 'this'))=='future'
                if operation['entity']=='item' and native:
                    current = locate_remote(sync,next(row for row in sync._maps() if row['todo_id' if reminder else 'stella_id']==operation['local_id']),kind)
                    if current:
                        from apple_sync.calendar import _fingerprint
                        from apple_sync.reminders import _hash
                        from apple_sync.metadata import metadata
                        altered = (_hash(current,remote=True)!=_hash(native,remote=True) if reminder else _fingerprint(current,series=True,remote=True)!=_fingerprint(native,series=True,remote=True)) or metadata(current,True)!=metadata(native,True)
                        if altered:
                            mapping = next(row for row in sync._maps() if row['todo_id' if reminder else 'stella_id']==operation['local_id'])
                            sync._conflict(mapping,'Apple 在待删除期间更改了项目，请选择继续删除或采用 Apple 内容')
                            raise ValueError('Apple 内容已变化，删除已暂停；两侧数据均保留')
                sync.bridge.call(op,**payload)
                if operation['entity']=='category':
                    deleted_categories += 1
                else:
                    statistics['deleted_remote'] += 1
                    for mapping in sync._maps():
                        if mapping['todo_id' if reminder else 'stella_id']==operation['local_id']:
                            mapping.update(state='deleted',conflict='')
                            sync._save_map(mapping)
            elif action=='truncate' and not reminder:
                local=service.get_event(operation['local_id'])
                zone=ZoneInfo(native.get('timezone') if local.get('floating') else local['repeat_timezone'] or 'UTC')
                day=datetime.fromisoformat(document['occurrence_on']).date()
                original=datetime.fromisoformat((native['start_at'] if local.get('floating') else local['start_at']).replace('Z','+00:00')).astimezone(zone)
                target=datetime.combine(day,original.timetz(),zone)
                key='operation-receipt:'+operation['id']
                if not sync._get(key):
                    sync.bridge.call('delete',**payload,occurrence_date=target.isoformat(),scan_start=(target-timedelta(days=2)).isoformat(),scan_end=(target+timedelta(days=2)).isoformat(),series=True,require_match=True)
                    sync._set(key,True)
                service.apple_finish_operation(operation['id'])
                mapping=next(row for row in sync._maps() if row['stella_id']==local['id'])
                remote=locate_remote(sync,mapping,'calendar')
                if remote:
                    sync._refresh_map(mapping,service.get_event(local['id']),remote)
                continue
            elif action=='edit' and not reminder:
                from apple_sync.calendar import _payload,_source_key
                local = service.get_event(operation['local_id'])
                zone = ZoneInfo(local['repeat_timezone'] or 'UTC')
                day = datetime.fromisoformat(document['occurrence_on']).date()
                lower = datetime.combine(day,datetime.min.time(),zone)
                instance = next(row for row in service.list_events(lower.isoformat(),(lower+timedelta(days=1)).isoformat()) if row.get('series_id')==local['id'])
                fields = {**instance,**document['patch'],'id':local['id']}
                source=service.get_category(local['category_id']).get('apple') or {}
                if not source.get('can_modify'):
                    raise PermissionError('原日历已不可写，操作已暂停，记录已保留')
                target=service.get_category(fields['category_id']).get('apple') or {}
                if not target.get('apple_id') or not target.get('can_modify'):
                    raise ValueError('目标日历尚未连接可写 Apple 来源，请同步分类后重试')
                data = sync._write_payload(fields,None,calendar_id=target['apple_id'])
                data['lookup_calendar_id']=binding['group_id']
                occurrence = instance['start_at']
                if local.get('floating'):
                    wall = datetime.fromisoformat(occurrence.replace('Z','+00:00')).astimezone(ZoneInfo(local['repeat_timezone']))
                    occurrence = wall.replace(tzinfo=ZoneInfo(native.get('timezone') or local['repeat_timezone'])).isoformat()
                data.update(event_id=binding['apple_id'],external_id=native.get('external_id',''),occurrence_date=occurrence,series=document['scope']=='future',
                            scan_start=(lower-timedelta(days=2)).isoformat(),scan_end=(lower+timedelta(days=2)).isoformat())
                if any(key in document['patch'] and document['patch'][key]!=(json.loads(local[key]) if key=='repeat_days' and isinstance(local[key],str) else local.get(key)) for key in ('repeat_frequency','repeat_days','repeat_until')):
                    data['set_recurrence'] = True
                    data.pop('native_recurrence',None)
                if document['scope']=='this':
                    data.pop('native_recurrence',None)
                    data.pop('repeat_frequency',None)
                data['receipt_file'] = str(sync.db_path.parent/'native-receipts'/(operation['id']+'.json'))
                remote = sync.bridge.call('upsert',**data)['event']
                first_day = datetime.fromisoformat(local['start_at'].replace('Z','+00:00')).astimezone(zone).date()
                if document['scope']=='future' and day==first_day:
                    local = service.apple_apply_item(local['id'],{**document['patch'],'repeat_frequency':fields['repeat_frequency']})
                    mapping = next(row for row in sync._maps() if row['stella_id']==local['id'])
                    sync._refresh_map(mapping,local,remote)
                else:
                    if document['scope']=='this':
                        service.apple_exclude(local['id'],day.isoformat())
                    else:
                        service.apple_apply_item(local['id'],{'repeat_until':(day-timedelta(days=1)).isoformat()})
                    inventory = sync.bridge.call('events',calendar_ids=[binding['group_id']],start=(lower-timedelta(days=370)).isoformat(),end=(lower+timedelta(days=370)).isoformat())
                    candidates = [row for row in inventory.get('events',[]) if row.get('external_id')==native.get('external_id') and not row.get('is_detached') and row.get('occurrence_date')!=instance['start_at'] and
                                  (document['scope']=='this' or datetime.fromisoformat(row['start_at'].replace('Z','+00:00'))<lower)]
                    mapping = next(row for row in sync._maps() if row['stella_id']==local['id'])
                    if candidates:
                        sync._refresh_map(mapping,service.get_event(local['id']),sorted(candidates,key=lambda row:row['start_at'])[0])
                    else:
                        mapping.update(state='instance_excluded',conflict='')
                        sync._save_map(mapping)
            elif action=='exclude' and not reminder:
                local = service.get_event(operation['local_id'])
                zone = ZoneInfo(native.get('timezone') if local.get('floating') else local.get('repeat_timezone') or 'UTC')
                anchor = datetime.fromisoformat((native['start_at'] if local.get('floating') else local['start_at']).replace('Z','+00:00')).astimezone(zone)
                target = datetime.combine(datetime.fromisoformat(document['occurrence_on']).date(),anchor.timetz(),zone)
                payload.update(occurrence_date=target.isoformat(),scan_start=(target-timedelta(days=2)).isoformat(),
                               scan_end=(target+timedelta(days=2)).isoformat(),series=False,require_match=True)
                # Recover after an acknowledged native delete even if the owner ACK was interrupted.
                key = 'operation-receipt:'+operation['id']
                if not sync._get(key):
                    sync.bridge.call('delete',**payload)
                    sync._set(key,True)
                for mapping in sync._maps():
                    if mapping['stella_id']==local['id'] and mapping['occurrence_date'][:10]==target.astimezone(timezone.utc).date().isoformat():
                        rows = sync.bridge.call('events',calendar_ids=[payload['calendar_id']],start=(target-timedelta(days=2)).isoformat(),end=(target+timedelta(days=370)).isoformat())
                        found = sorted([row for row in rows.get('events',[]) if row.get('external_id')==mapping['external_id'] and row.get('occurrence_date')!=mapping['occurrence_date']],key=lambda row:row['start_at'])
                        if found:
                            sync._refresh_map(mapping,local,found[0])
                        else:
                            mapping.update(state='instance_excluded',conflict='')
                            sync._save_map(mapping)
            elif action=='complete' and reminder:
                key = 'operation-receipt:'+operation['id']
                completed = sync._get(key)
                if not completed:
                    before_inventory = sync.bridge.call('reminders',list_ids=[payload['list_id']])
                    sync._set('completion-inventory:'+operation['id'],[row['reminder_id'] for row in before_inventory.get('reminders',[])])
                    completed = sync.bridge.call('reminder_complete',**payload,**document)['reminder']
                    sync._set(key,completed)
                remote = completed
                recurring = (native.get('recurrence') or {}).get('frequency','none')!='none'
                if recurring and document.get('completed'):
                    inventory = sync.bridge.call('reminders',list_ids=[payload['list_id']])
                    if payload['list_id'] not in inventory.get('successful_list_ids',[]):
                        raise ValueError('等待 Apple 返回下一条重复提醒；当前完成记录已保存')
                    candidates = [row for row in inventory['reminders'] if row['list_id']==payload['list_id'] and not row.get('completed') and row.get('title')==native.get('title') and row.get('notes','')==native.get('notes','') and row.get('recurrence')==native.get('recurrence') and (row.get('due_on') or '')>(native.get('due_on') or '') and (row['reminder_id']==native.get('reminder_id') or row['reminder_id'] not in sync._get('completion-inventory:'+operation['id'],[]))]
                    if len(candidates)!=1:
                        raise ValueError('当前提醒已完成；等待 Apple 明确下一实例，不在本地推算或重复创建')
                    remote = candidates[0]
                    retired = sync._get('retired_reminder_ids',[])
                    if completed['reminder_id']!=remote['reminder_id'] and completed['reminder_id'] not in retired:
                        retired.append(completed['reminder_id'])
                        sync._set('retired_reminder_ids',retired)
                mapping = next(row for row in sync._maps() if row['todo_id']==operation['local_id'])
                previous = {**native,'completed':True,'completed_at':completed.get('completed_at') or sync.clock()} if recurring and document.get('completed') else completed
                local = service.apple_record_completion(operation['local_id'],previous,todo_payload(remote))
                sync._refresh_map(mapping,local,remote)
                statistics['exported'] += 1
            else:
                raise ValueError('此 Apple 操作尚未支持，请取消后重试')
            service.apple_finish_operation(operation['id'])
        except Exception as error:
            service.apple_operation_error(operation['id'],str(error))
            errors.append(str(error))
    sync._set('operation_counts',statistics)
    return deleted_categories,errors


def locate_remote(sync,mapping,kind):
    from datetime import datetime,timedelta
    reminder = kind=='reminders'
    group,identity,singular = ('list_id','reminder_id','reminder') if reminder else ('calendar_id','event_id','event')
    lookup = {key:mapping[key] for key in (group,identity,'external_id')}
    if not reminder:
        lookup['occurrence_date']=mapping['occurrence_date']
    found = sync.bridge.call('reminder_lookup' if reminder else 'lookup',**lookup).get(singular)
    if found:
        return found
    ids = [row['id'] for row in sync.bridge.call('reminder_lists' if reminder else 'calendars')['lists' if reminder else 'calendars']]
    if not ids:
        return None
    if reminder:
        inventory=sync.bridge.call('reminders',list_ids=ids)
    else:
        anchor=datetime.fromisoformat(mapping['occurrence_date'].replace('Z','+00:00'))
        inventory=sync.bridge.call('events',calendar_ids=ids,start=(anchor-timedelta(days=2)).isoformat(),end=(anchor+timedelta(days=2)).isoformat())
    failed=inventory.get('failed_list_ids' if reminder else 'failed_calendar_ids',[])
    if failed:
        raise ValueError('来源读取不完整，暂不判定删除或移动')
    matches=[row for row in inventory[singular+'s'] if row.get('external_id') and row['external_id']==mapping['external_id'] and
             (reminder or row.get('occurrence_date')==mapping['occurrence_date'] or row.get('event_id')==mapping['event_id'])]
    if len(matches)>1:
        raise ValueError('存在多个相同身份的 Apple 项目，请先在 Apple 中确认目标')
    return matches[0] if matches else None


def resolve_pending_deletion(sync,owner,mapping,kind,choice,remote):
    reminder = kind=='reminders'
    identity = mapping['todo_id' if reminder else 'stella_id']
    pending = [row for row in owner.apple_operations() if row['entity']=='item' and row['local_id']==identity and row['action']=='delete']
    if not pending:
        return False
    if choice=='keep_apple':
        for operation in pending:
            owner.cancel_apple_operation(operation['id'])
        return False
    if remote:
        if not remote.get('writable'):
            raise PermissionError('Apple 来源不允许删除')
        payload = {key:remote[key] for key in (('list_id','reminder_id','external_id') if reminder else ('calendar_id','event_id','external_id','occurrence_date'))}
        if not reminder:
            local=sync._local(identity) or {}
            payload['series']=pending[0]['document'].get('scope','future' if local.get('repeat_frequency','none')!='none' else 'this')=='future'
        sync.bridge.call('reminder_delete' if reminder else 'delete',**payload)
    for operation in pending:
        owner.apple_finish_operation(operation['id'])
    mapping.update(state='deleted',conflict='')
    sync._save_map(mapping)
    return True
