"""Private Apple peer CAS, not an approval provider or public business method."""
import json
from business.object_generations import generations_for


def _capture(owner, db, identity):
    row = db.execute(f'SELECT * FROM {owner.APPLE_ITEMS} WHERE id=?', (identity,)).fetchone()
    if row is None:
        raise ValueError('export item no longer exists')
    row = dict(row)
    ids = [row['category_id']] if row.get('category_id') else []
    categories = []
    for key in ids:
        category = db.execute(f'SELECT * FROM {owner.APPLE_CATEGORIES} WHERE id=?', (key,)).fetchone()
        if category is None:
            raise ValueError('export category no longer exists')
        categories.append(owner._apple_decorate(db, 'category', category))
    decorated = owner._with_progress(db, dict(row)) if owner.APPLE_ITEMS == 'todos' else row
    return {'version': 1, 'local_id': identity, 'generation': generations_for(db, 'item', [identity])[0][1],
            'row': row, 'local': owner._apple_decorate(db, 'item', decorated),
            'categories': categories, 'category_generations': generations_for(db, 'category', ids)}


def _unique_binding(owner, db, expected):
    binding = expected['local'].get('apple')
    if not binding:
        return
    calendar = owner.APPLE_ITEMS == 'calendar_events'
    if calendar:
        from business.calendar_rebind import _instant
        occurrence = _instant((binding.get('native') or {}).get('occurrence_date'))
    for entity, local_id, native_id, group_id in (
            ('item', expected['local_id'], binding.get('apple_id'), binding.get('group_id')),
            ('category', expected['row'].get('category_id'), binding.get('group_id'), None)):
        for other in db.execute('SELECT document FROM apple_bindings WHERE entity=? AND local_id!=?', (entity, local_id)):
            document = json.loads(other[0])
            if (document.get('apple_id') == native_id and (entity == 'category' or document.get('group_id') == group_id)
                    and document.get('source_id') in (None, '', binding.get('source_id'))):
                if calendar and entity == 'item':
                    other_occurrence = _instant((document.get('native') or {}).get('occurrence_date'))
                    if abs((other_occurrence - occurrence).total_seconds()) >= 1:
                        continue
                raise ValueError('native identity has another owner binding')


def capture(owner, identity):
    with owner._connect() as db:
        db.execute('BEGIN')
        value = _capture(owner, db, identity)
        _unique_binding(owner, db, value)
        if value['local'].get('apple_pending') or any(x.get('apple_pending') for x in value['categories']):
            raise ValueError('pending Apple operation requires reconciliation before export')
        return value


def finalize(owner, expected, native, previous):
    with owner._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        return _finalize_checked(owner, db, expected, native, previous)


def _finalize_checked(owner, db, expected, native, previous):
    reminder = owner.APPLE_ITEMS == 'todos'
    native_key, group_key = ('reminder_id', 'list_id') if reminder else ('event_id', 'calendar_id')
    if not isinstance(expected, dict) or expected.get('version') != 1 or not isinstance(native, dict):
        raise ValueError('invalid export snapshot')
    if any(not isinstance(native.get(k), str) or not native[k] for k in (native_key, group_key, 'source_id')):
        raise ValueError('export native identity incomplete')
    if _capture(owner, db, expected['local_id']) != expected:
        raise ValueError('owner changed after approved native write; reconciliation required')
    binding = expected['local'].get('apple')
    if previous is None:
        if binding:
            raise ValueError('export create target already bound')
    elif (not binding or binding.get('apple_id') != previous.get(native_key)
          or binding.get('group_id') != previous.get(group_key)
          or binding.get('source_id') not in (None, '', previous.get('source_id'))
          or any(native.get(k) != previous.get(k) for k in (native_key, group_key, 'source_id'))):
        raise ValueError('export update identity changed; reconciliation required')
    metadata = {'url': native.get('url') or '', 'alarms': native.get('alarms', []),
                'floating': bool(native.get('floating')), 'attendees': native.get('attendees', []),
                'native_snapshot': native, 'native_recurrence': native.get('recurrence', {}),
                'native_series_key': native.get('series_key') or native.get('external_id')}
    if reminder:
        metadata['native_managed'] = True
    owner._apple_fields(db, expected['local_id'], metadata, True)
    binding = {'apple_id': native[native_key], 'group_id': native[group_key], 'source_id': native['source_id'],
               'can_delete': bool(native.get('writable')), 'can_modify': bool(native.get('writable')),
               'issues': [], 'native': native, 'series_key': native.get('external_id'), 'floating': bool(native.get('floating'))}
    db.execute('INSERT OR REPLACE INTO apple_bindings VALUES (?,?,?)', ('item', expected['local_id'], json.dumps(binding, ensure_ascii=False)))
    return _capture(owner, db, expected['local_id'])['local']
