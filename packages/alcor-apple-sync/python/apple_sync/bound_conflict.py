"""One explicitly selected, existing bound conflict. Recovery only reads receipts."""
import json
from datetime import datetime, timedelta
from apple_sync.approved_export import digest, _shape, _baseline, _same_native_item, dispatch
from apple_sync.contracts import locate_remote
from apple_sync.group_scan_claims import WorkClaim
from apple_sync.metadata import metadata


def _key(identity):
    return 'bound-item-conflict:' + identity


def pending(sync):
    from apple_sync.group_scan_claims import schema
    with sync._connect() as db:
        held = [json.loads(row[0]).get('evidence', {}) for row in db.execute(
            'SELECT a.evidence FROM calendar_scan_attempts a JOIN calendar_scan_groups g USING(attempt_id)')] if schema(db) else []
        journals = [json.loads(row[0]) for row in db.execute("SELECT value FROM settings WHERE key LIKE 'bound-item-conflict:%'")]
    result = {}
    for journal in journals:
        plan = journal['plan']
        _check_plan(plan, plan['kind'])
        if journal['state'] == 'not_sent':
            continue
        if journal['state'] == 'done' and not any(row.get('kind') == 'bound_item_conflict' and
                row.get('operation_id') == plan['operation_id'] and row.get('plan_fingerprint') == plan['plan_fingerprint'] for row in held):
            continue
        phase = 'finalizing' if journal['state'] == 'done' else 'owner_uncertain'
        if journal['state'] != 'done' and plan['arguments']['choice'] == 'keep_alcor':
            try:
                _native_receipt(sync, plan)
                phase = 'native_saved'
            except (ValueError, OSError):
                phase = 'native_uncertain'
        result[plan['arguments']['identity']] = {'choice': plan['arguments']['choice'], 'phase': phase}
    return result


def _validate_arguments(kind, arguments, operation_id):
    _shape(kind)
    if (not isinstance(arguments, dict) or set(arguments) != {'kind', 'identity', 'choice'}
            or arguments['kind'] != kind or arguments['choice'] not in ('keep_apple', 'keep_alcor')
            or not isinstance(arguments['identity'], str) or not arguments['identity']
            or not isinstance(operation_id, str) or len(operation_id) != 64 or any(c not in '0123456789abcdef' for c in operation_id)):
        raise ValueError('invalid bound conflict selection')


def _check_plan(plan, kind):
    fields = {'version', 'operation_id', 'kind', 'mode', 'method', 'arguments', 'links_snapshot', 'items', 'skipped', 'plan_fingerprint'}
    if (not isinstance(plan, dict) or set(plan) != fields or type(plan['version']) is not int or plan['version'] != 1
            or plan['kind'] != kind or plan['mode'] != 'conflict' or plan['method'] != 'resolve_conflict'
            or type(plan['skipped']) is not int or plan['skipped'] != 0 or not isinstance(plan['items'], list) or len(plan['items']) != 1
            or plan['plan_fingerprint'] != digest({k: v for k, v in plan.items() if k != 'plan_fingerprint'})):
        raise ValueError('bound conflict plan changed')
    _validate_arguments(kind, plan['arguments'], plan['operation_id'])
    item = plan['items'][0]
    if item['local_id'] != plan['arguments']['identity'] or item['action'] != ('local_update' if plan['arguments']['choice'] == 'keep_apple' else 'update'):
        raise ValueError('bound conflict target changed')


def prepare(sync, kind, arguments, operation_id):
    _validate_arguments(kind, arguments, operation_id)
    with sync._lock:
        journal = sync._get(_key(arguments['identity']))
        if journal:
            plan = journal['plan']; _check_plan(plan, kind)
            local_key, _, _ = _shape(kind)
            current = next((row for row in sync._maps() if row[local_key] == arguments['identity']), None)
            unfinished = journal['state'] not in ('done', 'not_sent')
            if unfinished or journal['state'] == 'done' and (not current or current['state'] != 'conflict'):
                if plan['arguments'] != arguments:
                    raise ValueError('unfinished conflict choice differs; verify the previous receipt first')
                return {'mode': 'receipt_finish', 'plan': plan}
        return _prepare(sync, kind, arguments, operation_id)


