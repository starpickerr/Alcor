"""Stable, one-to-one category lifecycle shared by calendar and reminder sync."""
from datetime import datetime, timedelta

from apple_sync.contracts import category_binding, process_operations
from apple_sync.category_links import all_source_links, reconcile_names


def synchronize_categories(sync, service, kind):
    reminder = kind == 'reminders'
    listing, collection = ('reminder_lists', 'lists') if reminder else ('calendars', 'calendars')
    create, delete = ('reminder_list_create', 'reminder_list_delete') if reminder else ('create_calendar', 'delete_calendar')
    identity = 'list_id' if reminder else 'calendar_id'
    singular = 'list' if reminder else 'calendar'
    categories = {row['id']: row for row in service.categories()}
    inventory = sync.bridge.call(listing)
    sources = {row['id']: {**row,'default':row['id']==inventory.get('default_id')} for row in inventory[collection]}
    sync._set('available_sources',inventory.get('sources',[]))
    sync._set('default_source_id',inventory.get('default_source_id'))
    removed = 0
    conflicts = []
    quarantined = set()
    previous = sync._stored_links()
    occupied = {row['apple_id'] for row in previous if row['apple_id'] in sources}
    # Rebind a uniquely identified calendar before treating an old identifier as absent.
    for link in previous:
        if not link['enabled']:
            continue
        apple = sources.get(link['apple_id'])
        snapshot_key = 'category-snapshot:' + link['apple_id']
        snapshot = sync._get(snapshot_key, {})
        if link['stella_id'] not in categories:
            mapped_records=[sync._local(row['todo_id' if reminder else 'stella_id']) for row in sync._maps() if row[identity]==link['apple_id']]
            retained=[row for row in mapped_records if row is not None]
            if retained:
                restored=service.apple_restore_category(link['stella_id'],apple or snapshot or {'title':link['apple_name']})
                categories[restored['id']]=restored
                for record in retained:
                    service.apple_place_item(record['id'],restored['id'])
                sync._set('legacy-restored:'+link['apple_id'],True)
        if sync._get('legacy-restored:'+link['apple_id']):
            conflicts.append({'apple_id':link['apple_id'],'stella_id':link['stella_id'],'kind':'legacy_retained',
                'reason':'旧版删除分类后仍保留了日程或事务。已恢复其原分类归属以保护数据；请确认保留，或在分类管理中按新规则删除分类与内容。'})
        if apple is None:
            candidates = [row for row in sources.values() if row['id'] not in occupied and
                          row['title']==(snapshot.get('title') or link['apple_name']) and
                          (not snapshot.get('source_id') or row.get('source_id')==snapshot['source_id'])]
            if len(candidates)==1:
                apple = candidates[0]
                old_id = link['apple_id']
                with sync._connect() as db:
                    db.execute('UPDATE category_links SET apple_id=? WHERE apple_id=?', (apple['id'],old_id))
                    db.execute(f'UPDATE mappings SET {identity}=? WHERE {identity}=?', (apple['id'],old_id))
                sync._set('category-colors:'+apple['id'],sync._get('category-colors:'+old_id))
                link['apple_id'] = apple['id']
                occupied.add(apple['id'])
            else:
                source_id = snapshot.get('source_id') or sync._get('category-source:'+link['apple_id'])
                known_account = source_id and source_id in inventory.get('source_ids',[])
                unmatched = [row for row in sources.values() if row['id'] not in occupied]
                missing_key = 'category-missing:'+link['apple_id']
                since = sync._get(missing_key)
                sync._set(missing_key,since or sync.clock())
                # Account presence and an unchanged complete local inventory are both required.
                safe = bool(known_account and not unmatched and not candidates)
                records = service.list_todos() if reminder else service.list_events()
                mappings = {row['todo_id' if reminder else 'stella_id']:row for row in sync._maps()}
                if safe:
                    from apple_sync.calendar import _fingerprint
                    from apple_sync.reminders import _hash
                    from apple_sync.metadata import metadata
                    for record in [row for row in records if row.get('category_id')==link['stella_id']]:
                        mapping = mappings.get(record['id'])
                        baseline = sync._get('item-metadata:'+record['id'])
                        digest = _hash(record) if reminder else _fingerprint(record,series=mapping['origin']=='stella') if mapping else None
                        if not mapping or mapping['state']!='active' or digest!=mapping['local_hash'] or not baseline or metadata(record)!=baseline[0] or record.get('apple_pending'):
                            safe = False
                            break
                if safe and since and datetime.fromisoformat(sync.clock().replace('Z','+00:00'))-datetime.fromisoformat(since.replace('Z','+00:00'))>=timedelta(minutes=1):
                    service.apple_remove_category(link['stella_id'])
                    categories.pop(link['stella_id'],None)
                    with sync._connect() as db:
                        db.execute(f"UPDATE mappings SET state='remote_deleted',conflict='' WHERE {identity}=?",(link['apple_id'],))
                        db.execute('DELETE FROM category_links WHERE apple_id=?',(link['apple_id'],))
                    removed += 1
                    continue
                quarantined.update(row['id'] for row in candidates)
                conflicts.append({'apple_id':link['apple_id'],'stella_id':link['stella_id'],
                    'reason':'Apple 来源暂不可用或身份已变化；保留全部本地数据，请重新关联或确认来源已删除',
                    'kind':'missing_source','candidates':candidates})
                continue
        sync._set('category-missing:'+apple['id'],None)
        sync._set('category-snapshot:'+apple['id'],apple)
        sync._set('category-source:'+apple['id'],apple.get('source_id'))
        if link['stella_id'] in categories:
            service.apple_bind('category',link['stella_id'],category_binding(apple))
        else:
            # Upgrade old already-deleted mappings without deleting any additional local data.
            if not category_binding(apple)['can_delete']:
                conflicts.append({'apple_id':apple['id'],'stella_id':link['stella_id'],'kind':'legacy_delete',
                    'reason':'旧删除尚未完成，Apple 不允许删除该来源，请重新关联保留内容'})
                continue
            sync.bridge.call(delete,**{identity:apple['id']})
            sources.pop(apple['id'],None)
            with sync._connect() as db:
                db.execute(f"UPDATE mappings SET state='deleted' WHERE {identity}=?", (apple['id'],))
                db.execute('DELETE FROM category_links WHERE apple_id=?', (apple['id'],))
            removed += 1
    completed, operation_errors = process_operations(sync,service,kind)
    if completed:
        removed += completed
        categories = {row['id']:row for row in service.categories()}
        sources = {row['id']:row for row in sync.bridge.call(listing)[collection]}
        for link in sync._stored_links():
            if link['stella_id'] not in categories and link['apple_id'] not in sources:
                with sync._connect() as db:
                    db.execute(f"UPDATE mappings SET state='deleted' WHERE {identity}=?", (link['apple_id'],))
                    db.execute('DELETE FROM category_links WHERE apple_id=?', (link['apple_id'],))
    conflicts = [row for row in conflicts if row['stella_id'] in categories]
    sync._set('category_conflicts',conflicts)
    sync._set('operation_errors',operation_errors)
    stored = sync._stored_links()
    mapped = {row['todo_id' if reminder else 'stella_id'] for row in sync._maps()}
    records = service.list_todos() if reminder else service.list_events()
    unclassified = [row for row in records if not row.get('category_id') and row['id'] not in mapped]
    if unclassified:
        read_only = {row['stella_id'] for row in stored if row['direction'] == 'import'}
        default = next((row for row in categories.values() if row['id'] not in read_only), None)
        if default is None:
            name = '提醒事项' if reminder else '日程'
            names = {row['name'] for row in categories.values()}
            while name in names:
                name += ' · Stella'
            default = service.create_category({'name': name})
            categories[default['id']] = default
        for row in unclassified:
            if reminder:
                service.set_todo_category(row['id'], default['id'])
            else:
                service.set_event_category(row['id'], default['id'])
    used = {row['stella_id'] for row in stored}
    linked_sources = {row['apple_id'] for row in stored}
    # Reserve matching local names before exporting local-only categories.
    for apple in sources.values():
        if apple['id'] not in linked_sources:
            match = next((row for row in categories.values() if row['name'] == apple['title'] and
                          row['id'] not in used), None)
            if match:
                used.add(match['id'])
    for local in categories.values():
        if local['id'] in used:
            continue
        target = (local.get('apple') or {}).get('source_id') or inventory.get('default_source_id')
        receipt = sync.db_path.parent/'native-receipts'/('category-'+local['id']+'.json')
        receipt.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        apple = sync.bridge.call(create,title=local['name'],color=local['color'],receipt_file=str(receipt),**({'source_id':target} if target else {}))[singular]
        if apple['id'] in linked_sources:
            raise ValueError('Apple 已有分类与另一个 Stella 分类关联，请处理同名冲突后重试')
        sources[apple['id']] = apple
    sources = {key:value for key,value in sources.items() if key not in quarantined}
    links = all_source_links(list(sources.values()), list(categories.values()), stored)
    keys = lambda rows: [(r['apple_id'], r['stella_id'], r['direction'], bool(r['enabled'])) for r in rows]
    if sorted(keys(links)) != sorted(keys(stored)):
        sync.replace_links(links)
    if sync._get('sync_all', False):
        sync._set('connected', True)
    selected_key = 'list_ids' if reminder else 'calendar_ids'
    sync._set(selected_key, [row['apple_id'] for row in sync._stored_links() if row['enabled']])
    for link in sync._stored_links():
        if link['apple_id'] in sources:
            service.apple_bind('category',link['stella_id'],category_binding(sources[link['apple_id']]))
    # Colors use their own baseline so a change on both sides remains a conflict.
    for link in sync._stored_links():
        apple = sources.get(link['apple_id'])
        if not apple or not apple.get('color') or not link['enabled']:
            continue
        local = service.get_category(link['stella_id'])
        key = 'category-colors:' + link['apple_id']
        old = sync._get(key) or [apple['color'], local['color']]
        action, color = reconcile_names(*old, apple['color'], local['color'])
        if action == 'conflict':
            conflicts.append({'apple_id':apple['id'],'stella_id':local['id'],'kind':'color','reason':'Apple 与 Stella 同时修改颜色，请选择保留哪一侧'})
            continue
        if action == 'apple_to_stella':
            service.apple_apply_category(local['id'], {'color': color})
        elif action == 'stella_to_apple':
            if not category_binding(apple)['can_rename']:
                conflicts.append({'apple_id':apple['id'],'stella_id':local['id'],'kind':'color','reason':'Apple 不允许修改此分类的颜色'})
                continue
            sync.bridge.call('reminder_rename' if reminder else 'rename',
                             **{identity: apple['id'], 'title': apple['title'], 'color': color})
        sync._set(key, [color, color] if color else [apple['color'], local['color']])
    sync._set('category_conflicts',conflicts)
    return removed


