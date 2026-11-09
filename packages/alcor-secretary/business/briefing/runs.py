"""Host-owned briefing checkpoints; never exposed as Agent business methods."""
import json
from datetime import datetime, timezone


ACTIVE = ('starting', 'collecting', 'model')


def stamp():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def public_run(run):
    keys = ('requestId', 'date', 'phase', 'message', 'sessionId', 'model', 'createdAt', 'updatedAt', 'briefingId')
    return {**{key: run[key] for key in keys if key in run},
            'completedSources': run.get('completedSources', len(run['collected'])), 'completedBatches': run.get('completedBatches', len(run['submissions']))}


class BriefingRuns:
    def __init__(self, business, serialize):
        self.business = business
        self.serialize = serialize
        self.db = business.meta
        self.db.execute('CREATE TABLE IF NOT EXISTS briefing_runs (id TEXT PRIMARY KEY, phase TEXT NOT NULL, document TEXT NOT NULL)')
        for run in self.active():
            if run['phase'] in ACTIVE:
                record = self.invoke('result', run['requestId'])
                patch = {'phase': 'completed', 'message': '简报已保存', 'briefingId': record['business']['briefingId']} if record else {'phase': 'interrupted', 'message': '生成已中断，可继续未完成的部分。'}
                self.save(run, patch)
        self.db.commit()

    def active(self):
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT document FROM briefing_runs WHERE phase IN ('starting','collecting','model')")]

    def get(self, request_id):
        row = self.db.execute('SELECT document FROM briefing_runs WHERE id=?', (request_id,)).fetchone()
        if not row:
            raise ValueError('简报任务不存在。')
        return json.loads(row[0])

    def save(self, run, patch):
        run = {**run, **patch, 'updatedAt': stamp()}
        if run['phase'] == 'completed':
            run.update(completedSources=run.get('completedSources', len(run['collected'])), completedBatches=run.get('completedBatches', len(run['submissions'])))
            run.update(collected=[], submissions=[], baseline={}, rules=[], sourceStates=[])
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO briefing_runs VALUES (?,?,?)', (run['requestId'], run['phase'], json.dumps(run, ensure_ascii=False)))
        return run

    def assert_current(self, run):
        current = self.business.briefing.briefing_for_date(run['date'])
        generated_at = current.generated_at if current else None
        baseline_at = run['baseline'].get('briefing', {}).get('generated_at')
        states = [self.business.source_cursors(source['source'], source['accountId'], source) for source in run['sources']]
        if generated_at != baseline_at or states != run['sourceStates'] or run['window']['previous'] != self.business.briefing.store.checkpoint():
            raise ValueError('中断期间简报或来源已有更新，请重新生成简报。')

    def invoke(self, method, request_id=None, **args):
        if method == 'status':
            row = self.db.execute("SELECT document FROM briefing_runs ORDER BY (phase IN ('starting','collecting','model')) DESC, rowid DESC LIMIT 1").fetchone()
            return public_run(json.loads(row[0])) if row else {'phase': 'idle'}
        if method == 'result':
            receipt = self.business.briefing.store.collection(request_id)
            if receipt:
                return self.business.collection_result(receipt)
            row = self.db.execute('SELECT document FROM reports WHERE id=?', (request_id,)).fetchone()
            return json.loads(row[0]) if row else None
        if method == 'get':
            return self.get(request_id)
        if method == 'save':
            return self.save(self.get(request_id), args['patch'])
        if method == 'stop':
            run = self.get(request_id)
            saved = self.invoke('result', request_id)
            if saved:
                return self.save(run, {'phase': 'completed', 'message': '简报已保存', 'briefingId': saved['business']['briefingId']})
            phase = 'interrupted' if args.get('interrupted') else 'cancelled'
            message = '生成已中断，可继续未完成的部分。' if phase == 'interrupted' else '简报生成已取消。'
            return self.save(run, {'phase': phase, 'message': message})
        if method != 'prepare':
            raise ValueError('Unknown briefing run operation')
        record, sources = args['record'], args['sources']
        existing = self.db.execute('SELECT document FROM briefing_runs WHERE id=?', (request_id,)).fetchone()
        if existing:
            run = json.loads(existing[0])
            if [run.get('requestedDate',run['date']), run['record']['notes'], run['sources']] != [record['date'], record['notes'], sources]:
                raise ValueError('同一操作标识不能用于不同简报或来源。')
            if run['phase'] in ACTIVE:
                raise ValueError('简报正在生成，请等待完成或取消。')
            saved = self.invoke('result', request_id)
            if saved:
                return self.save(run, {'phase': 'completed', 'message': '简报已保存', 'briefingId': saved['business']['briefingId']})
            if run.get('protocol') != 'single-agent-window-v2':
                raise ValueError('旧版简报任务无法继续，请新建简报任务；原记录和来源游标未更改。')
            self.assert_current(run)
            return run
        if self.active():
            raise ValueError('简报正在生成，请等待完成或取消。')
        run_at = stamp()
        window = self.business.collection_window(run_at)
        requested_date = record['date']
        record = {**record,'date':window['day']}
        current = self.business.briefing.briefing_for_date(record['date'])
        run = {'protocol':'single-agent-window-v2', 'requestedDate':requested_date, 'window':window, 'requestId': request_id, 'date': record['date'], 'record': record, 'sources': sources,
               'context': args.get('context', {}), 'phase': 'starting', 'message': '正在准备简报…',
               'createdAt': stamp(), 'runAt':run_at, 'collected': [], 'submissions': [],
               'rules': self.serialize(self.business.briefing.list_rules(enabled_only=True)),
               'baseline': self.serialize(self.business.briefing.get_briefing(current.id)) if current else {}}
        previous = self.db.execute('SELECT document FROM reports WHERE id=?', (request_id,)).fetchone()
        if previous:
            saved = json.loads(previous[0])
            if [saved['date'], saved['notes']] != [record['date'], record['notes']]:
                raise ValueError('同一操作标识不能用于不同简报。')
            return self.save(run, {'phase': 'completed', 'message': '简报已保存', 'briefingId': saved['business']['briefingId']})
        run['sourceStates'] = [self.business.source_cursors(source['source'], source['accountId'], source) for source in sources]
        run['windows'] = [window['start']] * len(sources)
        return self.save(run, {})