def _prepare(sync, kind, arguments, operation_id):
    local_key, group_key, native_key = _shape(kind)
    identity, choice = arguments['identity'], arguments['choice']
    owner = sync.todos if kind == 'reminders' else sync.calendar
    from apple_sync.category_rebuild import guard_retained
    from apple_sync.marker_recovery import guard_pending
    guard_retained(sync, identity); guard_pending(sync, identity)
    status = sync.bridge.call('reminder_status' if kind == 'reminders' else 'status')
    if status.get('authorization') != 'full_access' or choice == 'keep_alcor' and status.get('approved_export_version') != 1:
        raise PermissionError('Apple source or conditional write contract is unavailable')
    maps = sync._maps()
    rows = [row for row in maps if row[local_key] == identity]
    if len(rows) != 1 or rows[0]['state'] != 'conflict':
        raise ValueError('only an existing bound conflict can be resolved here')
    mapping = rows[0]
    expected = owner.apple_capture_approved_export(identity)
    local = expected['local']
    native = locate_remote(sync, mapping, kind)
    inventory = sync.bridge.call('reminder_lists' if kind == 'reminders' else 'calendars')
    targets = [row for row in inventory.get('lists' if kind == 'reminders' else 'calendars', []) if row.get('id') == mapping[group_key]]
    if len(targets) != 1 or not targets[0].get('source_id') or targets[0]['source_id'] not in inventory.get('source_ids', []):
        raise ValueError('bound conflict source is unavailable or ambiguous')
    target = targets[0]
    binding = local.get('apple') or {}
    links = sync._stored_links()
    matching = [row for row in links if row['apple_id'] == mapping[group_key] or row['alcor_id'] == local.get('category_id')]
    category = next((row for row in expected['categories'] if row['id'] == local.get('category_id')), {})
    container = category.get('apple') or {}
    baseline = _baseline(sync, kind, identity)
    generation = baseline[('reminder' if kind == 'reminders' else 'calendar') + '-binding-generation:']
    if (not native or native.get(native_key) != mapping[native_key] or native.get(group_key) != mapping[group_key]
            or native.get('source_id') != target['source_id'] or not native.get('last_modified')
            or binding.get('apple_id') != mapping[native_key] or binding.get('group_id') != mapping[group_key]
            or kind == 'calendar' and (not _same_native_item(kind, mapping, native)
                                      or not _same_native_item(kind, mapping, binding.get('native') or {}))
            or binding.get('source_id') != target['source_id'] or container.get('apple_id') != mapping[group_key]
            or container.get('source_id') != target['source_id'] or container.get('planned')
            or len(matching) != 1 or not matching[0]['enabled'] or matching[0]['apple_id'] != mapping[group_key]
            or matching[0]['alcor_id'] != local.get('category_id')
            or generation not in (None, {'generation': expected['generation'], 'source_id': target['source_id'], 'group_id': mapping[group_key]})
            or not baseline['item-metadata:'] or mapping.get('missing_since')
            or any(row[local_key] != identity and _same_native_item(kind, mapping, row) for row in maps)
            or any(row['local_id'] == identity and row['entity'] == 'item' and row['state'] in ('pending', 'sending', 'uncertain') for row in owner.apple_operations())):
        raise ValueError('both objects, exact binding, source and versions must still agree')
    inbound = None
    if choice == 'keep_alcor':
        if not target.get('writable') or not native.get('writable') or not binding.get('can_modify') or not container.get('can_modify'):
            raise PermissionError('Apple conflict target is not writable')
        if kind == 'reminders':
            from apple_sync.reminders import _supported
            if not _supported(native) or baseline['reminder-local-remind-at:'] is None:
                raise ValueError('unsupported reminder or unknown alarm baseline')
            payload = sync._write_payload(local, mapping, list_id=mapping[group_key], include_receipt=False)
            if 'alarms' not in local:
                payload['remind_at'] = local.get('remind_at')
        else:
            anchor = datetime.fromisoformat(native['start_at'].replace('Z', '+00:00'))
            window = ((anchor - timedelta(days=2)).isoformat(), (anchor + timedelta(days=2)).isoformat())
            payload = sync._write_payload(local, mapping, calendar_id=mapping[group_key], window=window, include_receipt=False)
        # This choice selects the complete frozen local version, including unchanged metadata.
        payload.update(url=local.get('url') or '', floating=bool(local.get('floating')), set_recurrence=True)
        if 'alarms' in local:
            payload['alarms'] = local['alarms']
        if metadata(local)['simple_recurrence'] == baseline['item-metadata:'][0].get('simple_recurrence') and local.get('native_recurrence'):
            payload['native_recurrence'] = local['native_recurrence']
    else:
        if kind == 'reminders':
            from apple_sync.reminders import _payload
            inbound = owner.apple_capture_reminder_sync(identity)
            payload = _payload(native)
        else:
            inbound = owner.apple_capture_calendar_sync(identity)
            payload = sync._series_payload(local, native, local['category_id'])
        if not inbound or any(inbound[key] != expected[key] for key in ('row', 'local', 'generation')):
            raise ValueError('owner changed during conflict preview')
    item = {'local_id': identity, 'source_id': target['source_id'], 'group_id': mapping[group_key],
            'action': 'local_update' if choice == 'keep_apple' else 'update', 'wire_payload': payload,
            'owner_snapshot': expected, 'mapping_snapshot': mapping, 'native_snapshot': native,
            'destination_snapshot': target, 'baseline_snapshot': baseline}
    if inbound is not None:
        item['owner_inbound_snapshot'] = inbound
    plan = {'version': 1, 'operation_id': operation_id, 'kind': kind, 'mode': 'conflict', 'method': 'resolve_conflict',
            'arguments': arguments, 'links_snapshot': links, 'items': [item], 'skipped': 0}
    return {**plan, 'plan_fingerprint': digest(plan)}


