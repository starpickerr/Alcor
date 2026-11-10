"""Owner-local preparation and conditional queueing for approved Apple deletes."""
import hashlib
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from business.object_generations import generations_for
from business.security_receipts import read_receipt, save_receipt, validate_operation_id


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _request(domain, method, arguments):
    if domain not in ('tasks', 'calendar') or not isinstance(arguments, dict):
        raise ValueError('invalid Apple deletion request')
    if method == 'delete_category':
        key = 'category_id' if domain == 'tasks' else 'id'
        allowed, entity = {key}, 'category'
    elif domain == 'tasks' and method == 'delete_external_todo':
        key, allowed, entity = 'source_key', {'source_key', 'confirm', 'preserve_children'}, 'item'
        if 'preserve_children' in arguments and arguments['preserve_children'] is not True: raise ValueError('invalid task deletion range')
        if 'confirm' in arguments and type(arguments['confirm']) is not bool: raise ValueError('invalid confirmation argument')
    elif domain == 'tasks' and method == 'delete_todo':
        key, allowed, entity = 'todo_id', {'todo_id', 'preserve_children'}, 'item'
        if type(arguments.get('preserve_children', False)) is not bool:
            raise ValueError('invalid task deletion range')
    elif domain == 'calendar' and method == 'delete_event':
        key, allowed, entity = 'id', {'id', 'scope', 'occurrence_on'}, 'item'
    elif domain == 'calendar' and method == 'exclude_occurrence':
        key, allowed, entity = 'series_id', {'series_id', 'occurrence_on'}, 'item'
    else:
        raise ValueError('unsupported Apple deletion')
    if set(arguments) - allowed or not isinstance(arguments.get(key), str) or not 0 < len(arguments[key]) <= 200:
        raise ValueError('invalid Apple deletion target')
    args = dict(arguments)
    if domain == 'tasks' and method == 'delete_todo': args['preserve_children'] = args.get('preserve_children', False)
    if domain == 'tasks' and method == 'delete_external_todo': args = {'source_key': args['source_key'], 'preserve_children': True}
    if domain == 'calendar' and entity == 'item':
        if args.get('scope') not in (None, 'this', 'future'): raise ValueError('invalid calendar deletion scope')
        if method == 'exclude_occurrence' and not args.get('occurrence_on'): raise ValueError('occurrence required')
        if args.get('occurrence_on'):
            try: args['occurrence_on'] = date.fromisoformat(args['occurrence_on']).isoformat()
            except (ValueError, TypeError) as exc: raise ValueError('invalid occurrence') from exc
    return entity, args[key], args


def _resolve_identity(db, method, identity):
    if method != 'delete_external_todo': return identity
    row = db.execute("SELECT id FROM todos WHERE source_type='apple_reminder' AND source_key=?", (identity,)).fetchone()
    if not row: raise ValueError('Apple reminder source not found')
    return row['id']


