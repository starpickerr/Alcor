"""Merge an explicitly selected offline Stella snapshot into copied native databases."""
import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def rows(db, table):
    return [dict(row) for row in db.execute('SELECT * FROM ' + quote(table))]


def columns(db, table):
    return [row[1] for row in db.execute('PRAGMA table_info(' + quote(table) + ')')]


def insert(db, table, row, counts):
    info = db.execute('PRAGMA table_info(' + quote(table) + ')').fetchall()
    names = [col[1] for col in info if col[1] in row]
    keys = [col[1] for col in info if col[5]]
    if not names or not keys:
        raise ValueError('Destination schema is unsupported: ' + table)
    prior = db.execute('SELECT * FROM ' + quote(table) + ' WHERE ' + ' AND '.join(quote(k) + ' IS ?' for k in keys), [row[k] for k in keys]).fetchone()
    if prior is not None:
        counts[table + ':kept_current'] += 1
        return False
    # Other UNIQUE/CHECK failures abort the transaction instead of silently losing rows.
    db.execute('INSERT INTO ' + quote(table) + '(' + ','.join(map(quote, names)) + ') VALUES (' + ','.join('?' for _ in names) + ')', [row[n] for n in names])
    counts[table + ':inserted'] += 1
    return True


def categories(source, target, table, wanted, counts):
    mapping = {}
    for row in rows(source, table):
        if row['id'] not in wanted:
            continue
        existing = target.execute('SELECT id FROM ' + quote(table) + ' WHERE id=? OR name=? ORDER BY id=? DESC', (row['id'], row['name'], row['id'])).fetchone()
        if existing:
            mapping[row['id']] = existing[0]
        else:
            insert(target, table, row, counts)
            mapping[row['id']] = row['id']
    return mapping


def merge_objects(source, target, table, category_table, counts):
    items = rows(source, table)
    category_map = categories(source, target, category_table, {r.get('category_id') for r in items if r.get('source_type') not in ('apple_calendar', 'apple_reminder')}, counts)
    mapping, added = {}, set()
    for row in items:
        source_type, source_key = row.get('source_type'), row.get('source_key')
        if source_type in ('apple_calendar', 'apple_reminder'):
            counts[table + ':current_apple_owner'] += 1
            continue
        if source_key and target.execute('SELECT 1 FROM source_tombstones WHERE source_key=?', (source_key,)).fetchone():
            counts[table + ':kept_deletion'] += 1
            continue
        existing = target.execute('SELECT id FROM ' + quote(table) + ' WHERE id=? OR (source_type=? AND source_key=?) ORDER BY id=? DESC', (row['id'], source_type, source_key, row['id'])).fetchone()
        if existing:
            mapping[row['id']] = existing[0]
            counts[table + ':kept_current'] += 1
            continue
        if row.get('category_id'):
            row['category_id'] = category_map[row['category_id']]
        insert(target, table, row, counts)
        mapping[row['id']] = row['id']
        added.add(row['id'])
    return mapping, added


