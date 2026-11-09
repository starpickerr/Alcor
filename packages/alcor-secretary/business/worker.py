from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import traceback
from dataclasses import asdict, is_dataclass
from datetime import datetime, time, timedelta, timezone
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


from business.briefing.domain.window import collection_window, in_window


class Business:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.entries = {name: importlib.import_module(f'business.{name}.entry')
                        for name in ('tasks', 'calendar', 'bills', 'briefing')}
        from business.tasks.domain.service import TodoService
        from business.calendar.domain.service import CalendarService
        from business.bills.domain.service import BillsService
        from business.bills.domain.store import BillStore
        from business.briefing.domain.service import BriefingService
        from business.briefing.domain.store import BriefingStore
        self.tasks = TodoService(self.root / 'tasks.sqlite3')
        self.calendar = CalendarService(self.root / 'calendar.sqlite3')
        self.bills = BillsService(BillStore(self.root / 'bills.sqlite3'))
        self.briefing = BriefingService(BriefingStore(self.root / 'briefing.sqlite3'), self.tasks, self.calendar)
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
        from business.briefing.runs import BriefingRuns
        self.briefing_runs = BriefingRuns(self, jsonable)


    def invoke(self, domain, method, arguments=None, request_id=None):
        args = arguments if isinstance(arguments, dict) else {}
        if ((domain == 'tasks' and method in ('delete_todo', 'delete_category', 'delete_external_todo'))
                or (domain == 'bills' and method == 'delete_ledger')
                or (domain == 'calendar' and (method in ('delete_category', 'exclude_occurrence')
                    or (method == 'delete_event' and not (args.get('scope') == 'future' and isinstance(args.get('occurrence_on'), str)))))):
            raise PermissionError('缺少可验证的本次人工确认通道，不可恢复删除暂不可用。')
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
            methods = catalog(domain, entry, getattr(self, domain))
            name = (arguments or {}).get('method')
            if name is None:
                return methods
            found = [row for row in methods if row['name'] == name]
            if not found:
                raise ValueError('Unknown business operation')
            return found
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
                focus.setdefault(item['series_id'], {})[field] = {**{key: item[key] for key in ('id', 'due_on', 'status', 'completed_at')},'apple_history':bool(item.get('apple_history'))}
        for row in self.tasks.list_todos():
            if row['repeat_frequency'] != 'none' and (row['source_type'] != 'apple_reminder' or row.get('native_managed')):
                row = {**row, 'current_occurrence': ({key:row[key] for key in ('id','due_on','status','completed_at')} if row.get('native_managed') else None), **focus.get(row['id'], {})}
            records.append(self._metadata({'kind': 'task', 'id': row['id'], 'title': row['title'], 'details': row['notes'],
                                          'due': row['due_on'], 'status': 'done' if row['status'] in ('completed', 'cancelled') else 'open'}, row))
        for row in self.calendar.list_events():
            if row.get('series_id'):
                continue
            zone = ZoneInfo(row.get('repeat_timezone') or 'UTC')
            start, end = row['start_at'], row['end_at']
            if row['all_day']:
                local_start, local_end = (datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(zone) for value in (start, end))
                end_day = local_end.date()
                # Apple birthdays can end at 23:59:59; date records use an exclusive end.
                if local_end.time() != time.min:
                    end_day += timedelta(days=1)
                start, end = local_start.date().isoformat(), end_day.isoformat()
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
        by_day = {}
        for row in self.meta.execute('SELECT document FROM reports ORDER BY rowid'):
            report = json.loads(row[0])
            stable_id = by_day.get(report['date'],{}).get('id', report['id'])
            by_day[report['date']] = {**report,'id':stable_id}
        with self.briefing.store._conn() as db:
            receipts = db.execute('SELECT receipt FROM briefing_collections WHERE rowid IN (SELECT MAX(rowid) FROM briefing_collections GROUP BY briefing_id)').fetchall()
        for row in receipts:
            receipt = json.loads(row[0])
            report = self.collection_result(receipt, snapshot=False)
            day = report['date']
            with self.briefing.store._conn() as db:
                first_id = db.execute('SELECT request_id FROM briefing_collections WHERE briefing_id=? ORDER BY rowid LIMIT 1', (receipt['briefingId'],)).fetchone()[0]
            stable_id = by_day.get(day,{}).get('id', first_id)
            report['id'] = stable_id
            by_day[day] = self._metadata(report, report['business'])
        records.extend(by_day.values())
        return records

    def local_briefing(self, record, request_id):
        receipt = self.briefing.store.collection(request_id)
        if receipt:
            if receipt['record']['date'] != record['date'] or receipt['record']['notes'] != record['notes']:
                raise ValueError('同一操作标识不能用于不同简报。')
            return self.collection_result(receipt)
        fingerprint = json.dumps([record['date'], record['notes']], ensure_ascii=False)
        previous = self.meta.execute('SELECT * FROM reports WHERE id=?', (request_id,)).fetchone()
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise ValueError('同一操作标识不能用于不同简报。')
            return json.loads(previous['document'])
        stamp = now()
        window = self.collection_window(stamp)
        record = {**record, 'date':window['day']}
        configs = [row for row in self.source_settings() if row['source'] in ('calendar_agenda','alcor_tasks','alcor_bills')]
        sources = [self.calendar_agenda(stamp, window['start']) if row['source']=='calendar_agenda' else self.local_source(row['source'], stamp, window['start']) for row in configs]
        return self.generated_briefing(record, request_id, sources, run_at=stamp, configurations=configs, local_only=True)['record']

    def collection_window(self, stamp, time_zone=None):
        return collection_window(stamp, self.briefing.store.checkpoint(), time_zone or self.briefing.get_preferences().get('time_zone'))

    def collection_result(self, receipt, snapshot=True):
        window, record = receipt['window'], receipt['record']
        if not snapshot:
            briefing = self.briefing.store.get_briefing(receipt['briefingId'])
            return {**record, 'id':receipt['id'],'title':briefing.title,'body':briefing.body,'business':{'briefingId':briefing.id,'revision':receipt['revision']}}
        saved = self.briefing.store.get_revision(receipt['briefingId'], receipt['revision'])
        # A local schedule must not return external report contents from the same day.
        keys = set(receipt['sources'])
        def visible(row):
            return row['source'] + ':' + row['account_id'] in keys
        scoped = {**saved, 'items':[r for r in saved['items'] if visible(r)], 'mail':[r for r in saved['mail'] if visible(r)]}
        scoped['actions'] = self.briefing.actions.view(scoped['items'])
        scoped['preferences'] = self.briefing.get_preferences()
        scoped['revisions'] = [row for row in self.briefing.store.list_revisions(receipt['briefingId']) if row['number']<=receipt['revision']]
        scoped['briefing'] = {**saved['briefing'], 'body':saved['briefing']['body'] if receipt['body'] is None else receipt['body'], 'source_windows':{k:v for k,v in saved['briefing']['source_windows'].items() if k in keys}}
        return {**record, 'id':receipt['id'], 'date':window['day'], 'title':saved['briefing']['title'], 'body':saved['briefing']['body'] if receipt['body'] is None else receipt['body'],
                'revision':receipt['revision'], 'archived':False, 'createdAt':saved['briefing']['created_at'], 'updatedAt':saved['briefing']['updated_at'],
                'business':{'briefingId':receipt['briefingId'], 'revision':receipt['revision'], **({'snapshot':scoped} if snapshot else {})}}

    def generated_briefing(self, record=None, request_id=None, sources=None, submissions=None, run_at=None, configurations=None, rules_snapshot=None, baseline=None, validate_only=False, local_only=False):
        from business.briefing.domain.generator import AgentBriefingGenerator, NeedModel, rules_document
        run_row = self.meta.execute('SELECT document FROM briefing_runs WHERE id=?', (request_id,)).fetchone()
        run = json.loads(run_row[0]) if run_row else None
        if run and record is None:
            record, run_at = run['record'], run['runAt']
            sources, configurations = run['collected'], run['sources']
            rules_snapshot, baseline = run['rules'], run['baseline']
        stamp = run_at or now()
        previous = self.meta.execute('SELECT * FROM reports WHERE id=?', (request_id,)).fetchone()
        fingerprint = json.dumps([record['date'], record['notes']], ensure_ascii=False)
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise ValueError('同一操作标识不能用于不同简报。')
            return {'record': json.loads(previous['document'])}
        committed = self.briefing.store.collection(request_id)
        if committed:
            if committed['record']['notes'] != record['notes'] or committed['record']['date'] != record['date']:
                raise ValueError('同一操作标识不能用于不同简报。')
            return {'record':self.collection_result(committed)}
        window = run['window'] if run else self.collection_window(stamp)
        if run and (stamp!=window['end'] or run['phase'] not in ('collecting','model')):
            raise ValueError('简报任务已结束或时间窗口已改变。')
        record = {**record, 'date':window['day']}
        start = window['start']

        selected = list(sources)
        configurations = configurations or [{'source': row['source'], 'accountId': row.get('account_id') or 'default'} for row in selected]
        current = self.briefing.briefing_for_date(record['date'])
        detail = baseline if baseline is not None else (jsonable(self.briefing.get_briefing(current.id)) if current else {})
        from business.briefing.domain.models import FilterRule
        rules = [FilterRule(**rule) for rule in rules_snapshot] if rules_snapshot is not None else self.briefing.list_rules(enabled_only=True)
        local_items = []
        if record['notes']:
            local_items.append({'source_id': record['date'] + ':notes', 'title': '补充材料', 'snippet': record['notes'], 'occurred_at': stamp})
        if local_items:
            selected.append({'source': 'local', 'items': local_items, 'cursors': {'updated': stamp}})
        if not selected:
            raise ValueError('请选择简报来源，或填写补充材料。')
        from business.briefing.domain.incremental import daily_candidates, commit_daily, collection_stats
        stats = collection_stats()
        candidates = daily_candidates(self.briefing, selected, configurations, detail, window, stats)
        if detail.get('briefing', {}).get('rule_text') != rules_document(rules):
            candidates.decisions.clear()
            candidates.reports.clear()
            candidates.previous_reports.clear()
        try:
            if local_only:
                from business.briefing.domain.pipeline import compose_draft
                draft = compose_draft(list(candidates.rows.values()), rules, record['date'])
            else:
                draft = AgentBriefingGenerator(submissions or [], global_mode=True).generate(
                    list(candidates.rows.values()), rules, briefing_date=record['date'], window_start=start,
                    window_end=stamp, preserved_decisions=candidates.decisions, preserved_reports=candidates.reports,
                    previous_reports=candidates.previous_reports)
        except NeedModel as pending:
            return {'model': pending.request, 'runAt': stamp}
        if validate_only:
            return {'validated': True}
        if run:
            if run['phase'] not in ('collecting', 'model'):
                raise ValueError('简报任务已结束，不能提交迟到结果。')
            self.briefing_runs.assert_current(run)
        commit_daily(self.briefing,record,request_id,window,current,detail,draft,candidates,stats,
                     run.get('context',{}).get('trigger','manual') if run else 'manual')
        return {'record':self.collection_result(self.briefing.store.collection(request_id))}

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
            if not self.meta.execute('UPDATE projection SET archived=?,revision=revision+1,updated_at=? WHERE id=?',
                                     (int(archived),now(),record_id)).rowcount:
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

    @staticmethod
    def changed_ids(connect, table, window):
        # Only internal table names are supplied. Recheck exact instants after SQLite's millisecond comparison.
        with connect() as db:
            return [row[0] for row in db.execute(
                f'SELECT id FROM {table} WHERE julianday(updated_at) BETWEEN julianday(?) AND julianday(?)',
                (window['start'], window['end']))]

    def local_source(self, source, run_at, since=None):
        tasks = source == 'alcor_tasks'
        window = self.collection_window(run_at) if since is None else {'start':since,'end':run_at,'include_start':True}
        items = []
        if tasks:
            ids = self.changed_ids(self.tasks._connect, 'todos', window)
            rows = (self.tasks.get_todo(key) for key in ids)
        else:
            ids = self.changed_ids(self.bills.store._conn, 'financial_events', window)
            rows = filter(None, (self.bills.store.get_ledger(key) for key in ids))
        for row in rows:
            changed = row['updated_at']
            if not in_window(changed, window):
                continue
            archived = self.meta.execute('SELECT archived FROM projection WHERE id=?',(row['id'],)).fetchone()
            if archived and archived[0]:
                continue
            text = row['notes'] if tasks else row['note']
            fields = ('status','due_on','due_time','priority','repeat_frequency') if tasks else ('amount_minor','currency','entry_type','occurred_at','status')
            items.append({'source_id':row['id'],'title':row['title'] if tasks else row['name'],'snippet':text,
                          'occurred_at':row['created_at'], 'published_at':row['created_at'], 'source_updated_at':changed,
                          'payload':{'body':text, 'record':{field:row.get(field) for field in fields}}})
        return {'source':source,'account_id':'local','collected_at':now(),'items':items,'cursors':{'until':run_at},'errors':[]}

    def calendar_agenda(self, run_at, since=None):
        window = self.collection_window(run_at) if since is None else {'start':since,'end':run_at,'include_start':True}
        events = (self.calendar.get_event(key) for key in self.changed_ids(self.calendar._connect, 'calendar_events', window))
        return {'source':'calendar_agenda','account_id':'local','collected_at':now(),'items':[
            {'source_id':event['id'],'title':event['title'],'sender':'Alcor 日程',
             'snippet':event['start_at']+' · '+(event.get('location') or ''), 'occurred_at':event['created_at'],
             'published_at':event['created_at'], 'source_updated_at':event['updated_at'],
             'payload':{'body':event.get('description',''),'information_type':'calendar_event','alcor_calendar_event_id':event['id'],
                        'start_at':event['start_at'],'end_at':event['end_at'],'all_day':event['all_day']}}
            for event in events if in_window(event['updated_at'],window)], 'cursors':{'until':run_at},'errors':[]}

    def source_settings(self, sources=None, expected_sources=None):
        if expected_sources is not None and self.source_settings() != expected_sources:
            raise ValueError('来源设置已在其他页面变化，请刷新后重试。')
        if sources is not None:
            self.meta.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('sources', json.dumps(sources, ensure_ascii=False)))
            self.meta.commit()
        row = self.meta.execute('SELECT document FROM settings WHERE key=?', ('sources',)).fetchone()
        return json.loads(row[0]) if row else []

    def source_group_settings(self, groups=None, expected_groups=None):
        row = self.meta.execute('SELECT document FROM settings WHERE key=?', ('source_groups',)).fetchone()
        current = json.loads(row[0]) if row else {}
        if expected_groups is not None and current != expected_groups:
            raise ValueError('来源开关已在其他页面变化，请刷新后重试。')
        if groups is not None:
            self.meta.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('source_groups', json.dumps(groups, ensure_ascii=False)))
            self.meta.commit()
            return groups
        return current

    def source_windows(self, date, run_at, sources, time_zone=None):
        start = self.collection_window(run_at, time_zone)['start']
        return [start for _ in sources]

    def source_cursors(self, source, account_id, configuration=None):
        from business.briefing.domain.incremental import source_cursors
        return source_cursors(self.briefing.store, source, account_id, configuration)

    def list_inbound_recoveries(self, domain):
        if domain not in ('tasks', 'calendar'):
            raise ValueError('Unknown recovery domain')
        return getattr(self, domain).list_apple_inbound_recoveries()

    def restore_inbound_recovery(self, domain, recovery_id):
        if domain not in ('tasks', 'calendar') or not isinstance(recovery_id, str) or not recovery_id:
            raise ValueError('Invalid recovery request')
        return getattr(self, domain).restore_apple_inbound_recovery(recovery_id)

    def prepare_delete_receipt(self, domain, method, arguments, operation_id):
        from business.delete_receipt import prepare
        if domain not in ('tasks', 'calendar'): raise ValueError('Unknown deletion domain')
        return prepare(getattr(self, domain), domain, method, arguments, operation_id)

    def prepare_import_category_delete(self, domain, category_id, operation_id):
        from business.import_category_delete import prepare
        if domain not in ('tasks', 'calendar'): raise ValueError('Unknown import domain')
        return prepare(getattr(self, domain), category_id, operation_id)

    def commit_import_category_delete(self, domain, category_id, operation_id, snapshot):
        from business.import_category_delete import commit
        if domain not in ('tasks', 'calendar'): raise ValueError('Unknown import domain')
        return commit(getattr(self, domain), domain, category_id, operation_id, snapshot)

    def local_delete_prepare(self, domain, target_id, operation_id, preserve_children=False, category=False):
        from business.security_receipts import validate_operation_id
        validate_operation_id(operation_id)
        if category is True and domain in ('tasks', 'calendar') and preserve_children is False:
            return getattr(self, domain).prepare_local_category_delete(target_id, operation_id=operation_id)
        if category is not False: raise ValueError('Unknown local deletion')
        if domain == 'tasks':
            return self.tasks.prepare_local_todo_delete(target_id, preserve_children=preserve_children, operation_id=operation_id)
        if domain == 'calendar' and preserve_children is False:
            return self.calendar.prepare_local_event_delete(target_id, operation_id=operation_id)
        raise ValueError('Unknown local deletion')

    def local_delete_commit(self, domain, target_id, operation_id, snapshot='', preserve_children=False, category=False):
        from business.security_receipts import validate_operation_id
        validate_operation_id(operation_id)
        if category is True and domain in ('tasks', 'calendar') and preserve_children is False:
            return getattr(self, domain).commit_local_category_delete(target_id, snapshot=snapshot, operation_id=operation_id)
        if category is not False: raise ValueError('Unknown local deletion')
        if domain == 'tasks':
            return self.tasks.commit_local_todo_delete(target_id, snapshot=snapshot, preserve_children=preserve_children, operation_id=operation_id)
        if domain == 'calendar' and preserve_children is False:
            return self.calendar.commit_local_event_delete(target_id, snapshot=snapshot, operation_id=operation_id)
        raise ValueError('Unknown local deletion')

    def prepare_local_occurrence_delete(self, event_id, occurrence_on, scope, operation_id):
        return self.calendar.prepare_local_occurrence_delete(event_id, occurrence_on, scope, operation_id=operation_id)

    def commit_local_occurrence_delete(self, event_id, occurrence_on, scope, operation_id, snapshot=''):
        return self.calendar.commit_local_occurrence_delete(event_id, occurrence_on, scope, operation_id=operation_id, snapshot=snapshot)

    def prepare_apple_delete(self, domain, method, arguments, operation_id):
        from business.apple_delete_approval import prepare
        if domain not in ('tasks', 'calendar'): raise ValueError('Unknown Apple domain')
        return prepare(getattr(self, domain), domain, method, arguments, operation_id)

    def commit_apple_delete(self, domain, method, arguments, operation_id, snapshot, native_snapshot, confirmed_intent=None):
        from business.apple_delete_approval import commit
        if domain not in ('tasks', 'calendar'): raise ValueError('Unknown Apple domain')
        return commit(getattr(self, domain), domain, method, arguments, operation_id, snapshot, native_snapshot, confirmed_intent)

    def verify_confirmed_delete_finish(self, domain, operation_id, plan):
        from business.apple_delete_approval import verify_confirmed_finish
        if domain not in ('tasks', 'calendar'): raise ValueError('Unknown Apple domain')
        return verify_confirmed_finish(getattr(self, domain), domain, operation_id, plan)

    def prepare_confirmed_delete(self, domain, operation_id):
        from business.apple_delete_approval import prepare_confirmed
        if domain not in ('tasks', 'calendar'): raise ValueError('Unknown Apple domain')
        return prepare_confirmed(getattr(self, domain), domain, operation_id)

    def commit_bill_delete(self, entry_id, operation_id, revision, snapshot):
        if not isinstance(entry_id, str) or not entry_id or not isinstance(operation_id, str) or len(operation_id) != 64 or any(c not in '0123456789abcdef' for c in operation_id):
            raise ValueError('Invalid deletion operation identity')
        if type(revision) is not int or revision < 1 or not isinstance(snapshot, dict) or snapshot.get('id') != entry_id:
            raise ValueError('Invalid approved bill snapshot')
        return self.bills.commit_approved_ledger_delete(entry_id, operation_id, revision, snapshot)

    def verify_bill_delete_replay(self, entry_id, operation_id, expected_revision=None):
        # Private host-to-owner route, deliberately absent from the business catalog.
        if not isinstance(entry_id, str) or not entry_id or not isinstance(operation_id, str) or len(operation_id) != 64 or any(c not in '0123456789abcdef' for c in operation_id):
            raise ValueError('Invalid deletion operation identity')
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
            raise ValueError('Invalid deletion revision')
        return self.bills.verify_deleted_ledger(entry_id, operation_id, expected_revision)

    def item_restore_peer(self, domain, method, args=None, kwargs=None):
        counts={'apple_capture_item_restore':5,'apple_restore_item':2,'apple_verify_item_restore':2}
        if domain not in ('tasks','calendar') or method not in counts or not isinstance(args,list) or len(args)!=counts[method] or kwargs not in (None,{}):
            raise ValueError('Unknown local restore peer operation')
        return jsonable(getattr(getattr(self,domain),method)(*args))

    def bound_conflict_peer(self, domain, method, args=None, kwargs=None):
        if (domain not in ('tasks', 'calendar') or method not in ('apple_commit_bound_conflict', 'apple_verify_bound_conflict')
                or not isinstance(args, list) or len(args) != 4 or kwargs not in (None, {})):
            raise ValueError('Unknown bound conflict peer operation')
        return jsonable(getattr(getattr(self, domain), method)(*args))

    def apple_peer(self, domain, method, args=None, kwargs=None):
        if domain not in ('tasks', 'calendar'):
            raise ValueError('Unknown Apple peer domain')
        allowed = {item['name'] for descriptor in self.entries[domain].SERVICE_DESCRIPTORS for item in descriptor['methods']}
        allowed.update({'upsert_external_event','apple_prepare_calendar_rebind','apple_commit_calendar_rebind','apple_verify_calendar_move','apple_commit_calendar_move','apple_capture_calendar_sync','apple_capture_calendar_missing','apple_remove_calendar_inbound','apple_apply_calendar_inbound','apple_prepare_ordinary_calendar_create','apple_verify_ordinary_calendar_create','apple_finalize_ordinary_calendar_create','apple_local_only_categories','apple_begin_operation','apple_bind', 'apple_capture_unbound', 'apple_prime_unbound','apple_operations','apple_operation_error','apple_finish_operation','apple_apply_category','apple_apply_metadata','apple_restore_item','apple_apply_item','apple_place_item','apple_exclude','apple_record_completion','apple_remove_item','apple_remove_category','apple_capture_category_absence','apple_remove_category_if_unchanged','apple_restore_category','apple_capture_legacy_category','apple_restore_legacy_category','apple_bind_marker_if_unchanged','apple_capture_marker_binding'})
        allowed.update({'apple_connect_category','apple_category_incarnation','apple_commit_reminder_category_rebind','apple_category_absence_targets','apple_apply_category_if_unchanged','apple_reject_reminder_completion', 'apple_apply_reminder_move', 'apple_begin_reminder_completion', 'apple_capture_reminder_completion', 'apple_finalize_reminder_completion', 'apple_capture_reminder_sync', 'apple_verify_reminder_sync', 'apple_apply_reminder_inbound', 'apple_capture_reminder_missing', 'apple_remove_reminder_inbound'})
        allowed.update({'apple_capture_approved_export', 'apple_finalize_approved_export', 'apple_verify_import_unlink'})
        allowed.difference_update({'apple_capture_item_restore','apple_restore_item','apple_verify_item_restore', 'apple_commit_bound_conflict', 'apple_verify_bound_conflict'})
        if method not in allowed:
            raise ValueError('Unknown Apple peer operation')
        return jsonable(getattr(getattr(self, domain), method)(*(args or []), **(kwargs or {})))


