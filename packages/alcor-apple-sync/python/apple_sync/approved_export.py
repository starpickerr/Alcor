"""Specific export plans and conditional execution over the private host pipe.

Digests and durable records bind data, never grant permission. Each dispatch
requires the live host execution callback. Unknown writes retain WorkClaim.
"""
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from apple_sync.group_scan_claims import WorkClaim
from apple_sync.metadata import metadata
from apple_sync.contracts import locate_remote


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _shape(kind):
    if kind not in ('calendar', 'reminders'):
        raise ValueError('invalid Apple export kind')
    return ('todo_id', 'list_id', 'reminder_id') if kind == 'reminders' else ('alcor_id', 'calendar_id', 'event_id')


def _same_native_item(kind, left, right):
    _, group, native = _shape(kind)
    if left.get(group) != right.get(group) or left.get(native) != right.get(native):
        return False
    if kind == 'reminders':
        return True
    from apple_sync.calendar_rebind import _instant
    # Match EventKit's occurrence precision; missing or timezone-free dates are not proof.
    return abs((_instant(left.get('occurrence_date')) - _instant(right.get('occurrence_date'))).total_seconds()) < 1


def _baseline(sync, kind, identity):
    if kind == 'reminders':
        return sync._reminder_baselines(identity)
    return {key: sync._get(key + identity) for key in ('item-metadata:', 'calendar-binding-generation:')}


def _target(sync, kind, group):
    inventory = sync.bridge.call('reminder_lists' if kind == 'reminders' else 'calendars')
    rows = [row for row in inventory.get('lists' if kind == 'reminders' else 'calendars', []) if row.get('id') == group]
    if len(rows) != 1 or not rows[0].get('writable') or not rows[0].get('source_id') or rows[0]['source_id'] not in inventory.get('source_ids', []):
        raise ValueError('export destination unavailable or ambiguous')
    return rows[0]


def _hashes(kind, local, remote, mapping):
    if kind == 'reminders':
        from apple_sync.reminders import _hash
        return _hash(local), _hash(remote, remote=True)
    from apple_sync.calendar import _fingerprint
    return _fingerprint(local, series=mapping['origin'] == 'alcor'), _fingerprint(remote, remote=True, series=mapping['origin'] == 'alcor')


def prepare(sync, kind, method, arguments, operation_id):
    if method == 'resolve_conflict':
        from apple_sync.bound_conflict import prepare as prepare_conflict
        return prepare_conflict(sync, kind, arguments, operation_id)
    local_key, group_key, native_key = _shape(kind)
    if not isinstance(operation_id, str) or len(operation_id) != 64 or any(c not in '0123456789abcdef' for c in operation_id):
        raise ValueError('invalid export operation')
    args = dict(arguments)
    if args.pop('kind', kind) != kind or args.pop('direction', None) != 'export':
        raise ValueError('invalid export direction')
    owner = sync.todos if kind == 'reminders' else sync.calendar
    with sync._lock:
        status = sync.bridge.call('reminder_status' if kind == 'reminders' else 'status')
        if status.get('authorization') != 'full_access' or status.get('approved_export_version') != 1:
            raise PermissionError('Apple helper lacks the conditional approved-export contract')
        if method == 'one_time':
            if set(args) != {group_key, 'item_ids'}:
                raise ValueError('unexpected one-time export fields')
            chosen = [(row, args[group_key]) for row in sync._one_time_items('export', args[group_key], args['item_ids'])]
        elif method == 'transfer_batch':
            allowed = {'selections', 'start', 'end'} | ({'include_undated', 'include_completed'} if kind == 'reminders' else set())
            if set(args) - allowed or not {'selections', 'start', 'end'} <= set(args):
                raise ValueError('unexpected batch export fields')
            if kind == 'reminders':
                if any(type(args.get(key, False)) is not bool for key in ('include_undated', 'include_completed')):
                    raise ValueError('invalid reminder batch filters')
                chosen, failed = sync._batch_items('export', args['selections'], args['start'], args['end'], args.get('include_undated', False), args.get('include_completed', False))
            else:
                chosen, failed, _ = sync._batch_events('export', args['selections'], args['start'], args['end'])
            if failed:
                raise ValueError('export selection incomplete')
        else:
            raise ValueError('unknown approved export method')
        if len(chosen) > 500 or len({row['id'] for row, _ in chosen}) != len(chosen):
            raise ValueError('export requires at most 500 distinct local items')
        plan = {'version': 1, 'operation_id': operation_id, 'kind': kind, 'mode': 'one_time' if method == 'one_time' else 'batch',
                'method': method, 'arguments': {'kind': kind, 'direction': 'export', **args},
                'links_snapshot': sync._stored_links(), 'items': [], 'skipped': 0}
        maps = sync._maps()
        for local, group in chosen:
            mapping = next((row for row in maps if row[local_key] == local['id']), None)
            if mapping and method == 'transfer_batch':
                plan['skipped'] += 1
                continue
            expected = owner.apple_capture_approved_export(local['id'])
            if expected['local'] != local:
                raise ValueError('owner changed during export preview')
            target = _target(sync, kind, group)
            native = locate_remote(sync, mapping, kind) if mapping else None
            baseline = _baseline(sync, kind, local['id'])
            if mapping:
                if mapping['state'] != 'active' or not native or not native.get('writable') or native.get(group_key) != group or native.get('source_id') != target['source_id']:
                    raise ValueError('mapped export target requires conflict review')
                if kind == 'reminders':
                    from apple_sync.reminders import _supported
                    if not _supported(native):
                        raise ValueError('unsupported reminder requires conflict review')
                local_hash, remote_hash = _hashes(kind, local, native, mapping)
                old = baseline['item-metadata:']
                if not old or remote_hash != mapping['remote_hash'] or metadata(native, True) != old[1]:
                    raise ValueError('Apple content changed; resolve conflict before export')
                if local_hash == mapping['local_hash'] and metadata(local) == old[0]:
                    plan['skipped'] += 1
                    continue
            elif local.get('apple'):
                raise ValueError('unmapped owner binding requires reconciliation')
            if kind == 'calendar':
                anchor = datetime.fromisoformat((native or local)['start_at'].replace('Z', '+00:00'))
                window = ((anchor - timedelta(days=2)).isoformat(), (anchor + timedelta(days=2)).isoformat())
                payload = sync._write_payload(local, mapping, calendar_id=group, window=window, include_receipt=False)
            else:
                payload = sync._write_payload(local, mapping, list_id=group, include_receipt=False)
            plan['items'].append({'local_id': local['id'], 'source_id': target['source_id'], 'group_id': group,
                                  'action': 'update' if mapping else 'create', 'wire_payload': payload,
                                  'owner_snapshot': expected, 'mapping_snapshot': mapping, 'native_snapshot': native,
                                  'destination_snapshot': target, 'baseline_snapshot': baseline})
        plan['plan_fingerprint'] = digest(plan)
        return plan