def _native_occurrence_range(svc, db, identity, binding, scope, occurrence, ignore_operation_id):
    native=binding.get('native') or {};external=native.get('external_id')
    instant=lambda value:datetime.fromisoformat(value.replace('Z','+00:00'))
    if (not external or native.get('source_id')!=binding['source_id'] or native.get('calendar_id')!=binding['group_id']
            or native.get('event_id')!=binding['apple_id'] or not native.get('occurrence_date')
            or scope=='future' and (native.get('is_detached') or (native.get('recurrence') or {}).get('rule_count',1)>1)):
        raise ValueError('原生重复系列身份或例外范围无法确认，请在 Apple 核对')
    anchor=instant(native['occurrence_date']);zone=ZoneInfo(native.get('timezone') or 'UTC')
    if anchor.astimezone(zone).date().isoformat()!=occurrence:
        raise ValueError('请选择当前原生实例，再确认删除范围')
    selected=[];dates=set()
    for item in db.execute("SELECT e.*,b.document AS binding_document FROM calendar_events e JOIN apple_bindings b ON b.entity='item' AND b.local_id=e.id ORDER BY e.id"):
        bound=json.loads(item['binding_document']);saved=bound.get('native') or {}
        if (bound.get('source_id'),bound.get('group_id'),saved.get('external_id'))!=(binding['source_id'],binding['group_id'],external):continue
        if scope=='this' and item['id']!=identity:continue
        if not saved.get('occurrence_date'):raise ValueError('同系列本地实例缺少日期，范围待核对')
        when=instant(saved['occurrence_date'])
        if scope=='future' and when<anchor:continue
        if (saved.get('source_id')!=binding['source_id'] or saved.get('calendar_id')!=binding['group_id']
                or saved.get('event_id')!=bound.get('apple_id') or scope=='future' and saved.get('is_detached')
                or item['repeat_frequency']!='none' or item['source_type']!='apple_calendar' or when in dates):
            raise ValueError('同系列本地实例身份或例外范围不明确，已保留待核对')
        if ignore_operation_id is None:svc._apple_guard(db,'item',item['id'],'can_delete')
        if db.execute("SELECT 1 FROM apple_operations WHERE entity='item' AND local_id=? AND state IN ('sending','uncertain') AND id!=?",(item['id'],ignore_operation_id or '')).fetchone():
            raise ValueError('范围内有结果未知的 Apple 操作，请先核对')
        dates.add(when)
        selected.append(svc._local_event_delete_snapshot(db,item['id'],allow_apple=True,allow_recurring=True,ignore_operation_id=ignore_operation_id))
    if identity not in {item['id'] for item in selected}:raise ValueError('原生实例关联已变化')
    return {'snapshot':_digest(selected),'targets':[target for item in selected for target in item['targets']]}


def _plan(svc, db, domain, method, entity, identity, args, *, ignore_operation_id=None, ignore_exclusion_on=None):
    binding = svc._apple_binding(db, entity, identity) if ignore_operation_id else svc._apple_guard(db, entity, identity, 'can_delete')
    if not binding or binding.get('planned') or not binding.get('source_id') or not binding.get('apple_id'):
        raise ValueError('Apple 目标或来源身份缺失，请重新确认')
    if entity == 'item' and not binding.get('group_id'):
        raise ValueError('Apple 目标分组缺失，请重新确认')
    if entity == 'category':
        local = svc._local_category_delete_snapshot(db, identity, allow_apple=True, ignore_operation_id=ignore_operation_id)
        action, scope, occurrence, document = 'delete', 'category', None, {}
    elif domain == 'tasks':
        local = svc._local_delete_snapshot(db, identity, args['preserve_children'], allow_apple=True, ignore_operation_id=ignore_operation_id)
        if args['preserve_children'] and len(local['targets']) > 1: raise ValueError('请先移动子事务再删除 Apple 提醒')
        action, scope, occurrence, document = 'delete', 'item', None, {}
    else:
        local = svc._local_event_delete_snapshot(db, identity, allow_recurring=True, allow_apple=True, ignore_operation_id=ignore_operation_id, ignore_exclusion_on=ignore_exclusion_on)
        row = db.execute('SELECT * FROM calendar_events WHERE id=?', (identity,)).fetchone()
        occurrence = args.get('occurrence_on')
        native_rule = (json.loads(row['apple_data'] or '{}').get('native_recurrence') or {})
        recurring = row['repeat_frequency'] != 'none' or native_rule.get('frequency', 'none') != 'none'
        if occurrence and not recurring: raise ValueError('event is not a recurring series')
        if recurring and not occurrence: raise ValueError('请明确重复日程起始实例')
        if not recurring and args.get('scope') == 'future': raise ValueError('非重复日程不支持未来范围')
        scope = 'item' if not recurring else ('this' if method == 'exclude_occurrence' else (args.get('scope') or 'this'))
        if occurrence:
            day = date.fromisoformat(occurrence); zone = ZoneInfo(row['repeat_timezone'] or 'UTC')
            lower = datetime.combine(day, time.min, zone)
            if row['repeat_frequency'] != 'none' and not svc._occurrences(row, lower, lower + timedelta(days=1)):
                raise ValueError('所选日期不是此系列的实例')
        if occurrence and scope == 'this' and occurrence != ignore_exclusion_on and db.execute('SELECT 1 FROM calendar_occurrence_exclusions WHERE series_id=? AND occurrence_on=?', (identity, occurrence)).fetchone():
            raise ValueError('所选实例已跳过')
        action = 'exclude' if occurrence and scope == 'this' else 'truncate' if occurrence and scope == 'future' and day > datetime.fromisoformat(row['start_at']).astimezone(zone).date() else 'delete'
        document = {'occurrence_on': occurrence} if action in ('exclude', 'truncate') else ({'scope': scope} if scope != 'item' else {})
        if recurring and row['repeat_frequency']=='none' and row['source_type']=='apple_calendar':
            local=_native_occurrence_range(svc,db,identity,binding,scope,occurrence,ignore_operation_id)
            action='delete';document={'native_range_ids':[target['id'] for target in local['targets']]}
    document['scope'] = scope
    if occurrence is not None: document['occurrence_on'] = occurrence
    generation = generations_for(db, entity, [identity])[0][1]
    targets = []
    for target in local['targets']:
        target_entity = 'category' if target.get('category') else 'item'
        own_binding = svc._apple_binding(db, target_entity, target['id'])
        if target_entity == 'item' and own_binding and not own_binding.get('planned'):
            if entity == 'item' and target['id'] != identity and target['id'] not in document.get('native_range_ids',[]):
                raise ValueError('子事务关联其他 Apple 对象，请先分别处理并重新确认')
            if entity == 'category' and (not own_binding.get('source_id') or not own_binding.get('group_id')):
                raise ValueError('分类成员的 Apple 来源身份不完整，请先核对来源后重新确认')
            if entity == 'category' and (own_binding.get('source_id') != binding.get('source_id') or own_binding.get('group_id') != binding.get('apple_id')):
                raise ValueError('分类包含其他 Apple 来源的对象，请先分别处理并重新确认')
        apple = {key: own_binding.get(key) for key in ('source_id', 'group_id', 'apple_id')} if own_binding else None
        targets.append({**target, **({'apple': apple} if apple else {})})
    content = {'domain': domain, 'method': method, 'arguments': args, 'entity': entity, 'id': identity,
               'action': action, 'document': document, 'local_snapshot': local['snapshot'], 'binding': binding,
               'generation': generation}
    return {'id': identity, 'snapshot': _digest(content), 'targets': targets, 'apple': {'entity': entity, 'binding': binding,
            'scope': scope, 'occurrence_on': occurrence}, '_content': content}


