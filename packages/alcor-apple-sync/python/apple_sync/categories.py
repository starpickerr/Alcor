"""Stable, one-to-one category lifecycle shared by calendar and reminder sync."""
from datetime import datetime, timedelta
from contextlib import contextmanager
from copy import deepcopy
import json

from apple_sync.contracts import category_binding, process_operations
from apple_sync.category_links import all_source_links, reconcile_names


def verify_category_source(category, link, native):
    """A reused native container ID does not authorize changing its account."""
    from apple_sync.group_scan_claims import ScanUnavailable
    binding = category.get('apple') or {}
    if (category['id'] != link['alcor_id'] or native.get('id') != link['apple_id']
            or binding.get('apple_id') != link['apple_id'] or not binding.get('source_id')
            or binding['source_id'] != native.get('source_id')):
        raise ScanUnavailable('Apple 分类来源或关联无法核实；该分类已保留待核对，其他分类继续同步')


@contextmanager
def _category_claim(sync, service, kind, link, native):
    """Existing category property writes share the same container/item claims."""
    from apple_sync.group_scan_claims import WorkClaim
    native=deepcopy(native)
    group=link['apple_id'];source=native.get('source_id')
    group_key='list_id' if kind=='reminders' else 'calendar_id'
    local_key='todo_id' if kind=='reminders' else 'alcor_id'
    mappings=[row for row in sync._maps() if row[group_key]==group]
    expected=service.apple_capture_category_absence(link['alcor_id']);local=expected['local']
    verify_category_source(local,link,native)
    evidence={'kind':'category_properties','link':dict(link),'native':native,'expected':expected,'mappings':mappings}
    with WorkClaim(sync,{group:source},[row[local_key] for row in mappings],evidence) as claim:
        inventory=sync.bridge.call('reminder_lists' if kind=='reminders' else 'calendars')
        rows=inventory['lists' if kind=='reminders' else 'calendars']
        comparable=lambda row:{key:value for key,value in row.items() if key!='default'}
        if (source not in inventory.get('source_ids',[]) or
                [comparable(row) for row in rows if row.get('id')==group]!=[comparable(native)] or
                [row for row in sync._stored_links() if row['apple_id']==group]!=[link] or
                [row for row in sync._maps() if row[group_key]==group]!=mappings or
                service.apple_capture_category_absence(link['alcor_id'])!=expected):
            raise ValueError('category changed before claimed property update')
        yield claim,local,expected


def _save_claimed_link(sync, claim, before, updated):
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE');claim._check(db)
        current=db.execute('SELECT * FROM category_links WHERE apple_id=?',(before['apple_id'],)).fetchone()
        if not current or dict(current)!=before:
            raise ValueError('category link changed before property commit')
        db.execute('UPDATE category_links SET apple_name=?,alcor_name=?,name_conflict=?,updated_at=? WHERE apple_id=?',
                   (updated['apple_name'],updated['alcor_name'],updated['name_conflict'],sync.clock(),before['apple_id']))


def _apply_category(sync, service, claim, expected, payload, binding=None):
    native_started=claim.started_effects
    if not native_started:
        claim.started('owner_claiming')
    try:
        result=service.apple_apply_category_if_unchanged(expected,payload,binding)
    except ValueError as error:
        if not native_started and str(error).startswith('CATEGORY_CHANGED:'):
            claim.owner_rejected()
        raise
    claim.started('owner_committed')
    return result