def _native_receipt(sync, plan):
    item = plan['items'][0]
    if plan['arguments']['choice'] == 'keep_apple':
        return item['native_snapshot']
    binding = digest([plan['operation_id'], plan['plan_fingerprint'], item['local_id']])
    path = sync.db_path.parent / 'native-receipts' / (binding + '.json')
    if path.is_symlink() or not path.is_file():
        raise ValueError('native conflict receipt is unavailable; no retry')
    receipt = json.loads(path.read_text())
    native = receipt.get('saved')
    _, group, key = _shape(plan['kind'])
    if (set(receipt) != {'binding', 'saved'} or receipt['binding'] != binding or not isinstance(native, dict)
            or any(native.get(field) != item['native_snapshot'].get(field) for field in (group, key, 'source_id'))):
        raise ValueError('native conflict receipt identity differs; no retry')
    return native


def _manager_check(sync, kind, plan, db, claim):
    claim._check(db)
    item = plan['items'][0]; local_key, _, _ = _shape(kind)
    row = db.execute('SELECT * FROM mappings WHERE ' + local_key + '=?', (item['local_id'],)).fetchone()
    if not row or dict(row) != item['mapping_snapshot'] or [dict(row) for row in db.execute('SELECT * FROM category_links ORDER BY apple_id')] != plan['links_snapshot']:
        raise ValueError('conflict mapping or selection changed after owner commit')
    for prefix, value in item['baseline_snapshot'].items():
        old = db.execute('SELECT value FROM settings WHERE key=?', (prefix + item['local_id'],)).fetchone()
        if (json.loads(old[0]) if old else None) != value:
            raise ValueError('conflict baseline changed after owner commit')


def _finish(sync, owner, kind, plan, claim, journal):
    item = plan['items'][0]; native = _native_receipt(sync, plan)
    verified = owner.apple_verify_bound_conflict(plan['operation_id'], item, plan['arguments']['choice'], native)
    if verified is None:
        raise ValueError('owner commit is not proven; receipt verification only, no retry')
    local = verified['local']; _, group, _ = _shape(kind)
    if local['id'] != item['local_id'] or verified['native'] != native or verified['generation'] != item['owner_snapshot']['generation']:
        raise ValueError('owner conflict receipt changed')
    result = {'status': 'success', 'operation_id': plan['operation_id'], 'resolved': 1,
              'exported': int(item['action'] == 'update'), 'updated': 1, 'imported': int(item['action'] == 'local_update'), 'skipped': 0, 'conflicts': 0}
    updated = {**item['mapping_snapshot'], **sync._mapping_from_native(local, native, item['mapping_snapshot']['origin'], item['mapping_snapshot']['source_key'])}
    values = {'item-metadata:': [metadata(local), metadata(native, True)],
              ('reminder' if kind == 'reminders' else 'calendar') + '-binding-generation:':
                  {'generation': verified['generation'], 'source_id': native['source_id'], 'group_id': native[group]}}
    if kind == 'reminders':
        values['reminder-local-remind-at:'] = {'value': local.get('remind_at')}
    with sync._connect() as db:
        db.execute('BEGIN IMMEDIATE'); _manager_check(sync, kind, plan, db, claim)
        stored = db.execute('SELECT value FROM settings WHERE key=?', (_key(item['local_id']),)).fetchone()
        if not stored or json.loads(stored[0]) != journal:
            raise ValueError('conflict receipt journal changed')
        sync._save_map(updated, connection=db)
        for prefix, value in values.items():
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (prefix + item['local_id'], json.dumps(value, ensure_ascii=False)))
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (_key(item['local_id']), json.dumps({**journal, 'state': 'done', 'result': result, 'result_mapping': updated, 'receipt': verified['receipt']}, ensure_ascii=False)))
    return result