def prepare(svc, domain, method, arguments, operation_id):
    validate_operation_id(operation_id)
    entity, identity, args = _request(domain, method, arguments)
    with svc._connect() as db:
        db.execute('BEGIN')
        receipt = read_receipt(db, operation_id, f'apple_delete:{domain}:{method}', args)
        if receipt:
            return _replay(db, svc, entity, receipt['targetId'], operation_id)
        identity = _resolve_identity(db, method, identity)
        result = _plan(svc, db, domain, method, entity, identity, args)
        result.pop('_content')
        return result


def _replay(db, svc, entity, identity, operation_id, snapshot=None, native_snapshot=None):
    row = db.execute('SELECT * FROM apple_operations WHERE id=?', (operation_id,)).fetchone()
    if not row: raise ValueError('approved operation receipt is incomplete')
    document = json.loads(row['document'])
    if snapshot is not None and document.get('_approval') != {'snapshot': snapshot, 'native': native_snapshot}:
        raise ValueError('idempotency conflict: different approved Apple snapshot')
    current = db.execute(f'SELECT 1 FROM {svc.APPLE_CATEGORIES if entity == "category" else svc.APPLE_ITEMS} WHERE id=?', (identity,)).fetchone()
    conflict = bool(current and generations_for(db, entity, [identity])[0][1] != document.get('_approved_generation'))
    return {'replayed': True, 'operation_id': operation_id, 'state': row['state'], 'pending': row['state'] in ('pending', 'sending', 'uncertain'), 'targetConflict': conflict}