def _rename_claimed_category(sync, service, kind, claim, expected, native, title, color=None):
    """One conditional native save; unknown outcomes retain the existing claim."""
    from apple_sync.bridge import BridgeError
    from apple_sync.group_scan_claims import digest
    reminder=kind=='reminders';op='reminder_rename' if reminder else 'rename'
    identity='list_id' if reminder else 'calendar_id';singular='list' if reminder else 'calendar'
    snapshot=deepcopy({key:value for key,value in native.items() if key!='default'})
    capabilities=sync.bridge.call('reminder_status' if reminder else 'status')
    if capabilities.get('category_write_version')!=1:
        raise ValueError('当前 Apple helper 不支持分类条件修改，请更新 helper 后重试；本地修改已保留')
    if service.apple_capture_category_absence(expected['local_id'])!=expected:
        raise ValueError('category changed before native property send')
    payload={identity:snapshot['id'],'title':title,'category_write_version':1,
             'expected_source_id':snapshot['source_id'],'expected_snapshot':snapshot}
    if color is not None:payload['color']=color
    binding=digest({'claim':claim.tokens,'request':payload})
    receipt=sync.db_path.parent/'native-receipts'/('category-write-'+binding+'.json')
    receipt.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    payload.update(receipt_binding=binding,receipt_file=str(receipt))
    claim.started('sending')
    try:
        response=sync.bridge.call(op,**payload)
    except BridgeError as error:
        if error.native_no_side_effects:claim.native_rejected()
        raise
    saved=response.get(singular);proof=response.get('category_receipt')
    wanted={'version':1,'binding':binding,'op':op,'target_id':snapshot['id'],
            'source_id':snapshot['source_id'],'expected_snapshot':snapshot,
            'title':title,'color':color,'result':saved}
    if (not isinstance(saved,dict) or proof!=wanted or saved.get('id')!=snapshot['id']
            or saved.get('source_id')!=snapshot['source_id'] or saved.get('title')!=title
            or color is not None and str(saved.get('color','')).upper()!=color.upper()):
        raise ValueError('Apple 分类修改回执不完整或不匹配，已保留操作证据，请核对')
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE');claim._check(db)
        db.execute('INSERT INTO settings VALUES (?,?)',
                   ('category-write-receipt:'+binding,json.dumps(proof,ensure_ascii=False,sort_keys=True)))
    claim.started('native_committed')
    return deepcopy(saved)


def _bind_claimed_category(sync, service, kind, link, native, *, record_observation=False):
    with _category_claim(sync,service,kind,link,native) as (claim,local,expected):
        _apply_category(sync,service,claim,expected,{},category_binding(native))
        if record_observation:
            sync._set('category-missing:'+native['id'],None)
            sync._set('category-absence:'+native['id'],None)
            sync._set('category-snapshot:'+native['id'],native)
            sync._set('category-source:'+native['id'],native.get('source_id'))


def _category_wait(conflicts, link, reason):
    if not any(row['apple_id']==link['apple_id'] and row['kind']=='category_waiting' for row in conflicts):
        conflicts.append({'apple_id':link['apple_id'],'alcor_id':link['alcor_id'],
                          'kind':'category_waiting','reason':str(reason)})

