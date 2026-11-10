"""Restore one missing Apple-linked item in its owner's transaction, never in Apple."""
import json
from datetime import datetime, timezone

from business.apple_recovery import _rows
from business.object_generations import generations_for
from business.security_receipts import operation_digest, read_receipt, save_receipt, validate_operation_id


METHOD = 'restore_missing_apple_item'


def _changed(reason):
    raise ValueError('INBOUND_CHANGED: ' + reason)


def _state(owner, db, identity, category_id, source_key, native, *, strip_receipt=False):
    reminder = owner.APPLE_ITEMS == 'todos'
    group, native_id = ('list_id', 'reminder_id') if reminder else ('calendar_id', 'event_id')
    row = db.execute(f'SELECT * FROM {owner.APPLE_ITEMS} WHERE id=?', (identity,)).fetchone()
    category = db.execute(f'SELECT * FROM {owner.APPLE_CATEGORIES} WHERE id=?', (category_id,)).fetchone()
    if category is None:
        _changed('restore category is unavailable')
    binding = owner._apple_binding(db, 'item', identity)
    if binding and strip_receipt:
        binding.pop('_restore_receipt', None)
    others = []
    for other in db.execute("SELECT local_id,document FROM apple_bindings WHERE entity='item' AND local_id!=? ORDER BY local_id", (identity,)):
        document = json.loads(other['document'])
        if (document.get('apple_id') == native[native_id] and document.get('group_id') == native[group]
                and document.get('source_id') in (None, '', native['source_id'])):
            others.append(dict(other))
    related = {'operations': _rows(db, 'apple_operations', 'local_id', [identity]),
               'history': _rows(db, 'apple_completion_history', 'local_id', [identity])}
    if reminder:
        for table in ('todo_completions', 'todo_checklist_completions', 'todo_notifications', 'reminder_occurrences', 'todo_scheduler_links'):
            related[table] = _rows(db, table, 'todo_id', [identity])
        related['children'] = _rows(db, 'todos', 'parent_id', [identity])
        related['dependencies'] = [dict(item) for item in db.execute('SELECT * FROM todo_dependencies WHERE task_id=? OR prerequisite_id=?', (identity, identity))]
        related['deliveries'] = _rows(db, 'reminder_deliveries', 'occurrence_id', [item['occurrence_id'] for item in related['reminder_occurrences']])
        related['routes'] = _rows(db, 'reminder_route_deliveries', 'delivery_id', [item['delivery_id'] for item in related['deliveries']])
    else:
        related['exclusions'] = _rows(db, 'calendar_occurrence_exclusions', 'series_id', [identity])
        related['handoffs'] = _rows(db, 'agenda_handoffs', 'calendar_event_id', [identity])
    for rows in related.values():
        rows.sort(key=lambda value: json.dumps(value, sort_keys=True))
    return {'row': dict(row) if row else None, 'binding': binding,
            'generations': _rows(db, 'alcor_object_generations', 'object_id', [identity]),
            'category': owner._apple_decorate(db, 'category', category),
            'category_generation': generations_for(db, 'category', [category_id])[0][1],
            'related': related, 'other_bindings': others,
            'source_rows': _rows(db, owner.APPLE_ITEMS, 'source_key', [source_key]) if source_key else [],
            'tombstones': _rows(db, 'source_tombstones', 'source_key', [source_key]) if source_key else []}