def merge_database(source_path, destination_path, kind):
    for path in (source_path, destination_path):
        if path.is_symlink() or not path.is_file():
            raise ValueError('Supply independent, existing offline database copies')
        for suffix in ('-wal', '-shm', '-journal'):
            if Path(str(path) + suffix).exists():
                raise ValueError('Make consistent SQLite backups before migration')
    counts = Counter()
    with closing(sqlite3.connect(source_path.as_uri() + '?mode=ro&immutable=1', uri=True)) as source, closing(sqlite3.connect(destination_path)) as target:
        source.row_factory = target.row_factory = sqlite3.Row
        source.execute('PRAGMA trusted_schema=OFF')
        target.execute('PRAGMA foreign_keys=ON')
        target.execute('PRAGMA defer_foreign_keys=ON')
        with target:
            if kind == 'tasks':
                mapping, added = merge_objects(source, target, 'todos', 'todo_categories', counts)
                # Parent ids are resolved after all rows have entered the transaction.
                for row in rows(source, 'todos'):
                    if row['id'] in added and row.get('parent_id'):
                        target.execute('UPDATE todos SET parent_id=? WHERE id=?', (mapping.get(row['parent_id']), row['id']))
                for table in ('todo_completions', 'todo_checklist_completions', 'todo_dependencies', 'todo_notifications'):
                    for row in rows(source, table):
                        references = [k for k in ('todo_id', 'task_id', 'prerequisite_id') if k in row]
                        if not all(row[k] in mapping for k in references):
                            continue
                        for key in references:
                            row[key] = mapping[row[key]]
                        insert(target, table, row, counts)
            elif kind == 'calendar':
                mapping, _added = merge_objects(source, target, 'calendar_events', 'calendar_categories', counts)
                for row in rows(source, 'calendar_occurrence_exclusions'):
                    if row['series_id'] in mapping:
                        row['series_id'] = mapping[row['series_id']]
                        insert(target, 'calendar_occurrence_exclusions', row, counts)
                for row in rows(source, 'agenda_handoffs'):
                    row['calendar_event_id'] = mapping.get(row['calendar_event_id'])
                    insert(target, 'agenda_handoffs', row, counts)
            elif kind == 'bills':
                # IDs preserve independent but identical purchases. Unknown secondary collisions fail.
                for table in ('financial_events', 'planned_payments', 'ledger_entries', 'period_statements', 'period_statement_items', 'plugin_operation_receipts'):
                    for row in rows(source, table):
                        insert(target, table, row, counts)
            elif kind == 'briefing':
                for table in ('source_items', 'email_messages', 'briefings', 'briefing_items', 'filter_rules', 'briefing_candidates', 'briefing_revisions', 'briefing_mail', 'briefing_entities', 'briefing_entity_items', 'briefing_actions', 'briefing_action_rules', 'briefing_preferences'):
                    for row in rows(source, table):
                        insert(target, table, row, counts)
                # Old cursors, pending deliveries and outboxes remain historical, never replayed.
            if kind in ('tasks', 'calendar'):
                for table in ('source_tombstones', 'briefing_applied'):
                    for row in rows(source, table):
                        insert(target, table, row, counts)
            if target.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Migration would leave broken relationships')
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Destination integrity check failed')
    return dict(counts)


def convert_affairs(source_path, target_path):
    counts = Counter()
    with closing(sqlite3.connect(source_path)) as source, closing(sqlite3.connect(target_path)) as target:
        source.row_factory = target.row_factory = sqlite3.Row
        with target:
            for old in rows(source, 'calendar_affairs'):
                if old.get('source_key') and target.execute('SELECT 1 FROM todos WHERE source_type=? AND source_key=?', (old.get('source_type'), old['source_key'])).fetchone():
                    counts['todos:already_migrated_affair'] += 1
                    continue
                if old.get('source_key') and target.execute('SELECT 1 FROM source_tombstones WHERE source_key=?', (old['source_key'],)).fetchone():
                    counts['todos:kept_deletion'] += 1
                    continue
                row = {key: value for key, value in old.items() if key in columns(target, 'todos')}
                row.update(id='legacy-affair-' + old['id'], status='completed' if old['completed_at'] else 'todo')
                insert(target, 'todos', row, counts)
    return dict(counts)


def refresh_statements(destination):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages/stella-secretary'))
    from business.bills.domain.store import BillStore
    from business.bills.domain.service import BillsService
    service = BillsService(BillStore(destination))
    periods = {(row['occurred_at'][:7], row['currency']) for row in service.store.list_ledgers()}
    for month, currency in sorted(periods):
        service._refresh_periods(month + '-01T00:00:00Z', currency)
    return len(periods)


def export_history(workspace, destination):
    with closing(sqlite3.connect((workspace / 'memory/long-term/index.db').as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        db.row_factory = sqlite3.Row
        history = []
        for session in rows(db, 'sessions'):
            session['messages'] = [dict(row) for row in db.execute('SELECT * FROM messages WHERE session_id=? ORDER BY seq', (session['session_id'],))]
            history.append(session)
    destination.write_text(json.dumps(history, ensure_ascii=False))
    destination.chmod(0o600)
    return {'sessions': len(history), 'messages': sum(len(s['messages']) for s in history)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True, help='Existing offline copies of native business databases')
    parser.add_argument('--confirm-offline-copy', action='store_true', required=True)
    args = parser.parse_args()
    source, target = args.snapshot.resolve(), args.destination.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('Source and destination must be separate offline directories')
    report = {}
    for legacy, native, kind in [('todos/todos.db', 'tasks.sqlite3', 'tasks'), ('calendar/calendar.db', 'calendar.sqlite3', 'calendar'), ('bills/bills.db', 'bills.sqlite3', 'bills'), ('briefing/briefing.db', 'briefing.sqlite3', 'briefing')]:
        report[kind] = merge_database(source / legacy, target / native, kind)
    report['statement_periods_rebuilt'] = refresh_statements(target / 'bills.sqlite3')
    report['affairs'] = convert_affairs(source / 'calendar/calendar.db', target / 'tasks.sqlite3')
    report['history_export'] = export_history(source, target.parent / 'legacy-history.json')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
