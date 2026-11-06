from __future__ import annotations

import importlib
import inspect
import json
import sqlite3
import sys
import traceback
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def jsonable(value):
    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return value


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class Business:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.entries = {name: importlib.import_module(f'business.{name}.entry')
                        for name in ('tasks', 'calendar', 'bills', 'briefing', 'agent_hub')}
        from business.tasks.domain.service import TodoService
        from business.calendar.domain.service import CalendarService
        from business.bills.domain.service import BillsService
        from business.bills.domain.store import BillStore
        from business.briefing.domain.service import BriefingService
        from business.briefing.domain.store import BriefingStore
        from business.agent_hub.domain.service import AgentHubService
        from business.agent_hub.domain.store import AgentHubStore
        self.tasks = TodoService(self.root / 'tasks.sqlite3')
        self.calendar = CalendarService(self.root / 'calendar.sqlite3')
        self.bills = BillsService(BillStore(self.root / 'bills.sqlite3'))
        self.briefing = BriefingService(BriefingStore(self.root / 'briefing.sqlite3'), self.tasks, self.calendar)
        self.agent_hub = AgentHubService(AgentHubStore(self.root / 'agent-hub.sqlite3'))
        for name, entry in self.entries.items():
            entry._service = lambda request, name=name: getattr(self, name)
        self.meta = sqlite3.connect(self.root / 'projection.sqlite3')
        self.meta.row_factory = sqlite3.Row
        self.meta.execute('CREATE TABLE IF NOT EXISTS projection (id TEXT PRIMARY KEY, kind TEXT NOT NULL, revision INTEGER NOT NULL, archived INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, fingerprint TEXT NOT NULL)')
        self.meta.execute('CREATE TABLE IF NOT EXISTS migration (id TEXT PRIMARY KEY)')
        self.meta.execute('CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, document TEXT NOT NULL)')
        self.meta.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, document TEXT NOT NULL)')
        self.meta.execute('CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, document TEXT)')
        self.meta.commit()

        with self.agent_hub.store._connect() as db:
            tasks = [json.loads(row[0]) for row in db.execute('SELECT document_json FROM hub_tasks')]
        for task in tasks:
            for step in task['steps']:
                for attempt in step['attempts']:
                    if attempt['status'] in ('queued', 'running', 'cancel_requested', 'waiting_approval', 'interrupted'):
                        self.agent_hub.sync_attempt(task['taskId'], step['stepId'], attempt['executionId'], {
                            'status': 'outcome_unknown', 'summary': '宿主曾停止，执行结果未知；请核对原结果后显式重试。'})

    def invoke(self, domain, method, arguments=None, request_id=None):
        descriptor = next((item for entry in [self.entries.get(domain)] if entry for group in entry.SERVICE_DESCRIPTORS for item in group['methods'] if item['name'] == method), None)
        if request_id and descriptor and descriptor.get('effect') == 'domainWrite':
            fingerprint = json.dumps([domain, method, arguments or {}], sort_keys=True, ensure_ascii=False)
            previous = self.meta.execute('SELECT * FROM operations WHERE id=?', (request_id,)).fetchone()
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise ValueError('idempotency conflict: 同一操作标识不能用于不同操作。')
                if previous['document'] is None:
                    raise ValueError('操作结果未知，请核对已保存对象后再决定是否重试。')
                result = json.loads(previous['document'])
                if 'error' in result:
                    raise ValueError(result['error'])
                return result['value']
            self.meta.execute('INSERT INTO operations VALUES (?,?,NULL)', (request_id, fingerprint))
            self.meta.commit()
            try:
                value = self._invoke(domain, method, arguments, request_id)
            except (ValueError, KeyError, TypeError) as error:
                self.meta.execute('UPDATE operations SET document=? WHERE id=?', (json.dumps({'error': str(error)}, ensure_ascii=False), request_id))
                self.meta.commit()
                raise
            self.meta.execute('UPDATE operations SET document=? WHERE id=?', (json.dumps({'value': value}, ensure_ascii=False), request_id))
            self.meta.commit()
            return value
        return self._invoke(domain, method, arguments, request_id)

    def _invoke(self, domain, method, arguments=None, request_id=None):
        entry = self.entries[domain]
        if method == 'catalog':
            from business.contracts import catalog
            return catalog(domain, entry, getattr(self, domain))
        allowed = {method['name'] for descriptor in entry.SERVICE_DESCRIPTORS for method in descriptor['methods']}
        if method not in allowed:
            raise ValueError('Unknown business operation')
        request = {'method': method, 'input': arguments or {}, 'idempotencyKey': request_id,
                   '_hostDataHandle': {'hubCoordinator': True, 'sourceDirectory': [], 'executionTask': (arguments or {}).get('task'),
                                       'executionSandbox': {'mode': 'core', **((arguments or {}).get('task') or {}), 'outputDirectory': str(self.root / 'executions')}}}
        result = entry.handle_invoke(request)
        if result['status'] != 'succeeded':
            raise ValueError(result.get('error', {}).get('message') or 'Operation outcome unknown; check the saved state before retrying')
        return jsonable(result.get('value'))

    def _metadata(self, record, raw):
        fingerprint = json.dumps(jsonable(raw), sort_keys=True, ensure_ascii=False)
        stored = self.meta.execute('SELECT * FROM projection WHERE id=?', (record['id'],)).fetchone()
        stamp = now()
        if stored is None:
            self.meta.execute('INSERT INTO projection VALUES (?,?,1,0,?,?,?)',
                              (record['id'], record['kind'], stamp, stamp, fingerprint))
        elif stored['fingerprint'] != fingerprint:
            self.meta.execute('UPDATE projection SET revision=revision+1,updated_at=?,fingerprint=? WHERE id=?', (stamp, fingerprint, record['id']))
        self.meta.commit()
        row = self.meta.execute('SELECT * FROM projection WHERE id=?', (record['id'],)).fetchone()
        return {**record, 'revision': row['revision'], 'archived': bool(row['archived']),
                'createdAt': row['created_at'], 'updatedAt': row['updated_at'], 'business': jsonable(raw)}

    def records(self):
        records = []
        focus = {}
        for item in self.tasks.list_todos_focus():
            if item.get('series_id'):
                field = 'last_completed_occurrence' if item['status'] == 'completed' else 'current_occurrence'
                focus.setdefault(item['series_id'], {})[field] = {key: item[key] for key in ('id', 'due_on', 'status', 'completed_at')}
        for row in self.tasks.list_todos():
            if row['repeat_frequency'] != 'none' and row['source_type'] != 'apple_reminder':
                row = {**row, 'current_occurrence': None, **focus.get(row['id'], {})}
            records.append(self._metadata({'kind': 'task', 'id': row['id'], 'title': row['title'], 'details': row['notes'],
                                          'due': row['due_on'], 'status': 'done' if row['status'] in ('completed', 'cancelled') else 'open'}, row))
        for row in self.calendar.list_events():
            if row.get('series_id'):
                continue
            zone = ZoneInfo(row.get('repeat_timezone') or 'UTC')
            start, end = row['start_at'], row['end_at']
            if row['all_day']:
                start, end = (datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(zone).date().isoformat() for value in (start, end))
            else:
                start, end = (datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') for value in (start, end))
            records.append(self._metadata({'kind': 'event', 'id': row['id'], 'title': row['title'], 'details': row['description'],
                                          'start': start, 'end': end, 'allDay': bool(row['all_day']), 'timeZone': str(zone)}, row))
        for row in self.bills.store.list_ledgers():
            digits = 0 if row['currency'] == 'JPY' else 2
            amount = format(Decimal(row['amount_minor']).scaleb(-digits).normalize(), 'f')
            records.append(self._metadata({'kind': 'bill', 'id': row['id'], 'title': row['name'], 'details': row['note'],
                                          'date': row['occurred_at'][:10], 'currency': row['currency'], 'amount': amount,
                                          'direction': row['entry_type'], 'status': {'posted': 'paid', 'pending': 'planned', 'reversed': 'reversed'}[row['status']]}, row))
        records.extend(json.loads(row[0]) for row in self.meta.execute('SELECT document FROM reports'))
        return records

    def local_briefing(self, record, request_id):
        fingerprint = json.dumps([record['date'], record['notes']], ensure_ascii=False)
        previous = self.meta.execute('SELECT * FROM reports WHERE id=?', (request_id,)).fetchone()
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise ValueError('同一操作标识不能用于不同简报。')
            return json.loads(previous['document'])
        stamp = now()
        source_records = [item for item in self.records() if item['kind'] != 'briefing' and not item['archived']]
        items = [{'source_id': item['id'], 'title': item['title'], 'snippet': item['details'],
                  'occurred_at': stamp, 'payload': {'body': item['details'], 'record': item}}
                 for item in source_records]
        if record['notes']:
            items.append({'source_id': record['date'] + ':notes', 'title': '补充材料', 'snippet': record['notes'], 'occurred_at': stamp})
        result = self.briefing.run_pipeline(briefing_date=record['date'], sources=[{'source': 'local', 'items': items, 'cursors': {'updated': stamp}}], run_at=stamp)
        snapshot = jsonable(result)
        document = {**record, 'id': request_id, 'revision': 1, 'archived': False, 'createdAt': stamp, 'updatedAt': stamp,
                    'business': {'briefingId': snapshot['briefing']['id'], 'revision': snapshot['revisions'][0]['number'] if snapshot['revisions'] else 1}}
        self.meta.execute('INSERT INTO reports VALUES (?,?,?)', (request_id, fingerprint, json.dumps(document, ensure_ascii=False)))
        self.meta.commit()
        return document

    def generated_briefing(self, record, request_id, sources, submissions=None, run_at=None):
        from business.briefing.domain.generator import AgentBriefingGenerator, NeedModel
        from business.briefing.domain.pipeline import window_start
        from uuid import uuid5, NAMESPACE_URL
        stamp = run_at or now()
        previous = self.meta.execute('SELECT * FROM reports WHERE id=?', (request_id,)).fetchone()
        fingerprint = json.dumps([record['date'], record['notes']], ensure_ascii=False)
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise ValueError('同一操作标识不能用于不同简报。')
            return {'record': json.loads(previous['document'])}
        start = window_start(self.briefing, record['date'], stamp, datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone())
        selected = list(sources)
        local_items = []
        if record['notes']:
            local_items.append({'source_id': record['date'] + ':notes', 'title': '补充材料', 'snippet': record['notes'], 'occurred_at': stamp})
        if local_items:
            selected.append({'source': 'local', 'items': local_items, 'cursors': {'updated': stamp}})
        if not selected:
            raise ValueError('请选择简报来源，或填写补充材料。')
        rows = {}
        normalized = []
        for source in selected:
            account = source.get('account_id') or 'default'
            prepared = []
            for item in source.get('items') or []:
                identity = json.dumps([source['source'], account, item['source_id']], ensure_ascii=False)
                payload = {**item, 'source': source['source'], 'account_id': account, 'id': 'src-' + str(uuid5(NAMESPACE_URL, identity))}
                candidate = self.briefing.build_source_item(payload)
                prepared.append({**payload, 'id': candidate[0].id})
            normalized.append({**source, 'items': prepared})
            source_start = source.get('window_start') or start
            for candidate in self.briefing.store.window_candidates(source_start, stamp, [(source['source'], account)]):
                rows[candidate[0].id] = candidate
            for payload in prepared:
                if datetime.fromisoformat(source_start.replace('Z', '+00:00')) <= datetime.fromisoformat(payload['occurred_at'].replace('Z', '+00:00')) <= datetime.fromisoformat(stamp.replace('Z', '+00:00')):
                    candidate = self.briefing.build_source_item(payload)
                    rows[candidate[0].id] = candidate
        try:
            draft = AgentBriefingGenerator(submissions or []).generate(list(rows.values()), self.briefing.list_rules(enabled_only=True), briefing_date=record['date'], window_start=start, window_end=stamp)
        except NeedModel as pending:
            return {'model': pending.request, 'runAt': stamp}
        result = jsonable(self.briefing.run_pipeline(briefing_date=record['date'], sources=normalized, draft=draft, run_at=stamp))
        document = {**record, 'id': request_id, 'body': draft['body'], 'title': draft['title'], 'revision': 1, 'archived': False, 'createdAt': stamp, 'updatedAt': stamp,
                    'business': {'briefingId': result['briefing']['id'], 'revision': result['revisions'][0]['number'], 'snapshot': result}}
        self.meta.execute('INSERT INTO reports VALUES (?,?,?)', (request_id, fingerprint, json.dumps(document, ensure_ascii=False)))
        self.meta.commit()
        return {'record': document}

    def write(self, record, expected_revision):
        record_id = str(UUID(record['id']))
        current = next((item for item in self.records() if item['id'] == record_id), None)
        if (current['revision'] if current else 0) != expected_revision:
            raise ValueError('记录已变化，请刷新后重试。')
        if current and current['kind'] != record['kind']:
            raise ValueError('不能改变记录类型。')
        if record['kind'] == 'task':
            payload = {'id': record_id, 'title': record['title'], 'notes': record['details'], 'due_on': record['due']}
            if current:
                self.tasks.update_todo(record_id, payload)
                if record['status'] != current['status']:
                    self.tasks.complete_todo(record_id, record['status'] == 'done')
            else:
                self.tasks.create_todo({**payload, 'status': 'completed' if record['status'] == 'done' else 'todo'})
        elif record['kind'] == 'event':
            zone = ZoneInfo(record['timeZone'])
            def instant(value):
                value = datetime.fromisoformat(value.replace('Z', '+00:00'))
                return (value.replace(tzinfo=zone) if value.tzinfo is None else value).astimezone(timezone.utc).isoformat()
            payload = {'id': record_id, 'title': record['title'], 'description': record['details'], 'start_at': instant(record['start']),
                       'end_at': instant(record['end']), 'all_day': record['allDay'], 'repeat_timezone': record['timeZone']}
            if current:
                self.calendar.update_event(record_id, payload)
            else:
                self.calendar.create_event(payload)
        elif record['kind'] == 'bill':
            digits = 0 if record['currency'] == 'JPY' else 2
            scaled = Decimal(record['amount']).scaleb(digits)
            if scaled != scaled.to_integral_value():
                raise ValueError('金额小数位不符合币种。')
            payload = {'id': record_id, 'name': record['title'], 'note': record['details'], 'amount_minor': int(scaled),
                       'currency': record['currency'], 'entry_type': record['direction'], 'occurred_at': record['date'] + 'T12:00:00+00:00',
                       'status': {'paid': 'posted', 'planned': 'pending', 'reversed': 'reversed'}[record['status']]}
            if current:
                if current['business']['status'] == 'reversed':
                    raise ValueError('冲正流水须通过账单业务接口编辑。')
                if current['date'] == record['date']:
                    payload['occurred_at'] = current['business']['occurred_at']
                self.bills.update_ledger(record_id, payload, expected_revision=current['business']['revision'])
            else:
                self.bills.create_ledger(payload)
        else:
            raise ValueError('不支持的业务类型。')
        return next(item for item in self.records() if item['id'] == record_id)

    def archive(self, record_id, expected_revision, archived):
        current = next((item for item in self.records() if item['id'] == record_id), None)
        if not current or current['revision'] != expected_revision:
            raise ValueError('记录已变化，请刷新后重试。')
        if current['kind'] == 'briefing':
            current.update(archived=archived, revision=current['revision'] + 1, updatedAt=now())
            self.meta.execute('UPDATE reports SET document=? WHERE id=?', (json.dumps(current, ensure_ascii=False), record_id))
            self.meta.commit()
            return current
        self.meta.execute('UPDATE projection SET archived=?,revision=revision+1,updated_at=? WHERE id=?', (int(archived), now(), record_id))
        self.meta.commit()
        return next(item for item in self.records() if item['id'] == record_id)

    def migrate(self, records):
        for record in records:
            if record['kind'] == 'briefing' or self.meta.execute('SELECT 1 FROM migration WHERE id=?', (record['id'],)).fetchone():
                continue
            current = next((item for item in self.records() if item['id'] == record['id']), None)
            if current is None:
                self.write(record, 0)
            self.meta.execute('UPDATE projection SET revision=?,archived=?,created_at=?,updated_at=? WHERE id=?',
                              (record['revision'], int(record['archived']), record['createdAt'], record['updatedAt'], record['id']))
            self.meta.execute('INSERT INTO migration VALUES (?)', (record['id'],))
            self.meta.commit()
        return True

    def due_reminders(self):
        from business.briefing.domain.outbox import dispatch_pending
        dispatch_pending(self.briefing.store, todos=self.tasks, calendar=self.calendar)
        for link in self.invoke('tasks', 'pending_scheduler_links'):
            if link['desired_at']:
                self.tasks.queue_scheduled_reminder(link['todo_id'], link['desired_at'])
        return self.tasks.claim_desktop_reminders()

    def local_source(self, source, run_at):
        kind = {'stella_tasks': 'task', 'stella_bills': 'bill'}[source]
        return {'source': source, 'account_id': 'local', 'items': [
            {'source_id': item['id'], 'title': item['title'], 'snippet': item['details'], 'occurred_at': run_at,
             'payload': {'body': item['details'], 'record': item}}
            for item in self.records() if item['kind'] == kind and not item['archived']],
            'cursors': {'until': run_at}, 'errors': []}

    def calendar_agenda(self, run_at):
        from datetime import timedelta
        start = datetime.fromisoformat(run_at.replace('Z', '+00:00'))
        events = self.calendar.list_events(start.isoformat(), (start + timedelta(days=1)).isoformat())
        return {'source': 'calendar_agenda', 'account_id': 'local', 'items': [
            {'source_id': event['id'], 'title': event['title'], 'sender': 'Stella 日程',
             'snippet': event['start_at'] + ' · ' + (event.get('location') or ''), 'occurred_at': run_at,
             'payload': {'body': event.get('description', ''), 'information_type': 'calendar_event',
                         'stella_calendar_event_id': event['id']}}
            for event in events], 'cursors': {'until': run_at}, 'errors': []}

    def source_settings(self, sources=None, expected_sources=None):
        if expected_sources is not None and self.source_settings() != expected_sources:
            raise ValueError('来源设置已在其他页面变化，请刷新后重试。')
        if sources is not None:
            self.meta.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('sources', json.dumps(sources, ensure_ascii=False)))
            self.meta.commit()
        row = self.meta.execute('SELECT document FROM settings WHERE key=?', ('sources',)).fetchone()
        return json.loads(row[0]) if row else [{'source': 'calendar_agenda', 'accountId': 'local', 'label': 'Stella 日程'}]

    def source_windows(self, date, run_at, sources):
        from business.briefing.domain.pipeline import window_start
        from business.briefing.domain.sources import source_key
        finish = datetime.fromisoformat(run_at.replace('Z', '+00:00')).astimezone()
        start = window_start(self.briefing, date, run_at, finish)
        existing = self.briefing.briefing_for_date(date)
        windows = existing.source_windows if existing else {}
        previous = sorted((row for row in self.briefing.list_briefings() if row.id[4:14] < date), key=lambda row: row.id, reverse=True)
        result = []
        for source in sources:
            key = source_key({'source': source['source'], 'account_id': source['accountId']})
            prior = next((row.source_windows[key]['end'] for row in previous if row.source_windows.get(key, {}).get('end')), None)
            result.append(windows.get(key, {}).get('start') or prior or start)
        return result

    def source_cursors(self, source, account_id):
        with self.briefing.store._conn() as db:
            return {row['cursor_key']: row['cursor_value'] for row in db.execute(
                'SELECT cursor_key,cursor_value FROM sync_state WHERE source=? AND account_id=?', (source, account_id))}

    def apple_peer(self, domain, method, args=None, kwargs=None):
        if domain not in ('tasks', 'calendar'):
            raise ValueError('Unknown Apple peer domain')
        allowed = {item['name'] for descriptor in self.entries[domain].SERVICE_DESCRIPTORS for item in descriptor['methods']}
        if method not in allowed:
            raise ValueError('Unknown Apple peer operation')
        return jsonable(getattr(getattr(self, domain), method)(*(args or []), **(kwargs or {})))


def main():
    business = Business(sys.argv[1])
    for line in sys.stdin:
        request = json.loads(line)
        try:
            action = request['action']
            if action == 'invoke':
                value = business.invoke(**request['input'])
            elif action in ('records', 'write', 'archive', 'migrate', 'local_briefing', 'generated_briefing', 'due_reminders', 'source_settings', 'source_windows', 'source_cursors', 'calendar_agenda', 'local_source', 'apple_peer'):
                value = getattr(business, action)(**request.get('input', {}))
            else:
                raise ValueError('Unknown business action')
            response = {'id': request['id'], 'ok': True, 'value': value}
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            response = {'id': request['id'], 'ok': False, 'error': str(error), 'errorType': type(error).__name__}
        print(json.dumps(jsonable(response), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