def _validate(owner, identity, payload, category_id, native, source_key):
    reminder = owner.APPLE_ITEMS == 'todos'
    keys = ('reminder_id', 'list_id', 'source_id') if reminder else ('event_id', 'calendar_id', 'source_id')
    if (not isinstance(identity, str) or not identity or not isinstance(category_id, str) or not category_id
            or not isinstance(payload, dict) or not isinstance(native, dict)
            or any(not isinstance(native.get(key), str) or not native[key] for key in keys)
            or source_key is not None and (not isinstance(source_key, str) or not source_key)
            or payload.get('category_id', category_id) != category_id or payload.get('id', identity) != identity
            or payload.get('parent_id') or payload.get('reminder_delivery')):
        _changed('invalid missing-item restore snapshot')
    fields = {**payload, 'category_id': category_id}
    row = owner._validate_todo(fields) if reminder else owner._validate_event(fields)
    if reminder:
        completed = native.get('completed', False)
        if type(completed) is not bool or payload.get('completed', completed) != completed:
            _changed('reminder completion differs from native snapshot')
        completed_at = native.get('completed_at') if completed else None
        if completed_at:
            instant = datetime.fromisoformat(completed_at.replace('Z', '+00:00'))
            if instant.tzinfo is None:
                _changed('completion timestamp requires timezone')
            completed_at = instant.astimezone(timezone.utc).isoformat()
        row.update(status='completed' if completed else 'todo', completed_at=completed_at)
    return row


def capture(owner, identity, payload, category_id, native, source_key=None):
    _validate(owner, identity, payload, category_id, native, source_key)
    with owner._connect() as db:
        db.execute('BEGIN')
        state = _state(owner, db, identity, category_id, source_key, native)
        if state['row'] is not None:
            _changed('restore target already exists')
        expected = {'version': 1, 'local_id': identity, 'category_id': category_id, 'source_key': source_key,
                    'payload': payload, 'native': native, 'state': state}
        _available(owner, expected)
        return json.loads(json.dumps(expected, ensure_ascii=False))


def _available(owner, expected):
    state, native = expected['state'], expected['native']
    reminder = owner.APPLE_ITEMS == 'todos'
    group, native_id = ('list_id', 'reminder_id') if reminder else ('calendar_id', 'event_id')
    category = state['category'].get('apple') or {}
    binding = state['binding'] or {}
    if (state['row'] is not None or state['source_rows'] or state['other_bindings']
            or category.get('apple_id') != native[group] or category.get('source_id') != native['source_id']
            or category.get('planned') or state['category'].get('apple_pending')
            or any(row['state'] in ('pending', 'sending', 'uncertain') for row in state['related']['operations'])):
        _changed('restore target, category, native claim or operation changed')
    if binding and (binding.get('apple_id') != native[native_id]
                    or binding.get('source_id') not in (None, '', native['source_id'])):
        _changed('old binding belongs to another native identity')


def _arguments(expected):
    if (not isinstance(expected, dict) or set(expected) != {'version', 'local_id', 'category_id', 'source_key', 'payload', 'native', 'state'}
            or expected['version'] != 1 or not isinstance(expected['state'], dict)):
        _changed('missing-item restore capture required')
    return {'expected': expected}


def _current(owner, db, expected, *, strip_receipt=False):
    return _state(owner, db, expected['local_id'], expected['category_id'], expected['source_key'], expected['native'], strip_receipt=strip_receipt)


def _result(owner, db, operation_id, expected, receipt, replayed):
    state = _current(owner, db, expected, strip_receipt=True)
    binding = owner._apple_binding(db, 'item', expected['local_id']) or {}
    stamp = binding.get('_restore_receipt') or {}
    if (not state['row'] or stamp.get('operation_id') != operation_id
            or generations_for(db, 'item', [expected['local_id']])[0][1] != stamp.get('generation')
            or operation_digest(METHOD, state) != stamp.get('snapshot')):
        _changed('restored item or category changed after commit')
    row = state['row']
    local = owner._apple_decorate(db, 'item', owner._with_progress(db, row) if owner.APPLE_ITEMS == 'todos' else row)
    return {'local': local, 'generation': stamp['generation'], 'receipt': receipt, 'replayed': replayed}


def verify(owner, operation_id, expected):
    args = _arguments(expected)
    with owner._connect() as db:
        db.execute('BEGIN')
        receipt = read_receipt(db, operation_id, METHOD, args)
        return _result(owner, db, operation_id, expected, receipt, True) if receipt else None


