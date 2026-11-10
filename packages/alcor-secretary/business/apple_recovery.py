"""Capture only the local rows affected by an inbound Apple deletion."""
import json
import uuid
from datetime import datetime, timezone


def _rows(db, table, key, values):
    # Chunk input to avoid SQLite variable limits; use rowid only to de-duplicate.
    found = {}
    for start in range(0, len(values), 400):
        part = values[start:start + 400]
        placeholders = ','.join('?' for _ in part)
        for row in db.execute(f'SELECT rowid AS _recovery_rowid, * FROM {table} WHERE {key} IN ({placeholders})', part):
            item = dict(row)
            found[item.pop('_recovery_rowid')] = item
    return list(found.values())


def capture_inbound_delete(db, domain, entity, identity):
    if domain not in ('tasks', 'calendar') or entity not in ('item', 'category'):
        raise ValueError('invalid inbound recovery target')
    if domain == 'tasks':
        if entity == 'item':
            roots = [identity]
        else:
            roots = [row['id'] for row in db.execute('SELECT id FROM todos WHERE category_id=?', (identity,))]
        ids = set()
        for root in roots:
            ids.update(row['id'] for row in db.execute('''WITH RECURSIVE subtree(id) AS (
                SELECT id FROM todos WHERE id=? UNION SELECT t.id FROM todos t JOIN subtree s ON t.parent_id=s.id)
                SELECT id FROM subtree''', (root,)))
        ids = sorted(ids)
        main = _rows(db, 'todos', 'id', ids)
        category_ids = sorted({row['category_id'] for row in main if row.get('category_id')} | ({identity} if entity == 'category' else set()))
        category = _rows(db, 'todo_categories', 'id', category_ids)
        if not main and not category:
            return None
        tables = {'todos': main, 'todo_categories': category}
        for name in ('todo_completions', 'todo_checklist_completions', 'todo_notifications', 'reminder_occurrences', 'todo_scheduler_links'):
            tables[name] = _rows(db, name, 'todo_id', ids)
        deps = _rows(db, 'todo_dependencies', 'task_id', ids) + _rows(db, 'todo_dependencies', 'prerequisite_id', ids)
        tables['todo_dependencies'] = list({(row['task_id'], row['prerequisite_id']): row for row in deps}.values())
        occurrences = [row['occurrence_id'] for row in tables['reminder_occurrences']]
        tables['reminder_deliveries'] = _rows(db, 'reminder_deliveries', 'occurrence_id', occurrences)
        tables['reminder_route_deliveries'] = _rows(db, 'reminder_route_deliveries', 'delivery_id', [row['delivery_id'] for row in tables['reminder_deliveries']])
    else:
        main = _rows(db, 'calendar_events', 'id', [identity]) if entity == 'item' else _rows(db, 'calendar_events', 'category_id', [identity])
        ids = [row['id'] for row in main]
        category_ids = sorted({row['category_id'] for row in main if row.get('category_id')} | ({identity} if entity == 'category' else set()))
        category = _rows(db, 'calendar_categories', 'id', category_ids)
        if not main and not category:
            return None
        tables = {'calendar_events': main, 'calendar_categories': category,
                  'calendar_occurrence_exclusions': _rows(db, 'calendar_occurrence_exclusions', 'series_id', ids),
                  'agenda_handoffs': _rows(db, 'agenda_handoffs', 'calendar_event_id', ids)}
    all_ids = sorted(set(ids + ([identity] if entity == 'category' else [])))
    bindings = _rows(db, 'apple_bindings', 'local_id', all_ids)
    operations = _rows(db, 'apple_operations', 'local_id', all_ids)
    tables['apple_bindings'] = [row for row in bindings if (row['entity'] == 'item' and row['local_id'] in ids) or (entity == 'category' and row['entity'] == 'category' and row['local_id'] == identity)]
    tables['apple_operations'] = [row for row in operations if (row['entity'] == 'item' and row['local_id'] in ids) or (entity == 'category' and row['entity'] == 'category' and row['local_id'] == identity)]
    tables['apple_completion_history'] = _rows(db, 'apple_completion_history', 'local_id', ids)
    source_keys = [row['source_key'] for row in main if row.get('source_key')]
    tables['source_tombstones'] = _rows(db, 'source_tombstones', 'source_key', source_keys)
    # Serialize before any deletion; unsupported data aborts the same transaction.
    document = json.dumps({'version': 1, 'domain': domain, 'entity': entity, 'target_id': identity,
                           'source_keys': source_keys, 'tables': tables}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    db.execute('''CREATE TABLE IF NOT EXISTS apple_inbound_recovery (
        id TEXT PRIMARY KEY, domain TEXT NOT NULL, entity TEXT NOT NULL, target_id TEXT NOT NULL,
        created_at TEXT NOT NULL, status TEXT NOT NULL, document TEXT NOT NULL, result TEXT)''')
    recovery_id = str(uuid.uuid4())
    db.execute('INSERT INTO apple_inbound_recovery (id,domain,entity,target_id,created_at,status,document) VALUES (?,?,?,?,?,?,?)',
               (recovery_id, domain, entity, identity, datetime.now(timezone.utc).isoformat(), 'available', document))
    return recovery_id


def list_inbound_recoveries(db, domain):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='apple_inbound_recovery'").fetchone():
        return []
    records = []
    for row in db.execute("SELECT id,domain,entity,target_id,created_at,status,document FROM apple_inbound_recovery WHERE domain=? AND status IN ('available','restored','operation_completed') ORDER BY created_at DESC", (domain,)):
        document = json.loads(row['document']); tables = document.get('tables') or {}
        table = ('todo_categories' if domain == 'tasks' else 'calendar_categories') if row['entity'] == 'category' else ('todos' if domain == 'tasks' else 'calendar_events')
        target = next((item for item in tables.get(table, []) if item.get('id') == row['target_id']), {})
        title = str(target.get('name') if row['entity'] == 'category' else target.get('title') or '')[:200]
        records.append({key: row[key] for key in ('id','domain','entity','target_id','created_at','status')} | {'title': title, 'status': 'available' if row['status'] == 'operation_completed' else row['status']})
    return records


def restore_inbound_recovery(db, domain, recovery_id):
    """Restore as new, disconnected local copies; leave full originals in the archive."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='apple_inbound_recovery'").fetchone():
        raise ValueError('recovery record not found')
    row = db.execute('SELECT * FROM apple_inbound_recovery WHERE id=? AND domain=?', (recovery_id, domain)).fetchone()
    if not row:
        raise ValueError('recovery record not found')
    if row['status'] == 'restored':
        result = json.loads(row['result'])
        table = 'todos' if domain == 'tasks' else 'calendar_events'
        result['replayed'] = True
        category_table = 'todo_categories' if domain == 'tasks' else 'calendar_categories'
        result['currentConflict'] = (any(not db.execute(f'SELECT 1 FROM {table} WHERE id=?', (identity,)).fetchone() for identity in result['ids'])
                                     or any(not db.execute(f'SELECT 1 FROM {category_table} WHERE id=?', (identity,)).fetchone() for identity in result.get('categoryIds', [])))
        return result
    if row['status'] not in ('available', 'operation_completed'):
        raise ValueError('recovery state conflict')
    document = json.loads(row['document'])
    if document.get('version') != 1 or document.get('domain') != domain or not isinstance(document.get('tables'), dict):
        raise ValueError('unsupported recovery record')
    tables = document['tables']
    table = 'todos' if domain == 'tasks' else 'calendar_events'
    category_table = 'todo_categories' if domain == 'tasks' else 'calendar_categories'
    originals = tables.get(table)
    if not isinstance(originals, list):
        raise ValueError('invalid recovery records')
    categories = {item['id']: dict(item) for item in db.execute(f'SELECT id,name FROM {category_table}')}
    ids = {item['id']: str(uuid.uuid4()) for item in originals}
    if len(ids) != len(originals): raise ValueError('duplicate recovery identity')
    conflicts = []
    category_ids = []
    category_map = {}
    columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
    def insert(name, record):
        names = list(record)
        if set(names) != {column['name'] for column in db.execute(f'PRAGMA table_info({name})')}:
            raise ValueError('recovery schema changed; review before restoring')
        db.execute(f"INSERT INTO {name} ({','.join(names)}) VALUES ({','.join('?' for _ in names)})", [record[key] for key in names])
    if document.get('entity') == 'category':
        original = next((item for item in tables.get(category_table, []) if item.get('id') == document.get('target_id')), None)
        if not original or set(original) != {item['name'] for item in db.execute(f'PRAGMA table_info({category_table})')}:
            raise ValueError('recovery category schema changed')
        category = dict(original); previous = category['id']; category['id'] = str(uuid.uuid4())
        names = {item['name'] for item in categories.values()}
        if category['name'] in names:
            base = category['name']; index = 1
            while category['name'] in names:
                category['name'] = f'{base}（恢复 {index}）'; index += 1
            conflicts.append({'kind': 'category_renamed', 'originalId': previous})
        insert(category_table, category)
        db.execute('CREATE TABLE IF NOT EXISTS apple_inbound_local_only (entity TEXT NOT NULL, local_id TEXT NOT NULL, PRIMARY KEY(entity,local_id))')
        db.execute('INSERT INTO apple_inbound_local_only VALUES (?,?)', ('category', category['id']))
        categories[category['id']] = {'id': category['id'], 'name': category['name']}
        category_map[previous] = category['id']; category_ids.append(category['id'])
    if originals and not categories:
        raise ValueError('no local category available for recovery')
    fallback = category_ids[0] if category_ids else next(iter(categories), None)
    for original in originals:
        if set(original) != columns:
            raise ValueError('recovery schema changed; review before restoring')
        item = dict(original); old = item['id']; item['id'] = ids[old]
        item['source_type'] = 'apple_recovered'; item['source_key'] = None
        category = item.get('category_id')
        if category in category_map:
            item['category_id'] = category_map[category]
        elif category not in categories:
            item['category_id'] = fallback
            conflicts.append({'kind': 'category_unavailable', 'originalId': old})
        if domain == 'tasks':
            if item.get('category_id') in categories:
                item['list_name'] = categories[item['category_id']]['name']
            parent = item.get('parent_id')
            if parent in ids:
                item['parent_id'] = ids[parent]
            elif parent:
                item['parent_id'] = None
                conflicts.append({'kind': 'parent_unavailable', 'originalId': old})
            # Never re-arm a reminder without an explicit new user action.
            if item.get('remind_at'):
                item['remind_at'] = None
                conflicts.append({'kind': 'reminder_paused', 'originalId': old})
        insert(table, item)
    if domain == 'tasks':
        for name in ('todo_completions', 'todo_checklist_completions', 'todo_notifications'):
            for original in tables.get(name, []):
                if original.get('todo_id') not in ids: raise ValueError('invalid recovery reference')
                item = dict(original); item['todo_id'] = ids[item['todo_id']]; insert(name, item)
        for original in tables.get('todo_dependencies', []):
            item = dict(original)
            if item['task_id'] in ids and item['prerequisite_id'] in ids:
                item['task_id'] = ids[item['task_id']]; item['prerequisite_id'] = ids[item['prerequisite_id']]
                insert('todo_dependencies', item)
            else: conflicts.append({'kind': 'external_dependency_not_relinked'})
        occurrences = {}
        for original in tables.get('reminder_occurrences', []):
            item = dict(original)
            if item.get('todo_id') not in ids: raise ValueError('invalid recovery reference')
            occurrences[item['occurrence_id']] = str(uuid.uuid4()); item['occurrence_id'] = occurrences[item['occurrence_id']]
            item['todo_id'] = ids[item['todo_id']]; item['state'] = 'cancelled'; insert('reminder_occurrences', item)
        deliveries = {}
        for original in tables.get('reminder_deliveries', []):
            item = dict(original)
            if item.get('occurrence_id') not in occurrences: raise ValueError('invalid recovery reference')
            deliveries[item['delivery_id']] = str(uuid.uuid4()); item['delivery_id'] = deliveries[item['delivery_id']]
            item['occurrence_id'] = occurrences[item['occurrence_id']]; item['claim_id'] = None; item['lease_until'] = None
            if item['status'] not in ('shown','accepted','unknown'): item['status'] = 'cancelled'
            insert('reminder_deliveries', item)
        for original in tables.get('reminder_route_deliveries', []):
            item = dict(original)
            if item.get('delivery_id') not in deliveries: raise ValueError('invalid recovery reference')
            item['delivery_id'] = deliveries[item['delivery_id']]
            if item['status'] not in ('shown','accepted','unknown'): item['status'] = 'cancelled'
            insert('reminder_route_deliveries', item)
    else:
        for original in tables.get('calendar_occurrence_exclusions', []):
            item = dict(original)
            if item.get('series_id') not in ids: raise ValueError('invalid recovery reference')
            item['series_id'] = ids[item['series_id']]; insert('calendar_occurrence_exclusions', item)
        if tables.get('agenda_handoffs'): conflicts.append({'kind': 'handoff_not_relinked'})
    for original in tables.get('apple_completion_history', []):
        item = dict(original)
        if item.get('local_id') not in ids: raise ValueError('invalid recovery reference')
        item['local_id'] = ids[item['local_id']]
        insert('apple_completion_history', item)
    if tables.get('apple_bindings') or tables.get('apple_operations'):
        conflicts.append({'kind': 'apple_disconnected'})
    result = {'restored': True, 'replayed': False, 'ids': list(ids.values()), 'categoryIds': category_ids, 'conflicts': conflicts, 'sync': 'local_only'}
    db.execute('UPDATE apple_inbound_recovery SET status=?,result=? WHERE id=? AND status=?',
               ('restored', json.dumps(result, ensure_ascii=False, separators=(',', ':')), recovery_id, row['status']))
    return result