def commit(svc, domain, method, arguments, operation_id, snapshot, native_snapshot, confirmed_intent=None):
    validate_operation_id(operation_id)
    if confirmed_intent is not None:
        if (not isinstance(confirmed_intent, dict) or set(confirmed_intent) != {'version', 'plan_fingerprint'}
                or type(confirmed_intent['version']) is not int or confirmed_intent['version'] != 1):
            raise ValueError('invalid confirmed deletion intent')
        validate_operation_id(confirmed_intent['plan_fingerprint'])
    entity, identity, args = _request(domain, method, arguments)
    if not isinstance(snapshot, str) or len(snapshot) != 64 or not isinstance(native_snapshot, dict):
        raise ValueError('invalid approved Apple snapshot')
    with svc._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        receipt = read_receipt(db, operation_id, f'apple_delete:{domain}:{method}', args)
        if receipt:
            if confirmed_intent is not None:
                row = db.execute('SELECT document FROM apple_operations WHERE id=?', (operation_id,)).fetchone()
                if not row or json.loads(row['document']).get('_confirmed_intent') != confirmed_intent:
                    raise ValueError('confirmed deletion intent differs; an old operation cannot be upgraded')
            return _replay(db, svc, entity, receipt['targetId'], operation_id, snapshot, native_snapshot)
        identity = _resolve_identity(db, method, identity)
        plan = _plan(svc, db, domain, method, entity, identity, args)
        if plan['snapshot'] != snapshot: raise ValueError('Apple deletion snapshot conflict; review again')
        binding, scope, occurrence = plan['apple']['binding'], plan['apple']['scope'], plan['apple']['occurrence_on']
        group = binding['apple_id'] if entity == 'category' else binding['group_id']
        required = {'version', 'source_id', 'group_id', 'series_apple_id', 'instance_apple_id', 'occurrence_on', 'native_fingerprint', 'entity', 'scope', 'native_snapshot'}
        if (set(native_snapshot) != required or native_snapshot['version'] != 1 or native_snapshot['entity'] != entity or native_snapshot['scope'] != scope
                or native_snapshot['source_id'] != binding['source_id'] or native_snapshot['group_id'] != group
                or native_snapshot['series_apple_id'] != binding['apple_id'] or native_snapshot['occurrence_on'] != occurrence
                or not isinstance(native_snapshot['instance_apple_id'], str) or not native_snapshot['instance_apple_id']
                or (not occurrence and native_snapshot['instance_apple_id'] != binding['apple_id'])
                or not isinstance(native_snapshot['native_snapshot'], dict)
                or native_snapshot['native_fingerprint'] != _digest(native_snapshot['native_snapshot'])):
            raise ValueError('Apple 原生目标或版本无法确认')
        content = plan['_content']; document = {**content['document'], '_approved_generation': content['generation'],
                   '_approved_request': {'domain': domain, 'method': method, 'arguments': args}}
        if confirmed_intent is not None:
            frozen = _frozen_plan(domain, operation_id, plan, native_snapshot)
            if confirmed_intent['plan_fingerprint'] != frozen['plan_fingerprint']:
                raise ValueError('confirmed deletion fingerprint changed')
            document['_confirmed_intent'] = dict(confirmed_intent)
        result = svc._apple_queue(db, entity, identity, content['action'], document, operation_id=operation_id,
                                  approved_snapshot=snapshot, approved_native=native_snapshot)
        save_receipt(db, operation_id, f'apple_delete:{domain}:{method}', args, identity)
        return result


def revalidate_delete_snapshot(svc, db, operation):
    """Recheck an approved deletion in the owner's existing write transaction."""
    document = json.loads(operation['document']) if isinstance(operation['document'], str) else operation['document']
    request = document.get('_approved_request') or {}
    if operation['action'] not in ('delete', 'truncate', 'exclude'): raise ValueError('not an approved deletion')
    entity, requested, args = _request(request.get('domain'), request.get('method'), request.get('arguments'))
    identity = _resolve_identity(db, request['method'], requested)
    if entity != operation['entity'] or identity != operation['local_id']:
        raise ValueError('approved deletion target changed')
    current = _plan(svc, db, request['domain'], request['method'], entity, identity, args,
                    ignore_operation_id=operation['id'])
    if current['snapshot'] != (document.get('_approval') or {}).get('snapshot'):
        raise ValueError('approved deletion contents changed; review required')
    return True


def revalidate_category_snapshot(svc, db, operation):
    if operation['entity'] != 'category': raise ValueError('not an approved category deletion')
    return revalidate_delete_snapshot(svc, db, operation)