def run(sync, owner, kind, plan, authorize, *, receipt_only=False):
    _check_plan(plan, kind)
    operation = plan['operation_id']; item = plan['items'][0]; identity = item['local_id']
    groups = {item['group_id']: item['source_id']}
    key = _key(identity)
    evidence = {'kind': 'bound_item_conflict', 'operation_id': operation, 'plan_fingerprint': plan['plan_fingerprint'], 'item': item}
    def approved(local_id=None):
        if authorize(operation, plan['plan_fingerprint'], local_id) is not True:
            raise PermissionError('bound conflict choice is no longer active')
        if local_id is not None and _prepare(sync, kind, plan['arguments'], operation) != plan:
            raise ValueError('bound conflict changed at dispatch')
    with sync._lock:
        journal = sync._get(key)
        if journal and journal['state'] in ('done', 'not_sent') and journal['plan'] != plan and not receipt_only:
            journal = None
        attempt = None
        try:
            if journal:
                if journal['plan'] != plan:
                    raise ValueError('existing conflict receipt belongs to another selection')
                if journal['state'] == 'done':
                    if owner.apple_verify_bound_conflict(operation, item, plan['arguments']['choice'], _native_receipt(sync, plan)) is None:
                        raise ValueError('completed owner receipt is unavailable')
                    # A completed mapping is not rewritten: later owner/manager edits remain visible.
                    from apple_sync.group_scan_claims import schema
                    with sync._connect() as db:
                        held = schema(db) and any(json.loads(row[0]).get('evidence') == evidence for row in db.execute('SELECT a.evidence FROM calendar_scan_attempts a JOIN calendar_scan_groups g USING(attempt_id)'))
                    if not held:
                        return journal['result']
                if owner.apple_verify_bound_conflict(operation, item, plan['arguments']['choice'], _native_receipt(sync, plan)) is None:
                    raise ValueError('owner commit is not proven; no retry')
                with WorkClaim(sync, groups, [identity], evidence, recovery=True, completed_owner_receipt=True) as claim:
                    if journal['state'] == 'done':
                        return journal['result']
                    return _finish(sync, owner, kind, plan, claim, journal)
            if receipt_only:
                raise ValueError('conflict receipt journal is unavailable; no retry')
            approved()
            if _prepare(sync, kind, plan['arguments'], operation) != plan:
                raise ValueError('conflict changed while awaiting selection')
            with WorkClaim(sync, groups, [identity], evidence) as claim:
                attempt = claim
                if _prepare(sync, kind, plan['arguments'], operation) != plan:
                    raise ValueError('conflict changed before dispatch')
                journal = {'state': 'pending', 'plan': plan}
                sync._set(key, journal)
                if item['action'] == 'update':
                    native = dispatch(sync, kind, plan, item, claim, approved)
                    if native != _native_receipt(sync, plan):
                        raise ValueError('native receipt changed before owner finalize')
                else:
                    native = item['native_snapshot']
                    approved(identity)
                claim.started('owner_committing' if claim.started_effects else 'owner_claiming')
                try:
                    owner.apple_commit_bound_conflict(operation, item, plan['arguments']['choice'], native)
                except ValueError as error:
                    if item['action'] == 'local_update' and str(error).startswith('INBOUND_CHANGED:'):
                        claim.owner_rejected()
                    raise
                claim.started('owner_committed')
                return _finish(sync, owner, kind, plan, claim, journal)
        except Exception as error:
            if attempt is not None and not attempt.started_effects and isinstance(journal, dict) and sync._get(key) == journal:
                sync._set(key, {**journal, 'state': 'not_sent'})
            saved = sync._get(key) or {}
            if saved.get('state') == 'not_sent' or not saved:
                message = '本次选择尚未提交，请刷新后重新选择。'
            elif saved.get('state') == 'done':
                message = '两侧保存已完成，操作收尾待核对；请在 Apple 同步中对该项目再次选择原选项，仅核对既有回执。'
            elif plan['arguments']['choice'] == 'keep_apple':
                message = '本地保存结果尚未确认，Apple 未写入；请在 Apple 同步中对该项目再次选择保留 Apple，仅核对既有回执，不会重复保存。'
            else:
                try:
                    _native_receipt(sync, plan)
                    message = 'Apple 更新回执已保存，Alcor 本地结果尚未确认。'
                except (ValueError, OSError):
                    message = 'Apple 提交结果尚未确认。'
                message += '请在 Apple 同步中对该项目再次选择保留 Alcor，仅核对既有回执，不会再写 Apple；若仍无法确认，请在 Apple 核对内容，不要重发。两侧记录与占用已保留。'
            return {'status': 'needs_review', 'operation_id': operation, 'resolved': 0, 'message': str(error) + '；' + message}