def synchronize_categories(sync, service, kind):
    reminder = kind == 'reminders'
    listing, collection = ('reminder_lists', 'lists') if reminder else ('calendars', 'calendars')
    create, delete = ('reminder_list_create', 'reminder_list_delete') if reminder else ('create_calendar', 'delete_calendar')
    identity = 'list_id' if reminder else 'calendar_id'
    singular = 'list' if reminder else 'calendar'
    local_only = set(service.apple_local_only_categories())
    categories = {row['id']: row for row in service.categories() if row['id'] not in local_only}
    observations = {}
    # Snapshot before the first native inventory read, never after learning of absence.
    for link in sync._stored_links():
        if (not link['enabled'] or link['alcor_id'] not in categories
                or sync._get('category-missing:'+link['apple_id']) or sync._get('category-absence:'+link['apple_id'])):
            continue
        try:
            observations[link['apple_id']] = service.apple_capture_category_absence(link['alcor_id'])
        except (ValueError, KeyError):
            pass
    # Capture retained legacy members before the first native inventory read.
    legacy_observations={}
    for link in sync._stored_links():
        if not link['enabled'] or link['alcor_id'] in categories or link['alcor_id'] in local_only:continue
        mappings=[row for row in sync._maps() if row[identity]==link['apple_id']]
        ids=[row['todo_id' if reminder else 'alcor_id'] for row in mappings if sync._local(row['todo_id' if reminder else 'alcor_id']) is not None]
        if not ids:continue
        try:legacy_observations[link['apple_id']]={'expected':service.apple_capture_legacy_category(link['alcor_id'],ids),'mappings':mappings}
        except (ValueError,KeyError):pass
    inventory = sync.bridge.call(listing)
    sources = {row['id']: {**row,'default':row['id']==inventory.get('default_id')} for row in inventory[collection]}
    sync._set('available_sources',inventory.get('sources',[]))
    sync._set('default_source_id',inventory.get('default_source_id'))
    removed = 0
    conflicts = []
    from apple_sync.export_reservations import held_groups
    held = held_groups(sync)
    from apple_sync.category_rebuild import retained
    quarantined = set(held)|{row['old_group'] for row in retained(sync)}
    eligible_links = lambda: [link for link in sync._stored_links() if link['alcor_id'] not in local_only]
    previous = eligible_links()
    occupied = {row['apple_id'] for row in previous if row['apple_id'] in sources}
    # Rebind a uniquely identified calendar before treating an old identifier as absent.
    for link in previous:
        if link['apple_id'] in held:
            continue
        if not link['enabled']:
            continue
        absence = sync._get('category-absence:'+link['apple_id'])
        if absence and absence.get('phase') in ('deleting','uncertain'):
            conflicts.append({'apple_id':link['apple_id'],'alcor_id':link['alcor_id'],'kind':'category_delete_uncertain',
                              'reason':'分类删除结果或并发变化待核对；首次快照与恢复信息已保留，不会自动重试'})
            quarantined.add(link['apple_id'])
            continue
        apple = sources.get(link['apple_id'])
        snapshot_key = 'category-snapshot:' + link['apple_id']
        snapshot = sync._get(snapshot_key, {})
        if link['alcor_id'] not in categories:
            mapped_records=[sync._local(row['todo_id' if reminder else 'alcor_id']) for row in sync._maps() if row[identity]==link['apple_id']]
            retained=[row for row in mapped_records if row is not None]
            if retained:
                from apple_sync.category_legacy import restore
                observed=legacy_observations.get(link['apple_id'],{})
                try:
                    restored=restore(sync,service,kind,link,apple or snapshot or {'title':link['apple_name']},observed.get('expected'),observed.get('mappings'))
                except ValueError as error:
                    _category_wait(conflicts,link,error);quarantined.add(link['apple_id']);continue
                categories[restored['id']]=restored
        if sync._get('legacy-restored:'+link['apple_id']):
            conflicts.append({'apple_id':link['apple_id'],'alcor_id':link['alcor_id'],'kind':'legacy_retained',
                'reason':'旧版删除分类后仍保留了日程或事务。已恢复其原分类归属以保护数据；请确认保留，或在分类管理中按新规则删除分类与内容。'})
        if apple is None:
            candidates = [row for row in sources.values() if row['id'] not in occupied and
                          row['title']==(snapshot.get('title') or link['apple_name']) and
                          (not snapshot.get('source_id') or row.get('source_id')==snapshot['source_id'])]
            if len(candidates)==1:
                apple = candidates[0]
                old_id = link['apple_id']
                if not reminder:
                    # Existing destination only: claims + native item identity + owner CAS.
                    from apple_sync.calendar_rebind import EmptyCategoryIdentity, UnverifiedCategoryIdentity, rebind
                    try:
                        rebind(sync,service,link,snapshot,apple)
                    except EmptyCategoryIdentity:
                        quarantined.update((old_id, apple['id']))
                        conflicts.append({'apple_id':old_id,'alcor_id':link['alcor_id'],
                            'kind':'unverified_empty_category','candidates':candidates,
                            'reason':'空分类没有日程身份可核对，已保留原关联；同名不能证明是同一分类，请核对来源'})
                        continue
                    except UnverifiedCategoryIdentity as error:
                        quarantined.update((old_id, apple['id']))
                        conflicts.append({'apple_id':old_id,'alcor_id':link['alcor_id'],
                            'kind':'unverified_category_identity','candidates':candidates,'reason':str(error)})
                        continue
                else:
                    from apple_sync.reminder_rebind import rebind
                    from apple_sync.calendar_rebind import UnverifiedCategoryIdentity
                    from apple_sync.group_scan_claims import ScanHeld
                    try:rebind(sync,service,link,snapshot,apple)
                    except (UnverifiedCategoryIdentity,ScanHeld) as error:
                        quarantined.update((old_id,apple['id']))
                        conflicts.append({'apple_id':old_id,'alcor_id':link['alcor_id'],
                            'kind':'unverified_category_identity','candidates':candidates,'reason':str(error)})
                        continue
                sync._set('category-colors:'+apple['id'],sync._get('category-colors:'+old_id))
                link['apple_id'] = apple['id']
                occupied.add(apple['id'])
            else:
                source_id = snapshot.get('source_id') or sync._get('category-source:'+link['apple_id'])
                known_account = source_id and source_id in inventory.get('source_ids',[])
                unmatched = [row for row in sources.values() if row['id'] not in occupied]
                # Account presence and an unchanged complete local inventory are both required.
                safe = bool(known_account and not unmatched and not candidates)
                local_category = categories.get(link['alcor_id'], {})
                wanted = (local_category.get('apple') or {}).get('desired_title') or local_category.get('name')
                colors = sync._get('category-colors:'+link['apple_id'])
                if (wanted != link['alcor_name'] or local_category.get('apple_pending')
                        or colors and local_category.get('color') != colors[1]):
                    safe = False
                records = service.list_todos() if reminder else service.list_events()
                mappings = {row['todo_id' if reminder else 'alcor_id']:row for row in sync._maps()}
                if safe:
                    from apple_sync.calendar import _fingerprint
                    from apple_sync.reminders import _hash
                    from apple_sync.metadata import metadata
                    for record in [row for row in records if row.get('category_id')==link['alcor_id']]:
                        mapping = mappings.get(record['id'])
                        baseline = sync._get('item-metadata:'+record['id'])
                        digest = _hash(record) if reminder else _fingerprint(record,series=mapping['origin']=='alcor') if mapping else None
                        if not mapping or mapping['state']!='active' or digest!=mapping['local_hash'] or not baseline or metadata(record)!=baseline[0] or record.get('apple_pending'):
                            safe = False
                            break
                from apple_sync.category_absence import reconcile
                deleted, absence_reason = reconcile(sync, service, kind, link, inventory, safe,
                                                      observed=observations.get(link['apple_id']))
                if deleted:
                    categories.pop(link['alcor_id'],None)
                    removed += 1
                    continue
                quarantined.update(row['id'] for row in candidates)
                conflicts.append({'apple_id':link['apple_id'],'alcor_id':link['alcor_id'],
                    'reason':absence_reason,
                    'kind':'missing_source','candidates':candidates})
                continue
        if link['alcor_id'] in categories:
            try:
                _bind_claimed_category(sync,service,kind,link,apple,record_observation=True)
            except Exception as error:
                _category_wait(conflicts,link,error)
        else:
            # A legacy missing local category is not a specific approval to delete Apple data.
            conflicts.append({'apple_id':apple['id'],'alcor_id':link['alcor_id'],'kind':'legacy_delete',
                'reason':'旧版分类删除尚未核对；Apple 分类与关联已保留，需要重新确认具体来源与操作。'})
            quarantined.add(apple['id'])
            continue
    completed, operation_errors = process_operations(sync,service,kind)
    if completed:
        removed += completed
        categories = {row['id']:row for row in service.categories() if row['id'] not in local_only}
        sources = {row['id']:row for row in sync.bridge.call(listing)[collection]}
        for link in eligible_links():
            if link['alcor_id'] not in categories and link['apple_id'] not in sources:
                with sync._connect() as db:
                    db.execute(f"UPDATE mappings SET state='deleted' WHERE {identity}=?", (link['apple_id'],))
                    db.execute('DELETE FROM category_links WHERE apple_id=?', (link['apple_id'],))
    conflicts = [row for row in conflicts if row['alcor_id'] in categories or row['kind'] in ('legacy_delete','category_delete_uncertain','category_waiting')]
    sync._set('category_conflicts',conflicts)
    sync._set('operation_errors',operation_errors)
    stored = eligible_links()
    mapped = {row['todo_id' if reminder else 'alcor_id'] for row in sync._maps()}
    records = service.list_todos() if reminder else service.list_events()
    unclassified = [row for row in records if not row.get('category_id') and row['id'] not in mapped]
    if unclassified:
        read_only = {row['alcor_id'] for row in stored if row['direction'] == 'import'}
        default = next((row for row in categories.values() if row['id'] not in read_only), None)
        if default is None:
            name = '提醒事项' if reminder else '日程'
            names = {row['name'] for row in categories.values()}
            while name in names:
                name += ' · Alcor'
            default = service.create_category({'name': name})
            categories[default['id']] = default
        for row in unclassified:
            if reminder:
                service.set_todo_category(row['id'], default['id'])
            else:
                service.set_event_category(row['id'], default['id'])
    used = {row['alcor_id'] for row in stored}
    linked_sources = {row['apple_id'] for row in stored}
    # Reserve matching local names before exporting local-only categories.
    for apple in sources.values():
        if apple['id'] not in linked_sources:
            match = next((row for row in categories.values() if row['name'] == apple['title'] and
                          row['id'] not in used), None)
            if match:
                used.add(match['id'])
    from apple_sync.category_create import create_category, pending_in_db
    create_errors=[]
    for local in categories.values():
        if local['id'] in used:
            continue
        target = (local.get('apple') or {}).get('source_id') or inventory.get('default_source_id')
        try:
            apple=create_category(sync,service,kind,local,inventory,source_id=target)
        except Exception as error:
            create_errors.append({'local_id':local['id'],'title':local['name'],'source_id':target or '',
                                  'state':'waiting','reason':str(error)})
            continue
        sources[apple['id']]=apple
        linked_sources.add(apple['id']);used.add(local['id'])
    sync._set('category_create_errors',create_errors)
    stored=eligible_links()
    linked_sources={row['apple_id'] for row in stored}
    with sync._connect() as db:pending_rows=pending_in_db(db,kind)
    pending={row['source_id'] for row in pending_rows}
    pending_local={row['local_id'] for row in pending_rows}
    sources = {key:value for key,value in sources.items() if key not in quarantined
               and (key in linked_sources or value.get('source_id') not in pending)}
    links = all_source_links(list(sources.values()), list(categories.values()), stored, unavailable_ids=pending_local)
    keys = lambda rows: [(r['apple_id'], r['alcor_id'], r['direction'], bool(r['enabled'])) for r in rows]
    if sorted(keys(links)) != sorted(keys(stored)):
        sync.replace_links(links, preserve_missing=True)
    if sync._get('sync_all', False):
        sync._set('connected', True)
    selected_key = 'list_ids' if reminder else 'calendar_ids'
    sync._set(selected_key, [row['apple_id'] for row in eligible_links() if row['enabled']])
    for link in eligible_links():
        if link['enabled'] and link['apple_id'] in sources:
            try:
                _bind_claimed_category(sync,service,kind,link,sources[link['apple_id']])
            except Exception as error:
                _category_wait(conflicts,link,error)
    # Colors use their own baseline so a change on both sides remains a conflict.
    for link in eligible_links():
        apple = sources.get(link['apple_id'])
        if not apple or not apple.get('color') or not link['enabled']:
            continue
        try:
            with _category_claim(sync,service,kind,link,apple) as (claim,local,expected):
                key = 'category-colors:' + link['apple_id']
                old = sync._get(key) or [apple['color'], local['color']]
                action, color = reconcile_names(*old, apple['color'], local['color'])
                if action == 'conflict':
                    conflicts.append({'apple_id':apple['id'],'alcor_id':local['id'],'kind':'color','reason':'Apple 与 Alcor 同时修改颜色，请选择保留哪一侧'})
                    continue
                if action == 'apple_to_alcor':
                    _apply_category(sync,service,claim,expected,{'color':color},category_binding(apple))
                elif action == 'alcor_to_apple':
                    if not category_binding(apple)['can_rename']:
                        conflicts.append({'apple_id':apple['id'],'alcor_id':local['id'],'kind':'color','reason':'Apple 不允许修改此分类的颜色'})
                        continue
                    saved = _rename_claimed_category(sync,service,kind,claim,expected,apple,apple['title'],color)
                    _apply_category(sync,service,claim,expected,{},category_binding(saved))
                sync._set(key, [color, color] if color else [apple['color'], local['color']])
        except Exception as error:
            _category_wait(conflicts,link,error)
    sync._set('category_conflicts',conflicts)
    return removed