def _recheck(sync, kind, item, links):
    local_key, _, _ = _shape(kind)
    owner = sync.todos if kind == 'reminders' else sync.calendar
    current = next((row for row in sync._maps() if row[local_key] == item['local_id']), None)
    if (current != item['mapping_snapshot'] or _baseline(sync, kind, item['local_id']) != item['baseline_snapshot']
            or sync._stored_links() != links or owner.apple_capture_approved_export(item['local_id']) != item['owner_snapshot']
            or _target(sync, kind, item['group_id']) != item['destination_snapshot']
            or (locate_remote(sync, current, kind) if current else None) != item['native_snapshot']):
        raise ValueError('approved export target, content, or incarnation changed')


def _commit_mapping(sync, kind, item, local, native, claim):
    from apple_sync.calendar import _source_key as calendar_key, _fingerprint
    from apple_sync.reminders import _source_key as reminder_key, _hash
    local_key, group_key, native_key = _shape(kind)
    before = item['mapping_snapshot']
    origin = before['origin'] if before else 'alcor'
    mapping = {local_key: local['id'], 'source_key': before['source_key'] if before else (reminder_key if kind == 'reminders' else calendar_key)(native),
               group_key: native[group_key], native_key: native[native_key], 'external_id': native.get('external_id') or '',
               'origin': origin, 'state': 'active', 'conflict': '', 'missing_since': '', 'updated_at': sync.clock()}
    if kind == 'calendar':
        mapping.update(occurrence_date=native.get('occurrence_date') or native['start_at'],
                       local_hash=_fingerprint(local, series=origin == 'alcor'), remote_hash=_fingerprint(native, remote=True, series=origin == 'alcor'))
    else:
        mapping.update(local_hash=_hash(local), remote_hash=_hash(native, remote=True))
    if before and 'id' in before:
        mapping['id'] = before['id']
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE'); claim._check(db)
        rows = [dict(row) for row in db.execute(f'SELECT * FROM mappings WHERE {local_key}=?', (item['local_id'],))]
        if rows != ([before] if before else []):
            raise ValueError('export mapping changed after native save; reconciliation required')
        for prefix, expected in item['baseline_snapshot'].items():
            row = db.execute('SELECT value FROM settings WHERE key=?', (prefix + item['local_id'],)).fetchone()
            if (json.loads(row[0]) if row else None) != expected:
                raise ValueError('export baseline changed after native save')
        others = db.execute(f'SELECT * FROM mappings WHERE {group_key}=? AND {native_key}=? AND {local_key}!=?',
                            (native[group_key], native[native_key], local['id']))
        if any(_same_native_item(kind, native, dict(row)) for row in others):
            raise ValueError('native identity already belongs to another local item')
        sync._save_map(mapping, connection=db)
        values = {'item-metadata:': [metadata(local), metadata(native, True)],
                  ('reminder' if kind == 'reminders' else 'calendar') + '-binding-generation:':
                      {'generation': item['owner_snapshot']['generation'], 'source_id': native['source_id'], 'group_id': native[group_key]}}
        if kind == 'reminders':
            values['reminder-local-remind-at:'] = {'value': local.get('remind_at')}
        for prefix, value in values.items():
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (prefix + local['id'], json.dumps(value, ensure_ascii=False)))