def _insert(owner, db, expected, row):
    identity, native = expected['local_id'], expected['native']
    reminder = owner.APPLE_ITEMS == 'todos'
    now = datetime.now(timezone.utc).isoformat()
    row.update(id=identity, category_id=expected['category_id'], source_key=expected['source_key'],
               source_type=('apple_reminder' if reminder else 'apple_calendar') if expected['source_key'] else None,
               source_url='', user_modified=0, created_at=now, updated_at=now)
    if reminder:
        row.update(list_name=expected['state']['category']['name'], parent_id=None, sort_order=owner._next_order(db, None))
        columns = 'id title notes due_on due_time timezone priority completed_at repeat_frequency repeat_days repeat_until repeat_interval source_type source_key source_url user_modified created_at updated_at planned_on list_name tags flagged checklist status parent_id sort_order remind_at reminder_delivery category_id'.split()
    else:
        columns = 'id title description start_at end_at all_day location category_id source_type source_key source_url user_modified repeat_frequency repeat_days repeat_until repeat_timezone created_at updated_at'.split()
    db.execute(f"INSERT INTO {owner.APPLE_ITEMS} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [row[key] for key in columns])
    fields = {**expected['payload'], 'native_snapshot': native, 'native_recurrence': native.get('recurrence', {}),
              'native_series_key': native.get('series_key') or native.get('external_id')}
    if reminder:
        fields['native_managed'] = True
        owner._cancel_pending_reminders(db, identity)
        owner._queue_scheduler_link(db, identity, None, row['status'])
    owner._apple_fields(db, identity, fields, True)


def commit(owner, operation_id, expected):
    validate_operation_id(operation_id)
    args = _arguments(expected)
    with owner._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        receipt = read_receipt(db, operation_id, METHOD, args)
        if receipt:
            return _result(owner, db, operation_id, expected, receipt, True)
        if _current(owner, db, expected) != expected['state']:
            _changed('restore target or evidence changed after capture')
        _available(owner, expected)
        row = _validate(owner, expected['local_id'], expected['payload'], expected['category_id'], expected['native'], expected['source_key'])
        _insert(owner, db, expected, row)
        if expected['source_key']:
            db.execute('DELETE FROM source_tombstones WHERE source_key=?', (expected['source_key'],))
        native = expected['native']
        reminder = owner.APPLE_ITEMS == 'todos'
        issues = []
        if (native.get('recurrence') or {}).get('rule_count', 0) > 1:
            issues.append('包含多条原生重复规则，请在 Apple 中编辑')
        rule = native.get('recurrence') or {}
        if reminder and (native.get('alarm_count', 0) > 1 or native.get('alarm_count') and not native.get('remind_at')
                or rule.get('frequency', 'none') not in ('none', 'daily', 'weekly', 'monthly', 'yearly')
                or type(rule.get('interval', 1)) is not int or not 1 <= rule.get('interval', 1) <= 99
                or rule.get('count') or rule.get('frequency') != 'weekly' and rule.get('days')
                or any(rule.get(key) for key in ('month_days', 'months', 'week_numbers', 'year_days', 'set_positions'))):
            issues.append('此提醒包含复杂重复或位置/多重提醒，完整保留；请在 Apple 中编辑')
        binding = {'apple_id': native['reminder_id' if reminder else 'event_id'],
                   'group_id': native['list_id' if reminder else 'calendar_id'], 'source_id': native['source_id'],
                   'can_delete': bool(native.get('writable')), 'can_modify': bool(native.get('writable')) and not issues,
                   'issues': issues, 'native': native, 'series_key': native.get('external_id'), 'floating': native.get('floating', False)}
        db.execute('INSERT OR REPLACE INTO apple_bindings VALUES (?,?,?)', ('item', expected['local_id'], json.dumps(binding, ensure_ascii=False)))
        binding['_restore_receipt'] = {'operation_id': operation_id,
            'generation': generations_for(db, 'item', [expected['local_id']])[0][1],
            'snapshot': operation_digest(METHOD, _current(owner, db, expected, strip_receipt=True))}
        db.execute("UPDATE apple_bindings SET document=? WHERE entity='item' AND local_id=?", (json.dumps(binding, ensure_ascii=False), expected['local_id']))
        save_receipt(db, operation_id, METHOD, args, expected['local_id'])
        return _result(owner, db, operation_id, expected, read_receipt(db, operation_id, METHOD, args), False)