def synchronize_moves(sync, service, kind, fingerprint):
    """Move an existing item using its identity, never by exporting a second copy."""
    reminder = kind == 'reminders'
    group = 'list_id' if reminder else 'calendar_id'
    local_key = 'todo_id' if reminder else 'alcor_id'
    remote_key = 'reminder_id' if reminder else 'event_id'
    local_only = set(service.apple_local_only_categories())
    links = {row['apple_id']: row for row in sync._stored_links() if row['enabled'] and row['alcor_id'] not in local_only}
    by_local = {row['alcor_id']: row for row in links.values()}
    from apple_sync.export_reservations import held_groups
    held = held_groups(sync)
    moved = 0
    move_errors = []
    for mapping in sync._maps():
        if mapping[group] in held or mapping['state'] != 'active':
            continue
        original = links.get(mapping[group])
        local = sync._local(mapping[local_key])
        if not original or not local or local.get('category_id') == original['alcor_id']:
            continue
        target = by_local.get(local.get('category_id'))
        if target and target['apple_id'] in held:
            continue
        if not target or target['direction'] not in ('export', 'bidirectional') or original['direction'] == 'import':
            sync._conflict(mapping, '目标分类未连接可写 Apple 来源，无法同步移动')
            continue
        try:
            inventory=sync.bridge.call('reminder_lists' if reminder else 'calendars')
            for selected_link in (original,target):
                native=[row for row in inventory['lists' if reminder else 'calendars'] if row['id']==selected_link['apple_id']]
                if len(native)!=1:
                    raise ValueError('移动分类来源暂不可用；保留待核对')
                verify_category_source(service.get_category(selected_link['alcor_id']),selected_link,native[0])
        except ValueError as error:
            move_errors.append({'local_id':mapping[local_key],
                                'group_ids':[original['apple_id'],target['apple_id']],'reason':str(error)})
            continue
        if not reminder:
            from apple_sync.calendar_moves import move
            try:
                moved += move(sync, service, mapping, original, target, fingerprint)
            except Exception as error:
                move_errors.append({'local_id': mapping['alcor_id'],
                                    'group_ids': [original['apple_id'], target['apple_id']], 'reason': str(error)})
            continue
        from apple_sync.reminder_moves import move
        try:
            moved += move(sync,service,mapping,original,target)
        except Exception as error:
            move_errors.append({'local_id':mapping['todo_id'],
                                'group_ids':[original['apple_id'],target['apple_id']],'reason':str(error)})

    sync._set('reminder_move_errors' if reminder else 'calendar_move_errors', move_errors)
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
            with _category_claim(sync,service,kind,link,apple) as (claim,local,expected):
                before=dict(link)
                binding = local.get('apple') or {}
                wanted = binding.get('desired_title') or local['name']
                action,name = reconcile_names(link['apple_name'],link['alcor_name'],apple['title'],wanted)
                if action=='conflict':
                    link['name_conflict']='Apple 与 Alcor 同时更改了分类名称，请选择保留哪一侧'
                    _save_claimed_link(sync,claim,before,link)
                    errors.append(link['name_conflict']);continue
                if action=='apple_to_alcor':
                    local = _apply_category(sync,service,claim,expected,{'name':name},category_binding(apple))
                elif action=='alcor_to_apple':
                    if not category_binding(apple)['can_rename']:
                        raise ValueError('Apple 不允许修改此分类的名称')
                    apple = _rename_claimed_category(sync,service,kind,claim,expected,apple,name)
                    local = _apply_category(sync,service,claim,expected,{},category_binding(apple))
                link.update(apple_name=apple['title'],alcor_name=local['name'],name_conflict='')
                _save_claimed_link(sync,claim,before,link)
        except Exception as error:
            errors.append(str(error))
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
    local = service.get_category(link['alcor_id'])
    if choice=='acknowledge' and sync._get('legacy-restored:'+apple_id):
        sync._set('legacy-restored:'+apple_id,False)
        sync._set('category_conflicts',[row for row in sync._get('category_conflicts',[]) if not(row['apple_id']==apple_id and row['kind']=='legacy_retained')])
        return sync.status()
    remote = sources.get(apple_id)
    if replacement_id:
        replacement=sources.get(replacement_id)
        snapshot=sync._get('category-snapshot:'+apple_id) or {}
        source=(local.get('apple') or {}).get('source_id')
        if remote or not replacement or replacement.get('source_id')!=source or any(row['apple_id']==replacement_id for row in sync._stored_links()):
            raise ValueError('仅能核验同源且尚未关联的替代分类，原内容已保留')
        if reminder:
            from apple_sync.reminder_rebind import rebind
        else:
            from apple_sync.calendar_rebind import rebind
        if not reminder:replacement={**replacement,'default':replacement['id']==inventory.get('default_id')}
        rebind(sync,service,link,snapshot,replacement)
        return sync.status()
    elif remote:
        if choice not in ('keep_alcor','keep_apple') or field not in ('name','color'):
            raise ValueError('请选择名称或颜色的处理方式')
        with _category_claim(sync,service,kind,link,remote) as (claim,local,expected):
            before=dict(link)
            if choice=='keep_alcor':
                if not category_binding(remote)['can_rename']:
                    raise PermissionError('Apple 不允许修改此分类')
                remote = _rename_claimed_category(sync,service,kind,claim,expected,remote,
                    (local.get('apple') or {}).get('desired_title') or local['name'] if field=='name' else remote['title'],
                    local['color'] if field=='color' else None)
            else:
                local = _apply_category(sync,service,claim,expected,{'name':remote['title']} if field=='name' else {'color':remote['color']},category_binding(remote))
            if choice=='keep_alcor':
                local = _apply_category(sync,service,claim,expected,{},category_binding(remote))
            link.update(apple_name=remote['title'],alcor_name=local['name'],name_conflict='')
            _save_claimed_link(sync,claim,before,link)
            if field=='color':
                sync._set('category-colors:'+apple_id,[remote['color'],local['color']])
            sync._set('category_conflicts',[row for row in sync._get('category_conflicts',[]) if not(row['apple_id']==apple_id and row['kind']==field)])
        return sync.status()
    elif choice=='keep_apple':
        from apple_sync.category_absence import reconcile
        deleted, reason = reconcile(sync, service, kind, link, inventory, True, confirm=True)
        if not deleted:
            raise ValueError(reason)
        sync._set('category_conflicts',[row for row in sync._get('category_conflicts',[]) if row['apple_id']!=apple_id])
        return sync.status()
    elif choice=='keep_alcor':
        raise ValueError('请在所有者页面预览并确认创建空 Apple 容器；旧成员将保留本地待重新关联')
    else:
        raise ValueError('请选择可核验的重新关联、采用 Apple 删除或预览创建空容器')


def prime_bindings(sync,service,kind):
    from apple_sync.export_reservations import held_groups
    held = held_groups(sync)
    # Placeholders only restrict legacy rows; a concurrent verified binding always wins.
    for link in sync._stored_links():
        if link['apple_id'] in held:
            continue
        expected=service.apple_capture_unbound('category',link['alcor_id'])
        if expected is not None:
            service.apple_prime_unbound(expected,{'apple_id':link['apple_id'],'title':link['apple_name'],
                'can_modify':False,'can_rename':False,'can_delete':False,'awaiting_permissions':True})
    for mapping in sync._maps():
        if mapping['list_id' if kind=='reminders' else 'calendar_id'] in held:
            continue
        expected=service.apple_capture_unbound('item',mapping['todo_id' if kind=='reminders' else 'alcor_id'])
        if expected is not None:
            service.apple_prime_unbound(expected,{'apple_id':mapping['reminder_id' if kind=='reminders' else 'event_id'],
                'group_id':mapping['list_id' if kind=='reminders' else 'calendar_id'],'can_modify':False,'awaiting_permissions':True})