def dispatch(sync, kind, plan, item, claim, authorize):
    _, group_key, native_key = _shape(kind)
    operation = plan['operation_id']
    binding = digest([operation, plan['plan_fingerprint'], item['local_id']])
    directory = sync.db_path.parent / 'native-receipts'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / (binding + '.json')
    if path.exists() or path.is_symlink():
        raise ValueError('native receipt already exists; export will not be replayed')
    payload = {**item['wire_payload'], 'expected_source_id': item['source_id'],
               'expected_destination_snapshot': item['destination_snapshot'],
               'receipt_file': str(path), 'receipt_binding': binding,
               **({'expected_snapshot': item['native_snapshot']} if item['action'] == 'update' else {'expected_absent': True})}
    # Mark before dispatch. A failed callback may conservatively retain the hold.
    claim.started('sending')
    try:
        authorize(item['local_id'])
    except Exception:
        claim.native_rejected()  # No native call was dispatched.
        raise
    native = sync.bridge.call('reminder_upsert' if kind == 'reminders' else 'upsert', **payload)['reminder' if kind == 'reminders' else 'event']
    if native.get(group_key) != item['group_id'] or native.get('source_id') != item['source_id'] or not native.get(native_key):
        raise ValueError('native export result identity changed')
    receipt = json.loads(path.read_text())
    if receipt != {'binding': binding, 'saved': native}:
        raise ValueError('native export receipt missing or inconsistent')
    return native


def run(sync, kind, plan, authorize):
    local_key, group_key, native_key = _shape(kind)
    operation = plan['operation_id']
    if plan.get('kind') != kind or plan.get('plan_fingerprint') != digest({k: v for k, v in plan.items() if k != 'plan_fingerprint'}):
        raise ValueError('approved export plan changed')
    def approved(item=None):
        if authorize(operation, plan['plan_fingerprint'], item) is not True:
            raise PermissionError('export execution approval unavailable or revoked')
    owner = sync.todos if kind == 'reminders' else sync.calendar
    key = 'approved-export:' + operation
    with sync._lock:
        approved()
        if sync._get(key) is not None:
            return {'status': 'needs_review', 'operation_id': operation, 'message': '此操作已有记录，不会重发。'}
        # A persisted attempt is a no-retry marker, never persisted permission.
        sync._set(key, {'plan': plan, 'state': 'admitted', 'completed': []})
        completed = []
        try:
            current = prepare(sync, kind, plan['method'], plan['arguments'], operation)
            if current != plan:
                raise ValueError('export plan changed while waiting for approval')
            for item in plan['items']:
                groups = {item['group_id']: item['source_id']}
                with WorkClaim(sync, groups, [item['local_id']], {'kind': 'approved_export', 'operation_id': operation, 'plan_fingerprint': plan['plan_fingerprint'], 'item': item}) as claim:
                    _recheck(sync, kind, item, plan['links_snapshot'])
                    native = dispatch(sync, kind, plan, item, claim, lambda identity: approved(identity))
                    local = owner.apple_finalize_approved_export(item['owner_snapshot'], native, item['native_snapshot'])
                    _commit_mapping(sync, kind, item, local, native, claim)
                    completed.append(item['local_id'])
                    sync._set(key, {'plan': plan, 'state': 'running', 'completed': completed})
            result = {'status': 'success', 'operation_id': operation, 'exported': len(completed), 'updated': sum(item['action'] == 'update' for item in plan['items']), 'imported': 0, 'skipped': plan['skipped'], 'conflicts': 0}
            sync._set(key, {'plan': plan, 'state': 'done', 'completed': completed, 'result': result})
            return result
        except Exception as error:
            sync._set(key, {'plan': plan, 'state': 'needs_review', 'completed': completed, 'error': str(error)})
            return {'status': 'needs_review', 'operation_id': operation, 'exported': len(completed), 'skipped': plan['skipped'], 'message': str(error) + '；请核对状态，不要重复发送。'}


def guard_unresolved(sync):
    # Direct import and compatibility routes must respect interrupted exports too.
    from apple_sync.group_scan_claims import schema
    with sync._connect() as db:
        if not schema(db):
            return
        for row in db.execute("SELECT evidence FROM calendar_scan_attempts WHERE state IN ('scanning','uncertain','receipt_recovery')"):
            if json.loads(row[0]).get('evidence', {}).get('kind') == 'approved_export':
                raise ValueError('具体导出结果尚未核对；已保留占用，不会重发或覆盖。')