def synchronize_moves(sync, service, kind, fingerprint):
    """Move an existing item using its identity, never by exporting a second copy."""
    reminder = kind == 'reminders'
    group = 'list_id' if reminder else 'calendar_id'
    local_key = 'todo_id' if reminder else 'stella_id'
    remote_key = 'reminder_id' if reminder else 'event_id'
    links = {row['apple_id']: row for row in sync._stored_links() if row['enabled']}
    by_local = {row['stella_id']: row for row in links.values()}
    moved = 0
    for mapping in sync._maps():
        if mapping['state'] != 'active':
            continue
        original = links.get(mapping[group])
        local = sync._local(mapping[local_key])
        if not original or not local or local.get('category_id') == original['stella_id']:
            continue
        target = by_local.get(local.get('category_id'))
        if not target or target['direction'] not in ('export', 'bidirectional') or original['direction'] == 'import':
            sync._conflict(mapping, '目标分类未连接可写 Apple 来源，无法同步移动')
            continue
        lookup = {group: mapping[group], remote_key: mapping[remote_key], 'external_id': mapping['external_id']}
        if not reminder:
            lookup['occurrence_date'] = mapping['occurrence_date']
        remote = sync.bridge.call('reminder_lookup' if reminder else 'lookup', **lookup).get('reminder' if reminder else 'event')
        if remote is None or fingerprint(remote, mapping, True) != mapping['remote_hash'] or __import__('apple_sync.metadata',fromlist=['changed']).changed(sync,local,remote)[1]:
            sync._conflict(mapping, 'Apple 同时修改或移动了记录，请先处理冲突')
            continue
        payload = sync._write_payload(local, mapping, **{group: target['apple_id']})
        payload['lookup_' + group] = mapping[group]
        remote = sync.bridge.call('reminder_upsert' if reminder else 'upsert', **payload)['reminder' if reminder else 'event']
        mapping.update(sync._new_map(local, remote, mapping['origin'], mapping['source_key']))
        sync._save_map(mapping)
        moved += 1
    return moved


