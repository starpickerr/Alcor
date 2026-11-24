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
