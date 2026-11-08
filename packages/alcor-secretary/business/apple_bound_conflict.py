"""A bound-item choice and its immutable receipt commit in one owner transaction."""
import json
from business.apple_export import _capture, _finalize_checked, _unique_binding
from business.object_generations import generations_for
from business.security_receipts import read_receipt, save_receipt, validate_operation_id

METHOD = 'resolve_bound_apple_conflict'


def _arguments(owner, item, choice, native):
    if choice not in ('keep_apple', 'keep_alcor') or not isinstance(item, dict) or not isinstance(native, dict):
        raise ValueError('invalid bound conflict choice')
    expected = item['owner_snapshot']
    group, key = ('list_id', 'reminder_id') if owner.APPLE_ITEMS == 'todos' else ('calendar_id', 'event_id')
    previous = item['native_snapshot']
    binding = expected['local'].get('apple') or {}
    category = next((row for row in expected['categories'] if row['id'] == expected['row']['category_id']), {})
    container = category.get('apple') or {}
    if (item['local_id'] != expected['local_id'] or item['action'] != ('local_update' if choice == 'keep_apple' else 'update')
            or not expected.get('generation') or not previous
            or any(not isinstance(native.get(k), str) or not native[k] or native[k] != previous.get(k) for k in (group, key, 'source_id'))
            or binding.get('apple_id') != native[key] or binding.get('group_id') != native[group]
            or binding.get('source_id') != native['source_id'] or container.get('apple_id') != native[group]
            or container.get('source_id') != native['source_id'] or container.get('planned')
            or expected['local'].get('apple_pending') or category.get('apple_pending')
            or choice == 'keep_apple' and (native != previous or item['wire_payload'].get('category_id', expected['row']['category_id']) != expected['row']['category_id'])):
        raise ValueError('INBOUND_CHANGED: conflict binding or target changed')
    return {'item': item, 'choice': choice, 'native': native}


def _result(db, operation_id, arguments):
    receipt = read_receipt(db, operation_id, METHOD, arguments)
    if not receipt:
        return None
    row = db.execute('SELECT result FROM alcor_bound_conflict_results WHERE operation_id=?', (operation_id,)).fetchone()
    if not row:
        raise ValueError('bound conflict committed result is unavailable')
    # The committed snapshot is the baseline. Reading the current item here would hide later edits.
    result = json.loads(row[0])
    if result['local']['id'] != receipt['targetId'] or result['native'] != arguments['native']:
        raise ValueError('bound conflict receipt target changed')
    return {**result, 'receipt': receipt}


def verify(owner, operation_id, item, choice, native):
    arguments = _arguments(owner, item, choice, native)
    with owner._connect() as db:
        db.execute('BEGIN')
        return _result(db, operation_id, arguments)


def commit(owner, operation_id, item, choice, native):
    validate_operation_id(operation_id)
    arguments = _arguments(owner, item, choice, native)
    with owner._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        result = _result(db, operation_id, arguments)
        if result is not None:
            return result
        expected = item['owner_snapshot']
        _unique_binding(owner, db, expected)
        if _capture(owner, db, item['local_id']) != expected:
            raise ValueError('INBOUND_CHANGED: conflict owner changed after selection')
        if choice == 'keep_alcor':
            local = _finalize_checked(owner, db, expected, native, item['native_snapshot'])
        elif owner.APPLE_ITEMS == 'todos':
            from business.reminder_sync import _verify, _apply_checked
            _verify(owner, db, item['owner_inbound_snapshot'])
            local = _apply_checked(owner, db, item['owner_inbound_snapshot'], item['wire_payload'], native, False, None)
        else:
            local = owner._apply_calendar_inbound(db, item['owner_inbound_snapshot'], item['wire_payload'], native)
        result = {'local': local, 'generation': generations_for(db, 'item', [item['local_id']])[0][1], 'native': native}
        db.execute('CREATE TABLE IF NOT EXISTS alcor_bound_conflict_results (operation_id TEXT PRIMARY KEY, result TEXT NOT NULL)')
        db.execute('INSERT INTO alcor_bound_conflict_results VALUES (?,?)', (operation_id, json.dumps(result, ensure_ascii=False)))
        save_receipt(db, operation_id, METHOD, arguments, item['local_id'])
        return _result(db, operation_id, arguments)
