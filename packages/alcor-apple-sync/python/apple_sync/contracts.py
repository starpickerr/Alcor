"""Shared capability and durable-operation boundary for the native Apple peer."""

def category_binding(apple):
    mutable = not apple['immutable'] if 'immutable' in apple else bool(apple.get('writable'))
    return {'apple_id':apple['id'],'source_id':apple.get('source_id'),'source_name':apple.get('source',''),
            'can_modify':bool(apple.get('writable')),'can_rename':mutable,'can_delete':mutable,
            'title':apple.get('title'),'default':apple.get('default',False)}


def bind_item(service, local, remote, kind, issues=None):
    service.apple_bind('item',local['id'],{'apple_id':remote['reminder_id' if kind=='reminders' else 'event_id'],
        'group_id':remote['list_id' if kind=='reminders' else 'calendar_id'], 'source_id':remote.get('source_id'),
        'can_delete':bool(remote.get('writable')),'can_modify':bool(remote.get('writable')) and not issues,'issues':issues or [],
        'native':remote,'series_key':remote.get('external_id'),'floating':remote.get('floating',False)})


def delete_receipt(operation, kind):
    """Bind a native-delete receipt to the precise queued operation and destination."""
    import hashlib
    import json
    binding = operation.get('binding') or {}
    identity = {'version': 1, 'kind': kind, 'id': operation['id'], 'entity': operation['entity'],
                'local_id': operation['local_id'], 'action': operation['action'],
                'document': operation['document'], 'apple_id': binding.get('apple_id'),
                'group_id': binding.get('group_id'), 'source_id': binding.get('source_id')}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return {'version': 1, 'kind': 'native_delete', 'digest': digest}