def _frozen_plan(domain, operation_id, local_plan, native):
    plan = {'version': 1, 'domain': domain, 'kind': 'reminders' if domain == 'tasks' else 'calendar',
            'operation_id': operation_id, 'local_plan': {key: value for key, value in local_plan.items() if key != '_content'},
            'native': native}
    fingerprint = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return {**plan, 'plan_fingerprint': fingerprint}


def prepare_confirmed(svc, domain, operation_id):
    """Read a previously confirmed, still-unsent intent; never promote old queue rows."""
    validate_operation_id(operation_id)
    with svc._connect() as db:
        db.execute('BEGIN')
        row = db.execute('SELECT * FROM apple_operations WHERE id=?', (operation_id,)).fetchone()
        if not row or row['state'] != 'pending': return None
        document = json.loads(row['document'])
        intent = document.get('_confirmed_intent')
        if intent is None: return None
        if (not isinstance(intent, dict) or set(intent) != {'version', 'plan_fingerprint'}
                or type(intent['version']) is not int or intent['version'] != 1):
            raise ValueError('invalid stored confirmed deletion intent')
        request = document.get('_approved_request') or {}
        if request.get('domain') != domain: raise ValueError('confirmed deletion domain changed')
        revalidate_delete_snapshot(svc, db, row)
        entity, requested, args = _request(domain, request['method'], request['arguments'])
        receipt = read_receipt(db, operation_id, f"apple_delete:{domain}:{request['method']}", args)
        if not receipt or receipt['targetId'] != row['local_id']:
            raise ValueError('confirmed deletion receipt missing or changed')
        identity = _resolve_identity(db, request['method'], requested)
        local = _plan(svc, db, domain, request['method'], entity, identity, args, ignore_operation_id=operation_id)
        frozen = _frozen_plan(domain, operation_id, local, document['_approval']['native'])
        if frozen['plan_fingerprint'] != intent['plan_fingerprint']:
            raise ValueError('confirmed deletion plan changed; review required')
        return frozen


def verify_confirmed_finish(svc, domain, operation_id, plan):
    """Verify only the original confirmed intent, never native success or a new dispatch."""
    validate_operation_id(operation_id)
    if (not isinstance(plan, dict) or type(plan.get('version')) is not int or plan['version'] != 1
            or plan.get('domain') != domain or plan.get('operation_id') != operation_id
            or plan.get('kind') != ('reminders' if domain == 'tasks' else 'calendar')
            or not isinstance(plan.get('local_plan'), dict) or not isinstance(plan.get('native'), dict)):
        raise ValueError('invalid confirmed finish plan')
    frozen = {key: value for key, value in plan.items() if key != 'plan_fingerprint'}
    fingerprint = hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    if plan.get('plan_fingerprint') != fingerprint:
        raise ValueError('confirmed finish plan fingerprint changed')
    with svc._connect() as db:
        db.execute('BEGIN')
        row = db.execute('SELECT * FROM apple_operations WHERE id=?', (operation_id,)).fetchone()
        if not row or row['state'] not in ('sending', 'uncertain', 'done') or row['action'] not in ('delete', 'truncate', 'exclude'):
            raise ValueError('confirmed deletion is not awaiting finish')
        document = json.loads(row['document'])
        intent = document.get('_confirmed_intent')
        if (not isinstance(intent, dict) or type(intent.get('version')) is not int
                or intent != {'version': 1, 'plan_fingerprint': fingerprint}):
            raise ValueError('confirmed finish intent missing or changed')
        request = document.get('_approved_request') or {}
        if request.get('domain') != domain: raise ValueError('confirmed finish domain changed')
        entity, requested, args = _request(domain, request.get('method'), request.get('arguments'))
        receipt = read_receipt(db, operation_id, f"apple_delete:{domain}:{request['method']}", args)
        local = plan['local_plan']
        if (not receipt or receipt['targetId'] != row['local_id'] or entity != row['entity']
                or local.get('id') != row['local_id'] or plan['native'].get('entity') != entity
                or (request['method'] != 'delete_external_todo' and requested != row['local_id'])
                or document.get('_approval') != {'snapshot': local.get('snapshot'), 'native': plan['native']}):
            raise ValueError('confirmed finish receipt or target changed')
        return True
