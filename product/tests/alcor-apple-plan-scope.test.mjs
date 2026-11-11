import { python as testPython } from './platform-python.mjs'
import test from 'node:test'
import assert from 'node:assert/strict'
import { applePlanDetail } from '../../packages/alcor-secretary/src/security-apple-export-gate.js'

const plan = (series, mode = 'conflict', choice = 'keep_alcor') => ({
  kind: 'calendar', mode, arguments: { choice }, skipped: 0,
  items: [{ action: choice === 'keep_apple' ? 'local_update' : 'update', local_id: 'local', source_id: 'source', group_id: 'group',
    wire_payload: { event_id: 'exact-series-id', occurrence_date: '2026-11-05T01:00:00Z', series, title: 'Changed' },
    destination_snapshot: {}, mapping_snapshot: {}, native_snapshot: {} }],
})
for (const mode of ['conflict', 'one_time']) {
  for (const series of [true, false]) {
    test(`${mode}: readable ${series ? 'future' : 'this'} scope comes from the frozen wire payload`, () => {
      const frozen = plan(series, mode), before = JSON.stringify(frozen), detail = applePlanDetail(frozen)
      assert.match(detail, new RegExp(`Apple 写入范围：${series ? '本次及此后日程' : '仅本次日程'}`))
      assert.match(detail, /起始实例 "2026-11-05T01:00:00Z"/)
      assert.match(detail, /系列\/对象 "exact-series-id"/)
      assert.equal(JSON.stringify(frozen), before)
    })
  }
}
test('local-only conflict, reminders and new items do not claim an EventKit update range', () => {
  assert.doesNotMatch(applePlanDetail(plan(true, 'conflict', 'keep_apple')), /Apple 写入范围/)
  const reminder = { ...plan(true), kind: 'reminders' }
  assert.doesNotMatch(applePlanDetail(reminder), /Apple 写入范围/)
  const create = plan(true, 'one_time'); create.items[0].action = 'create'
  assert.doesNotMatch(applePlanDetail(create), /Apple 写入范围/)
  assert.match(applePlanDetail({ mode: 'receipt_finish', plan: plan(true) }), /仅核对原操作回执，不再次写入/)
})

test('real owner/manager preview retains default future and explicit this, with readable scope', async () => {
  const { execFileSync } = await import('node:child_process')
  const output = execFileSync(testPython, ['-B', '-c', `
import json,runpy
M=runpy.run_path('product/tests/apple-approved-export.py')
c=M['ApprovedExport']();c.setUp()
try:
 o,b,s,category,create=c.fixture('calendar')
 s.replace_links([{'apple_id':'a','alcor_id':category['id'],'direction':'bidirectional','enabled':True}])
 local=create();o.update_event(local['id'],{'repeat_frequency':'weekly'})
 first=c.plan(s,'calendar',[local['id']]);r=M['run'](s,'calendar',first,lambda *args:True)
 assert r['status']=='success',r
 o.update_event(local['id'],{'title':'Local changed'})
 b.rows[0]['title']='Apple changed';b.rows[0]['last_modified']='2026-11-08T00:00:00Z'
 s.sync();assert s._maps()[0]['state']=='conflict'
 plans=[]
 for scope,op in [(None,'a'),('this','b'),('future','c')]:
  if scope:o.update_event(local['id'],{'edit_scope':scope})
  plans.append(M['prepare'](s,'calendar','resolve_conflict',{'kind':'calendar','identity':local['id'],'choice':'keep_alcor'},op*64))
 print(json.dumps(plans))
finally:c.tearDown()
`], { cwd: process.cwd(), encoding: 'utf8', maxBuffer: 1024 * 1024 })
  const plans = JSON.parse(output)
  assert.deepEqual(plans.map(row => row.items[0].wire_payload.series), [true, false, true])
  for (const [index, row] of plans.entries()) {
    const detail = applePlanDetail(row)
    assert.match(detail, index === 1 ? /Apple 写入范围：仅本次日程/ : /Apple 写入范围：本次及此后日程/)
    assert.ok(detail.includes(JSON.stringify(row.items[0].wire_payload.occurrence_date)))
    assert.ok(detail.includes(JSON.stringify(row.items[0].wire_payload.event_id)))
  }
})