def reconcile_category_names(sync, service, kind):
    reminder = kind=='reminders'
    listing,collection,rename,identity = ('reminder_lists','lists','reminder_rename','list_id') if reminder else ('calendars','calendars','rename','calendar_id')
    available = {row['id']:row for row in sync.bridge.call(listing)[collection]}
    errors = []
    for link in sync._stored_links():
        if not link['enabled'] or link['apple_id'] not in available:
            continue
        try:
            apple = available[link['apple_id']]
            local = service.get_category(link['stella_id'])
            binding = local.get('apple') or {}
            wanted = binding.get('desired_title') or local['name']
            action,name = reconcile_names(link['apple_name'],link['stella_name'],apple['title'],wanted)
            if action=='conflict':
                raise ValueError('Apple 与 Stella 同时更改了分类名称，请选择保留哪一侧')
            if action=='apple_to_stella':
                local = service.apple_apply_category(local['id'],{'name':name})
            elif action=='stella_to_apple':
                if not category_binding(apple)['can_rename']:
                    raise ValueError('Apple 不允许修改此分类的名称')
                sync.bridge.call(rename,**{identity:apple['id'],'title':name})
                apple['title'] = name
                service.apple_bind('category',local['id'],category_binding(apple))
            link.update(apple_name=apple['title'],stella_name=local['name'],name_conflict='')
        except Exception as error:
            link['name_conflict'] = str(error)
            errors.append(str(error))
        sync._save_link(link)
    return errors


