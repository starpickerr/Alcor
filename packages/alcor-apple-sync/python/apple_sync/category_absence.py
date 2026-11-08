"""Keep the first trusted category-absence snapshot until conditional deletion."""
import json
from datetime import datetime, timedelta


def _read(db, key):
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _manager(db, link, identity):
    current = db.execute('SELECT * FROM category_links WHERE apple_id=?', (link['apple_id'],)).fetchone()
    if not current or dict(current) != link:
        raise ValueError('category absence link changed; review required')
    local_key = 'todo_id' if identity == 'list_id' else 'alcor_id'
    mappings = [dict(row) for row in db.execute(f'SELECT * FROM mappings WHERE {identity}=? ORDER BY {local_key}', (link['apple_id'],))]
    baselines = {row[local_key]: _read(db, 'item-metadata:'+row[local_key]) for row in mappings}
    return {'mappings': mappings, 'baselines': baselines}


def _verified(sync, kind, link, source_id, inventory):
    reminder = kind == 'reminders'
    collection = 'lists' if reminder else 'calendars'
    status = sync.bridge.call('reminder_status' if reminder else 'status')
    if status.get('authorization') != 'full_access':
        return False
    source_ids = inventory.get('source_ids')
    rows = inventory.get(collection)
    if (not source_id or not isinstance(source_ids, list) or source_id not in source_ids
            or not isinstance(rows, list) or any(not row.get('id') or not row.get('source_id') for row in rows)
            or len({row['id'] for row in rows}) != len(rows)):
        return False
    known = {row['apple_id'] for row in sync._stored_links()}
    return not any(row['id'] == link['apple_id'] or row['id'] not in known for row in rows)


def reconcile(sync, service, kind, link, inventory, safe, *, observed=None, confirm=False):
    from apple_sync.group_scan_claims import WorkClaim
    doc=sync._get('category-absence:'+link['apple_id'])
    if not doc and sync._get('category-missing:'+link['apple_id']):
        return False,'旧分类缺失记录没有首次可信快照，已保留本地，请重新核对来源与内容'
    expected=doc.get('expected') if isinstance(doc,dict) else observed
    source=(expected.get('local',{}).get('apple') or {}).get('source_id') if isinstance(expected,dict) else None
    if (not safe or not source or link['direction'] not in ('import','bidirectional')
            or doc and doc.get('phase')!='observed'):
        return False,'分类来源、本地内容或上次操作尚待核对；本地分类与证据已保留'
    identity='list_id' if kind=='reminders' else 'calendar_id'
    try:
        targets=service.apple_category_absence_targets(expected)
        target_ids={row['id'] for row in targets}
        local_key='todo_id' if kind=='reminders' else 'alcor_id'
        if any(row[local_key] in target_ids and row[identity]!=link['apple_id'] for row in sync._maps()):
            return False,'递归成员仍关联其他 Apple 容器，请先在分类管理中核对成员归属；本地分类与内容已保留'
        if any(row.get('category_id')!=link['alcor_id'] or not row['binding']
               or row['binding'].get('group_id')!=link['apple_id']
               or row['binding'].get('source_id')!=source for row in targets):
            return False,'递归成员仍关联其他或未确认的 Apple 来源，请先在分类管理中核对成员归属；本地分类与内容已保留'
        with sync._connect() as db:before=_manager(db,link,identity)
        evidence={'kind':'category_absence','link':link,'first':doc,'expected':expected,'targets':targets,'manager':before}
        with WorkClaim(sync,{link['apple_id']:source},[row['id'] for row in targets],evidence) as claim:
            with sync._connect() as db:
                claim._check(db)
                if _manager(db,link,identity)!=before or _read(db,'category-absence:'+link['apple_id'])!=doc:
                    raise ValueError('category absence manager changed before claim')
            if service.apple_category_absence_targets(expected)!=targets:
                raise ValueError('category absence targets changed before claim')
            return _reconcile_claimed(sync,service,kind,link,inventory,safe,observed=observed,confirm=confirm,claim=claim)
    except Exception as error:
        return False,str(error)