def recurring_delete_receipt(operation, kind, occurrence):
    import hashlib
    import json
    identity = {key: occurrence.get(key) for key in ('event_id', 'calendar_id', 'source_id', 'occurrence_date')}
    identity['snapshot_hash'] = hashlib.sha256(json.dumps(occurrence, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return recurring_receipt_for_identity(operation, kind, identity)


def recurring_receipt_for_identity(operation, kind, identity):
    import hashlib
    import json
    value = {'operation': delete_receipt(operation, kind), 'target': identity}
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return {'version': 2, 'kind': 'native_recurrence_delete', 'target': identity, 'digest': digest}


def valid_recurring_receipt(operation, kind, target, receipt):
    from datetime import datetime
    if not isinstance(receipt, dict) or not isinstance(receipt.get('target'), dict):
        return False
    identity = receipt['target']
    expected = operation['document'].get('_expected_target') or {}
    try:
        occurrence = datetime.fromisoformat(identity['occurrence_date'].replace('Z', '+00:00'))
        return (bool(identity.get('event_id')) and identity.get('calendar_id') == expected.get('group_id')
                and identity.get('source_id') == expected.get('source_id')
                and isinstance(identity.get('snapshot_hash'), str) and len(identity['snapshot_hash']) == 64
                and abs((occurrence-target).total_seconds()) < 1
                and receipt == recurring_receipt_for_identity(operation, kind, identity))
    except (KeyError, TypeError, ValueError):
        return False


def recurring_delete_target(sync, operation, target):
    """Check the queued series and resolve exactly one occurrence before dispatch."""
    from datetime import datetime, timedelta
    from apple_sync.calendar import _fingerprint
    from apple_sync.metadata import metadata
    binding, document = operation.get('binding') or {}, operation['document']
    expected = document.get('_expected_target')
    if (not isinstance(expected, dict) or not expected.get('source_id') or not expected.get('apple_id')
            or not expected.get('group_id') or not isinstance(expected.get('native'), dict)
            or any(expected.get(key) != binding.get(key) for key in ('source_id', 'apple_id', 'group_id'))
            or expected['native'] != binding.get('native')):
        raise ValueError('Apple 日程来源或原始快照缺失或已变化，请重新确认')
    if not binding.get('can_delete', binding.get('can_modify', False)):
        raise PermissionError('Apple 来源不允许删除；本地记录已保留')
    native = expected['native']
    external = native.get('external_id')
    if not external:
        raise ValueError('Apple 重复日程缺少系列身份，请重新确认')
    mapping = next(row for row in sync._maps() if row['alcor_id'] == operation['local_id'])
    current = locate_remote(sync, mapping, 'calendar')
    if (not current or current.get('event_id') != expected['apple_id']
            or current.get('calendar_id') != expected['group_id'] or current.get('source_id') != expected['source_id']
            or _fingerprint(current, series=True, remote=True) != _fingerprint(native, series=True, remote=True)
            or metadata(current, True) != metadata(native, True)):
        sync._conflict(mapping, 'Apple 重复日程来源或内容已变化，请重新确认')
        raise ValueError('Apple 重复日程来源或内容已变化，删除已暂停')
    inventory = sync.bridge.call('events', calendar_ids=[expected['group_id']],
                                 start=(target-timedelta(days=2)).isoformat(), end=(target+timedelta(days=2)).isoformat())
    if expected['group_id'] not in inventory.get('successful_calendar_ids', []):
        raise ValueError('无法完整读取 Apple 日程实例，请重新确认')
    def instant(value):
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    candidates = [row for row in inventory.get('events', []) if row.get('calendar_id') == expected['group_id']
                  and row.get('external_id') == external and row.get('occurrence_date')
                  and abs((instant(row['occurrence_date']) - target).total_seconds()) < 1]
    if len(candidates) != 1 or not candidates[0].get('event_id') or candidates[0].get('source_id') != expected['source_id']:
        raise ValueError('Apple 日程实例缺失、重复或来源变化，删除已暂停')
    occurrence = candidates[0]
    # Instance exceptions with different content require a fresh explicit decision.
    comparable = {**occurrence, 'start_at': native['start_at'], 'end_at': native['end_at']}
    if ((instant(occurrence['end_at'])-instant(occurrence['start_at'])) != (instant(native['end_at'])-instant(native['start_at']))
            or _fingerprint(comparable, series=True, remote=True) != _fingerprint(native, series=True, remote=True)
            or metadata(occurrence, True) != metadata(native, True)):
        raise ValueError('Apple 日程实例内容已变化，删除已暂停')
    return occurrence


def native_delete_preview(sync, service, kind, entity, local_id, scope=None, occurrence_on=None):
    """Read one concrete Apple target; callers must bind the returned snapshot to approval."""
    import hashlib
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    reminder = kind == 'reminders'
    if kind not in ('calendar', 'reminders') or entity not in ('item', 'category') or not isinstance(local_id, str) or not local_id:
        raise ValueError('Apple 删除目标无效')
    if (scope not in (None, 'item', 'category', 'this', 'future')
            or scope == 'category' and entity != 'category' or scope == 'item' and entity != 'item'
            or scope in ('this', 'future') and (entity != 'item' or reminder)
            or (occurrence_on is not None and (reminder or entity != 'item'))):
        raise ValueError('Apple 删除范围无效')
    local = service.get_category(local_id) if entity == 'category' else service.get_todo(local_id) if reminder else service.get_event(local_id)
    binding = local.get('apple') or {}
    source, series = binding.get('source_id'), binding.get('apple_id')
    group = series if entity == 'category' else binding.get('group_id')
    if not source or not series or not group or not binding.get('can_delete', binding.get('can_modify', False)):
        raise ValueError('Apple 目标来源、身份或删除权限无法确认')
    if entity == 'category':
        inventory = sync.bridge.call('reminder_lists' if reminder else 'calendars')
        matches = [row for row in inventory.get('lists' if reminder else 'calendars', []) if row.get('id') == series and row.get('source_id') == source]
        if len(matches) != 1 or not category_binding(matches[0])['can_delete']:
            raise ValueError('Apple 分类来源或删除权限已变化')
        remote = matches[0]
        instance = series
    else:
        mapping = next((row for row in sync._maps() if row['todo_id' if reminder else 'alcor_id'] == local_id), None)
        if not mapping or not isinstance(binding.get('native'), dict):
            raise ValueError('Apple 目标缺少可验证的原始关联')
        if occurrence_on is not None:
            day = datetime.fromisoformat(occurrence_on).date()
            if day.isoformat() != occurrence_on:
                raise ValueError('Apple 实例日期无效')
            native = binding['native']
            zone = ZoneInfo(native.get('timezone') if local.get('floating') else local.get('repeat_timezone') or 'UTC')
            original = datetime.fromisoformat((native['start_at'] if local.get('floating') else local['start_at']).replace('Z', '+00:00')).astimezone(zone)
            target = datetime.combine(day, original.timetz(), zone)
            remote = recurring_delete_target(sync, {'local_id': local_id, 'binding': binding, 'document': {'_expected_target': {'apple_id':series,'group_id':group,'source_id':source,'native':native}}}, target)
        else:
            remote = locate_remote(sync, mapping, kind)
            identity_key, group_key = ('reminder_id','list_id') if reminder else ('event_id','calendar_id')
            if not remote or remote.get(identity_key) != series or remote.get(group_key) != group or remote.get('source_id') != source:
                raise ValueError('Apple 目标身份或来源已变化')
            from apple_sync.calendar import _fingerprint
            from apple_sync.reminders import _hash
            from apple_sync.metadata import metadata
            native = binding['native']
            if ((_hash(remote,remote=True) != _hash(native,remote=True) if reminder else _fingerprint(remote,series=True,remote=True) != _fingerprint(native,series=True,remote=True))
                    or metadata(remote,True) != metadata(native,True)):
                raise ValueError('Apple 目标内容已变化，请重新确认')
        instance = remote['reminder_id' if reminder else 'event_id']
    fingerprint = hashlib.sha256(json.dumps(remote, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return {'version':1, 'entity':entity, 'source_id':source, 'group_id':group, 'series_apple_id':series,
            'instance_apple_id':instance, 'occurrence_on':occurrence_on, 'scope':scope,
            'native_fingerprint':fingerprint, 'native_snapshot':remote}


def verify_approved_native(operation, current):
    approval = operation['document'].get('_approval')
    if approval is None:
        return None  # Legacy callers are not proof of approval; entry routing must restrict them.
    if not isinstance(approval, dict) or not isinstance(approval.get('native'), dict):
        raise ValueError('Apple 删除批准快照无效')
    native = approval['native']
    if current != native.get('native_snapshot'):
        raise ValueError('Apple 目标在批准后发生变化，请重新确认')
    return current


def finish_operation(service, operation_id):
    result = service.apple_finish_operation(operation_id)
    if not isinstance(result, dict) or result.get('state') != 'done':
        raise ValueError((result or {}).get('reason', 'Apple 操作尚未完成本地核对，记录已保留') if isinstance(result, dict) else 'Apple 操作尚未完成本地核对，记录已保留')


def _operation_claim(sync, service, kind, operation, *, completed_delete_receipt=False):
    from contextlib import nullcontext
    # Other legacy outbox actions do not yet share this deletion protocol.
    if operation['action'] not in ('delete','truncate','exclude'):
        return nullcontext()
    from apple_sync.group_scan_claims import WorkClaim
    binding = operation['binding']
    group = binding['apple_id'] if operation['entity'] == 'category' else binding.get('group_id')
    groups = {group:binding.get('source_id')}
    target_id = (operation['document'].get('patch') or {}).get('category_id')
    if target_id:
        target = service.get_category(target_id).get('apple') or {}
        groups[target.get('apple_id')] = target.get('source_id')
    evidence = {key:operation[key] for key in ('id','entity','local_id','action','document','binding')}
    receipt = sync._get('operation-receipt:'+operation['id'])
    recover = (operation['action'] == 'delete' and receipt == delete_receipt(operation,kind))
    if completed_delete_receipt and receipt == delete_receipt(operation,kind):recover=True
    if operation['action'] in ('truncate','exclude') and isinstance(receipt,dict) and isinstance(receipt.get('target'),dict):
        recover = receipt == recurring_receipt_for_identity(operation,kind,receipt['target'])
    if operation['action'] in ('delete','truncate','exclude') and operation.get('state') in ('sending','uncertain') and not recover:
        raise ValueError('Apple 删除结果不确定，请先核对 Apple 后再处理')
    items=operation['document'].get('native_range_ids',[operation['local_id']]) if operation['entity']=='item' else []
    return WorkClaim(sync,groups,items,evidence,recovery=recover,completed_delete_receipt=bool(completed_delete_receipt and recover))


def _begin_claimed_delete(service, operation, claim):
    claim.started('owner_claiming')
    try:
        service.apple_begin_operation(operation['id'],operation['document'],operation['binding'])
    except (ValueError,KeyError):
        claim.owner_rejected()
        raise
    claim.started()


def process_operations(sync, service, kind):
    from datetime import datetime,timedelta,timezone
    from zoneinfo import ZoneInfo
    import json
    reminder = kind=='reminders'
    from apple_sync.reminders import _payload as todo_payload
    from apple_sync.approved_delete import finish_receipts
    handled,deleted_categories,finished_items,errors=finish_receipts(sync)
    statistics = {'deleted_remote':finished_items,'exported':0}
    pending=[row for row in service.apple_operations() if row['id'] not in handled]
    if reminder:
        from apple_sync import reminder_completion
        pending=reminder_completion.operations(sync,pending)
    for operation in pending:
        if operation['action'] in ('delete', 'truncate', 'exclude'):
            continuation=getattr(sync,'continue_approved_delete',None)
            if operation['state']=='pending' and operation['document'].get('_confirmed_intent') and continuation:
                try:
                    result=continuation(operation['id'])
                    if result and result.get('state')=='done':
                        if operation['entity']=='category':deleted_categories+=1
                        else:statistics['deleted_remote']+=1
                    else:errors.append('此删除尚未具备可续办的具体确认，内容已保留')
                except Exception as error:errors.append(str(error))
            else:errors.append('删除等待本次具体确认或结果核对，内容已保留')
            continue
        binding = operation.get('binding') or {}
        if not binding.get('apple_id'):
            service.apple_operation_error(operation['id'],'待处理操作缺少 Apple 身份，请重新关联来源')
            errors.append('待处理操作缺少 Apple 身份，请重新关联来源')
            continue
        try:
            if reminder and operation['action']=='complete':
                reminder_completion.process(sync,service,operation)
                statistics['exported']+=1
                continue
            with _operation_claim(sync,service,kind,operation) as claim:
                from apple_sync.export_reservations import guard_write, guard_group
                if operation['entity'] == 'item':
                    guard_write(sync, operation['local_id'])
                    guard_group(sync, binding.get('group_id'))
                else:
                    guard_group(sync, binding.get('apple_id'))
                action,document = operation['action'],operation['document']
                receipt_key = 'operation-receipt:' + operation['id']
                if action == 'delete' and sync._get(receipt_key):
                    if sync._get(receipt_key) != delete_receipt(operation, kind):
                        raise ValueError('Apple 删除回执无法验证，已暂停并保留本地记录')
                    finish_operation(service, operation['id'])
                    if operation['entity'] == 'item':
                        for mapping in sync._maps():
                            if mapping['todo_id' if reminder else 'alcor_id'] == operation['local_id']:
                                mapping.update(state='deleted', conflict='')
                                sync._save_map(mapping)
                    else:
                        deleted_categories += 1
                    continue
                if action == 'delete' and operation.get('state') in ('sending', 'uncertain'):
                    raise ValueError('Apple 删除结果不确定，请先核对 Apple 后再处理')
                payload = {'list_id' if reminder else 'calendar_id':binding.get('group_id',binding['apple_id']),
                           'reminder_id' if reminder else 'event_id':binding['apple_id']}
                native = binding.get('native',{})
                payload['external_id'] = native.get('external_id','')
                if action=='delete':
                    expected = document.get('_expected_target')
                    if (not isinstance(expected, dict) or not expected.get('source_id')
                            or not expected.get('apple_id') or expected.get('apple_id') != binding.get('apple_id')
                            or expected.get('group_id') != binding.get('group_id')
                            or expected.get('source_id') != binding.get('source_id')):
                        raise ValueError('Apple 删除来源身份缺失或已变化，请重新确认')
                    if not binding.get('can_delete',binding.get('can_modify',False)):
                        raise PermissionError('Apple 来源不允许删除；本地记录已保留')
                    if operation['entity']=='category':
                        op = 'reminder_list_delete' if reminder else 'delete_calendar'
                        payload = {'list_id' if reminder else 'calendar_id':binding['apple_id']}
                        if document.get('_approval') is not None:
                            inventory = sync.bridge.call('reminder_lists' if reminder else 'calendars')
                            candidates = [row for row in inventory.get('lists' if reminder else 'calendars', []) if row.get('id') == binding['apple_id'] and row.get('source_id') == expected['source_id']]
                            if len(candidates) != 1:
                                raise ValueError('Apple 分类身份或来源已变化，请重新确认')
                            payload['expected_snapshot'] = verify_approved_native(operation, candidates[0])
                    else:
                        op = 'reminder_delete' if reminder else 'delete'
                        payload['require_match'] = True
                        payload['require_exact_identity'] = True
                        if not reminder:
                            payload['occurrence_date'] = native.get('occurrence_date','')
                            local = sync._local(operation['local_id']) or {}
                            payload['series'] = document.get('scope',local.get('edit_scope','future' if local.get('repeat_frequency','none')!='none' else 'this'))=='future'
                    payload['expected_source_id'] = expected['source_id']
                    payload['require_match'] = True
                    if operation['entity']=='item' and native:
                        current = locate_remote(sync,next(row for row in sync._maps() if row['todo_id' if reminder else 'alcor_id']==operation['local_id']),kind)
                        if not current:
                            mapping = next(row for row in sync._maps() if row['todo_id' if reminder else 'alcor_id']==operation['local_id'])
                            sync._conflict(mapping,'无法确认 Apple 删除目标，请检查来源后选择处理方式')
                            raise ValueError('Apple 删除目标缺失或身份无法确认，已暂停并保留本地记录')
                        remote_id = current.get('reminder_id' if reminder else 'event_id')
                        remote_group = current.get('list_id' if reminder else 'calendar_id')
                        expected_group = binding.get('group_id')
                        expected_occurrence = native.get('occurrence_date') if not reminder else None
                        if (remote_id != binding.get('apple_id') or not expected_group or remote_group != expected_group
                                or current.get('source_id') != expected['source_id']
                                or (expected_occurrence and current.get('occurrence_date') != expected_occurrence)):
                            mapping = next(row for row in sync._maps() if row['todo_id' if reminder else 'alcor_id']==operation['local_id'])
                            sync._conflict(mapping,'Apple 删除目标身份或所属来源已变化，请重新确认')
                            raise ValueError('Apple 删除目标身份已变化，已暂停并保留本地记录')
                        from apple_sync.calendar import _fingerprint
                        from apple_sync.reminders import _hash
                        from apple_sync.metadata import metadata
                        altered = (_hash(current,remote=True)!=_hash(native,remote=True) if reminder else _fingerprint(current,series=True,remote=True)!=_fingerprint(native,series=True,remote=True)) or metadata(current,True)!=metadata(native,True)
                        if altered:
                            mapping = next(row for row in sync._maps() if row['todo_id' if reminder else 'alcor_id']==operation['local_id'])
                            sync._conflict(mapping,'Apple 在待删除期间更改了项目，请选择继续删除或采用 Apple 内容')
                            raise ValueError('Apple 内容已变化，删除已暂停；两侧数据均保留')
                        approved = verify_approved_native(operation, current)
                        if approved is not None:
                            payload['expected_snapshot'] = approved
                    elif operation['entity']=='item':
                        raise ValueError('Apple 删除目标缺少原始快照，已暂停并保留本地记录')
                    _begin_claimed_delete(service,operation,claim)
                    result = sync.bridge.call(op,**payload)
                    if not isinstance(result, dict) or result.get('deleted') is not True:
                        raise ValueError('Apple 未确认删除完成，已暂停并保留本地记录')
                    sync._set(receipt_key, delete_receipt(operation, kind))
                elif action=='truncate' and not reminder:
                    local=service.get_event(operation['local_id'])
                    zone=ZoneInfo(native.get('timezone') if local.get('floating') else local['repeat_timezone'] or 'UTC')
                    day=datetime.fromisoformat(document['occurrence_on']).date()
                    original=datetime.fromisoformat((native['start_at'] if local.get('floating') else local['start_at']).replace('Z','+00:00')).astimezone(zone)
                    target=datetime.combine(day,original.timetz(),zone)
                    key='operation-receipt:'+operation['id']
                    receipt = sync._get(key)
                    if receipt and not valid_recurring_receipt(operation, kind, target, receipt):
                        raise ValueError('Apple 删除回执无法验证，已暂停并保留本地记录')
                    if not receipt:
                        if operation.get('state') in ('sending', 'uncertain'):
                            raise ValueError('Apple 删除结果不确定，请先核对 Apple 后再处理')
                        occurrence = recurring_delete_target(sync, operation, target)
                        verify_approved_native(operation, occurrence)
                        payload.update(event_id=occurrence['event_id'], expected_source_id=document['_expected_target']['source_id'], require_exact_identity=True, expected_snapshot=occurrence)
                        _begin_claimed_delete(service,operation,claim)
                        result = sync.bridge.call('delete',**payload,occurrence_date=target.isoformat(),scan_start=(target-timedelta(days=2)).isoformat(),scan_end=(target+timedelta(days=2)).isoformat(),series=True,require_match=True)
                        if not isinstance(result, dict) or result.get('deleted') is not True:
                            raise ValueError('Apple 未确认删除完成，已暂停并保留本地记录')
                        sync._set(key,recurring_delete_receipt(operation, kind, occurrence))
                    finish_operation(service, operation['id'])
                    mapping=next(row for row in sync._maps() if row['alcor_id']==local['id'])
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
                    # Legacy marker URLs are hidden by the bridge; an absent baseline is not an explicit clear.
                    if 'url' not in document['patch'] and not sync._get('item-metadata:'+local['id']) and data.get('url') == '':
                        data.pop('url')
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
                        mapping = next(row for row in sync._maps() if row['alcor_id']==local['id'])
                        sync._refresh_map(mapping,local,remote)
                    else:
                        if document['scope']=='this':
                            service.apple_exclude(local['id'],day.isoformat())
                        else:
                            service.apple_apply_item(local['id'],{'repeat_until':(day-timedelta(days=1)).isoformat()})
                        inventory = sync.bridge.call('events',calendar_ids=[binding['group_id']],start=(lower-timedelta(days=370)).isoformat(),end=(lower+timedelta(days=370)).isoformat())
                        candidates = [row for row in inventory.get('events',[]) if row.get('external_id')==native.get('external_id') and not row.get('is_detached') and row.get('occurrence_date')!=instance['start_at'] and
                                      (document['scope']=='this' or datetime.fromisoformat(row['start_at'].replace('Z','+00:00'))<lower)]
                        mapping = next(row for row in sync._maps() if row['alcor_id']==local['id'])
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
                    receipt = sync._get(key)
                    if receipt and not valid_recurring_receipt(operation, kind, target, receipt):
                        raise ValueError('Apple 删除回执无法验证，已暂停并保留本地记录')
                    if not receipt:
                        if operation.get('state') in ('sending', 'uncertain'):
                            raise ValueError('Apple 删除结果不确定，请先核对 Apple 后再处理')
                        occurrence = recurring_delete_target(sync, operation, target)
                        verify_approved_native(operation, occurrence)
                        payload.update(event_id=occurrence['event_id'], expected_source_id=document['_expected_target']['source_id'], require_exact_identity=True, expected_snapshot=occurrence)
                        _begin_claimed_delete(service,operation,claim)
                        result = sync.bridge.call('delete',**payload)
                        if not isinstance(result, dict) or result.get('deleted') is not True:
                            raise ValueError('Apple 未确认删除完成，已暂停并保留本地记录')
                        sync._set(key,recurring_delete_receipt(operation, kind, occurrence))
                    for mapping in sync._maps():
                        if mapping['alcor_id']==local['id'] and mapping['occurrence_date'][:10]==target.astimezone(timezone.utc).date().isoformat():
                            rows = sync.bridge.call('events',calendar_ids=[payload['calendar_id']],start=(target-timedelta(days=2)).isoformat(),end=(target+timedelta(days=370)).isoformat())
                            found = sorted([row for row in rows.get('events',[]) if row.get('external_id')==mapping['external_id'] and row.get('occurrence_date')!=mapping['occurrence_date']],key=lambda row:row['start_at'])
                            if found:
                                sync._refresh_map(mapping,local,found[0])
                            else:
                                mapping.update(state='instance_excluded',conflict='')
                                sync._save_map(mapping)
                else:
                    raise ValueError('此 Apple 操作尚未支持，请取消后重试')
                finish_operation(service, operation['id'])
                if action == 'delete':
                    if operation['entity'] == 'category':
                        deleted_categories += 1
                    else:
                        statistics['deleted_remote'] += 1
                        for mapping in sync._maps():
                            if mapping['todo_id' if reminder else 'alcor_id'] == operation['local_id']:
                                mapping.update(state='deleted', conflict='')
                                sync._save_map(mapping)
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
    identity = mapping['todo_id' if reminder else 'alcor_id']
    pending = [row for row in owner.apple_operations() if row['entity']=='item' and row['local_id']==identity and row['action']=='delete']
    if not pending:
        return False
    if choice=='keep_apple':
        for operation in pending:
            owner.cancel_apple_operation(operation['id'])
        return False
    for operation in pending:
        if sync._get('operation-receipt:' + operation['id']) != delete_receipt(operation, kind):
            raise ValueError('Apple 删除需先核对具体目标并重新确认；已暂停并保留本地记录')
    for operation in pending:
        finish_operation(owner, operation['id'])
    mapping.update(state='deleted',conflict='')
    sync._save_map(mapping)
    return True