def resolve_category_conflict(sync, service, kind, apple_id, choice, replacement_id=None, source_id=None, field=None):
    reminder = kind=='reminders'
    listing,collection = ('reminder_lists','lists') if reminder else ('calendars','calendars')
    identity = 'list_id' if reminder else 'calendar_id'
    link = next((row for row in sync._stored_links() if row['apple_id']==apple_id),None)
    if not link:
        raise KeyError('分类关联不存在，请刷新')
    inventory = sync.bridge.call(listing)
    sources = {row['id']:row for row in inventory[collection]}
    local = service.get_category(link['stella_id'])
    if choice=='acknowledge' and sync._get('legacy-restored:'+apple_id):
        sync._set('legacy-restored:'+apple_id,False)
        sync._set('category_conflicts',[row for row in sync._get('category_conflicts',[]) if not(row['apple_id']==apple_id and row['kind']=='legacy_retained')])
        return sync.status()
    remote = sources.get(apple_id)
    if replacement_id:
        replacement = sources.get(replacement_id)
        if not replacement or any(row['apple_id']==replacement_id for row in sync._stored_links()):
            raise ValueError('请选择尚未关联的有效 Apple 来源')
    elif remote:
        if choice not in ('keep_stella','keep_apple') or field not in ('name','color'):
            raise ValueError('请选择名称或颜色的处理方式')
        if choice=='keep_stella':
            if not category_binding(remote)['can_rename']:
                raise PermissionError('Apple 不允许修改此分类')
            sync.bridge.call('reminder_rename' if reminder else 'rename',**{identity:apple_id,
                'title':(local.get('apple') or {}).get('desired_title') or local['name'] if field=='name' else remote['title'],
                **({'color':local['color']} if field=='color' else {})})
            remote = next(row for row in sync.bridge.call(listing)[collection] if row['id']==apple_id)
        else:
            local = service.apple_apply_category(local['id'],{'name':remote['title']} if field=='name' else {'color':remote['color']})
        service.apple_bind('category',local['id'],category_binding(remote))
        link.update(apple_name=remote['title'],stella_name=local['name'],name_conflict='')
        sync._save_link(link)
        if field=='color':
            sync._set('category-colors:'+apple_id,[remote['color'],local['color']])
        sync._set('category_conflicts',[row for row in sync._get('category_conflicts',[]) if not(row['apple_id']==apple_id and row['kind']==field)])
        return sync.status()
    elif choice=='keep_apple':
        # Explicitly confirmed source deletion; never inferred from an ID/account disappearing.
        service.apple_remove_category(local['id'])
        with sync._connect() as db:
            db.execute('DELETE FROM category_links WHERE apple_id=?',(apple_id,))
            db.execute(f"UPDATE mappings SET state='remote_deleted',conflict='' WHERE {identity}=?",(apple_id,))
        sync._set('category_conflicts',[row for row in sync._get('category_conflicts',[]) if row['apple_id']!=apple_id])
        return sync.status()
    elif choice=='keep_stella':
        target = source_id or (local.get('apple') or {}).get('source_id') or inventory.get('default_source_id')
        if not target or target not in [row['id'] for row in inventory.get('sources',[])]:
            raise ValueError('请选择当前可用的 Apple 账户')
        replacement = sync.bridge.call('reminder_list_create' if reminder else 'create_calendar',title=local.get('display_name',local['name']),color=local['color'],source_id=target)['list' if reminder else 'calendar']
    else:
        raise ValueError('请选择重新关联、采用 Apple 删除或保留 Stella 重建')
    with sync._connect() as db:
        db.execute('UPDATE category_links SET apple_id=?,apple_name=?,name_conflict=? WHERE apple_id=?',(replacement['id'],replacement['title'],'',apple_id))
        db.execute(f'UPDATE mappings SET {identity}=?,missing_since=? WHERE {identity}=?',(replacement['id'],'',apple_id))
    service.apple_bind('category',local['id'],category_binding(replacement))
    sync._set('category-snapshot:'+replacement['id'],replacement)
    sync._set('category_conflicts',[row for row in sync._get('category_conflicts',[]) if row['apple_id']!=apple_id])
    return sync.status()


def prime_bindings(sync,service,kind):
    # Old links have no separate property permissions; fail closed until native inventory confirms them.
    for link in sync._stored_links():
        try:
            local = service.get_category(link['stella_id'])
            if not local.get('apple'):
                service.apple_bind('category',local['id'],{'apple_id':link['apple_id'],'title':link['apple_name'],'can_modify':False,'can_rename':False,'can_delete':False,'awaiting_permissions':True})
        except KeyError:
            pass
    for mapping in sync._maps():
        local = sync._local(mapping['todo_id' if kind=='reminders' else 'stella_id'])
        if local and not local.get('apple'):
            service.apple_bind('item',local['id'],{'apple_id':mapping['reminder_id' if kind=='reminders' else 'event_id'],
                'group_id':mapping['list_id' if kind=='reminders' else 'calendar_id'],'can_modify':False,'awaiting_permissions':True})