def _reconcile_claimed(sync, service, kind, link, inventory, safe, *, observed, confirm, claim):
    """Return (deleted, reason); a missing old snapshot is never silently replaced."""
    key = 'category-absence:'+link['apple_id']
    missing_key = 'category-missing:'+link['apple_id']
    identity = 'list_id' if kind == 'reminders' else 'calendar_id'
    doc = sync._get(key)
    if doc and doc.get('phase') != 'observed':
        return False, '分类删除或并发变化尚待核对；已保留首次快照与恢复信息，请勿重复执行'
    since = sync._get(missing_key)
    if not doc and since:
        return False, '旧分类缺失记录没有首次可信快照，已保留本地，请重新核对来源与内容'
    if not safe or link['direction'] not in ('import', 'bidirectional'):
        return False, '来源或本地成员状态无法确认，已保留本地分类与内容'
    if not doc:
        expected = observed
        if not isinstance(expected, dict) or expected.get('local_id') != link['alcor_id']:
            return False, '缺少读取 Apple 库存前的本地快照，已保留本地，请重新核对'
        binding = expected['local'].get('apple') or {}
        source_id = binding.get('source_id')
        if binding.get('apple_id') != link['apple_id'] or not _verified(sync, kind, link, source_id, inventory):
            return False, '分类来源身份无法确认，已保留本地分类与内容'
        with sync._connect() as db:
            db.execute('BEGIN IMMEDIATE');claim._check(db)
            if _read(db, key) is not None or _read(db, missing_key):
                raise ValueError('first category absence observation changed')
            doc = {'version': 1, 'phase': 'observed', 'first_seen': sync.clock(), 'link': link,
                   'source_id': source_id, 'expected': expected, **_manager(db, link, identity)}
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(doc, ensure_ascii=False, sort_keys=True)))
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (missing_key, json.dumps(doc['first_seen'])))
        return False, '已记录首次可信分类缺失，保留本地等待复核'
    if (doc.get('version') != 1 or doc.get('link') != link or not isinstance(doc.get('expected'), dict)
            or doc.get('source_id') != (doc['expected']['local'].get('apple') or {}).get('source_id')):
        return False, '分类首次缺失证据不完整，已保留本地，请核对'
    if not _verified(sync, kind, link, doc['source_id'], inventory):
        return False, 'Apple 账户或分类库存无法确认，已保留本地分类与内容'
    if not confirm:
        elapsed = datetime.fromisoformat(sync.clock().replace('Z', '+00:00')) - datetime.fromisoformat(doc['first_seen'].replace('Z', '+00:00'))
        if not sync._get('sync_deletions', False) or elapsed < timedelta(minutes=1):
            return False, '分类缺失等待复核，本地数据已保留'
    # The fresh source inventory is evidence of category absence, not an item-window miss.
    fresh = sync.bridge.call('reminder_lists' if kind == 'reminders' else 'calendars')
    if not _verified(sync, kind, link, doc['source_id'], fresh):
        return False, '删除前来源状态变化，已保留本地分类与内容'
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE');claim._check(db)
        if (_read(db, key) != doc or _manager(db, link, identity) !=
                {name: doc[name] for name in ('mappings', 'baselines')}):
            raise ValueError('category absence manager evidence changed')
        sending = {**doc, 'phase': 'deleting'}
        db.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(sending, ensure_ascii=False, sort_keys=True), key))
    result = None
    try:
        claim.started('owner_claiming')
        result = service.apple_remove_category_if_unchanged(doc['expected'])
        claim.started('owner_committed')
        with sync._connect() as db:
            db.execute('BEGIN IMMEDIATE');claim._check(db)
            if (_read(db, key) != sending or _manager(db, link, identity) !=
                    {name: doc[name] for name in ('mappings', 'baselines')}):
                raise ValueError('category absence manager changed after owner commit')
            db.execute(f"UPDATE mappings SET state='remote_deleted',conflict='' WHERE {identity}=?", (link['apple_id'],))
            db.execute('DELETE FROM category_links WHERE apple_id=?', (link['apple_id'],))
            db.execute('UPDATE settings SET value=? WHERE key=?',
                       (json.dumps({**sending, 'phase': 'done', 'owner_result': result}, ensure_ascii=False, sort_keys=True), key))
        return True, ''
    except Exception as error:
        if result is None and isinstance(error,ValueError) and str(error).startswith('CATEGORY_CHANGED:'):
            claim.owner_rejected()
        with sync._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if _read(db, key) == sending:
                db.execute('UPDATE settings SET value=? WHERE key=?',
                           (json.dumps({**sending, 'phase': 'uncertain', 'owner_result': result}, ensure_ascii=False, sort_keys=True), key))
        raise