def main():
    business = Business(sys.argv[1])
    for line in sys.stdin:
        request = json.loads(line)
        try:
            action = request['action']
            if action == 'briefing_run':
                value = business.briefing_runs.invoke(**request['input'])
            elif action == 'list_inbound_recoveries':
                value = business.list_inbound_recoveries(**request['input'])
            elif action == 'restore_inbound_recovery':
                value = business.restore_inbound_recovery(**request['input'])
            elif action == 'prepare_delete_receipt':
                value = business.prepare_delete_receipt(**request['input'])
            elif action in ('prepare_import_category_delete', 'commit_import_category_delete'):
                value = getattr(business, action)(**request['input'])
            elif action == 'local_delete_prepare':
                value = business.local_delete_prepare(**request['input'])
            elif action == 'local_delete_commit':
                value = business.local_delete_commit(**request['input'])
            elif action == 'prepare_local_occurrence_delete':
                value = business.prepare_local_occurrence_delete(**request['input'])
            elif action == 'commit_local_occurrence_delete':
                value = business.commit_local_occurrence_delete(**request['input'])
            elif action == 'prepare_apple_delete':
                value = business.prepare_apple_delete(**request['input'])
            elif action == 'verify_confirmed_delete_finish':
                value = business.verify_confirmed_delete_finish(**request['input'])
            elif action == 'prepare_confirmed_delete':
                value = business.prepare_confirmed_delete(**request['input'])
            elif action == 'commit_apple_delete':
                value = business.commit_apple_delete(**request['input'])
            elif action in ('apple_begin_approved_delete', 'apple_finish_approved_delete', 'apple_reset_approved_delete'):
                inputs = dict(request['input'])
                domain = inputs.pop('domain')
                if domain not in ('tasks', 'calendar'): raise ValueError('Unknown Apple domain')
                value = getattr(getattr(business, domain), action)(**inputs)
            elif action == 'commit_bill_delete':
                value = business.commit_bill_delete(**request['input'])
            elif action == 'verify_bill_delete_replay':
                value = business.verify_bill_delete_replay(**request['input'])
            elif action == 'invoke':
                value = business.invoke(**request['input'])
            elif action in ('item_restore_peer', 'bound_conflict_peer'):
                value = getattr(business, action)(**request['input'])
            elif action in ('records', 'write', 'archive', 'migrate', 'local_briefing', 'generated_briefing', 'due_reminders', 'source_settings', 'source_group_settings', 'source_windows', 'source_cursors', 'calendar_agenda', 'local_source', 'apple_peer'):
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
