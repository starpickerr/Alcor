import test from 'node:test'
import assert from 'node:assert/strict'
import { connectionStatus, formatSyncTime } from '../../packages/stella-apple-sync/src/presentation.js'

test('Apple status distinguishes a running operation from enabled background sync, pause and failures', () => {
  const state = { authorization: 'full_access', connected: true, auto_enabled: true }
  assert.equal(connectionStatus(null, false).label, '读取中')
  assert.equal(connectionStatus(null, false, true).tone, 'error')
  assert.equal(connectionStatus(state, true).label, '处理中')
  assert.equal(connectionStatus(state, false).label, '自动同步已开启')
  assert.equal(connectionStatus({ ...state, connected: false }, false).label, '未开启')
  assert.equal(connectionStatus({ ...state, connected: false, last_success: '2026-11-06T00:00:00Z' }, false).label, '已暂停')
  assert.equal(connectionStatus({ ...state, auto_enabled: false, links: [{}] }, false).label, '已暂停')
  assert.equal(connectionStatus({ ...state, authorization: 'not_determined' }, false).label, '待授权')
  assert.equal(connectionStatus({ ...state, authorization: 'denied' }, false).label, '无法同步')
  assert.equal(connectionStatus({ ...state, error: 'Read failed' }, false).tone, 'error')
  assert.equal(connectionStatus({ ...state, conflicts: [{}] }, false).label, '待处理冲突')
})

test('Apple sync times use Chinese dates and a 24-hour local clock, with a safe empty state', () => {
  assert.equal(formatSyncTime(null), null)
  assert.equal(formatSyncTime('invalid'), null)
  assert.equal(formatSyncTime(new Date(2026, 10, 6, 0, 5).toISOString()), '2026/11/06 00:05')
  assert.equal(formatSyncTime(new Date(2026, 10, 6, 17, 9).toISOString()), '2026/11/06 17:09')
})

test('review rows retain all actionable types and use current category values without duplicating names', async () => {
  const { reviewItems, resolutionRequest } = await import('../../packages/stella-apple-sync/src/presentation.js')
  assert.deepEqual(reviewItems(null, 'calendar'), [])
  const state = { categories: [{id:'local', name:'工作'}], calendars:[{id:'apple',title:'项目'}], links:[{apple_id:'apple',stella_id:'local',name_conflict:'changed'}], category_conflicts:[{apple_id:'apple',stella_id:'local',kind:'name'}], conflicts:[{stella_id:'event',title:'开会'}], operations:[{id:'op',action:'delete',document:{title:'旧日程'}}] }
  const items = reviewItems(state,'calendar')
  assert.equal(items.length,3)
  assert.equal(items[0].title,'工作')
  assert.equal(items[0].native.title,'项目')
  assert.equal(items[2].title,'旧日程')
  assert.deepEqual(resolutionRequest(items[0],'calendar','keep_apple'),{method:'resolve_category_conflict',arguments:{apple_id:'apple',field:'name',choice:'keep_apple'}})
  assert.deepEqual(resolutionRequest(items[1],'calendar','keep_stella'),{method:'resolve_conflict',arguments:{stella_id:'event',choice:'keep_stella'}})
  assert.deepEqual(resolutionRequest(items[2],'calendar','cancel'),{method:'cancel_operation',arguments:{operation_id:'op'}})
  assert.deepEqual(resolutionRequest({type:'record',todo_id:'task'},'reminders','keep_apple').arguments,{todo_id:'task',choice:'keep_apple'})
  const missing={type:'category',kind:'missing_source',apple_id:'old'}
  assert.deepEqual(resolutionRequest(missing,'calendar','rebind:new').arguments,{apple_id:'old',choice:'rebind',replacement_id:'new'})
  assert.deepEqual(resolutionRequest(missing,'calendar','keep_stella','icloud').arguments,{apple_id:'old',choice:'keep_stella',source_id:'icloud'})
  assert.equal(connectionStatus({authorization:'full_access',links:[{name_conflict:'changed'}]},false).tone,'warning')
  assert.equal(connectionStatus({authorization:'full_access',connected:true,auto_enabled:true},false,true).label,'状态读取失败')
  assert.equal(connectionStatus({authorization:'full_access',readonly_issues:[{}]},false).label,'部分同步 · 含只读内容')
})

test('conflict titles are local read-only summaries and tolerate missing records', async () => {
  const { execFile } = await import('node:child_process')
  const { promisify } = await import('node:util')
  const { fileURLToPath } = await import('node:url')
  const source=fileURLToPath(new URL('../../packages/stella-apple-sync/python',import.meta.url))
  await promisify(execFile)('python3',['-c',`import sys\nsys.path.insert(0,${JSON.stringify(source)})\nfrom apple_sync.metadata import conflict_summaries\nassert conflict_summaries([],lambda identity:1/0,'stella_id')==[]\nrows=conflict_summaries([{'todo_id':'a','conflict':'changed'},{'todo_id':'missing','conflict':'deleted'}],lambda identity:{'a':{'id':'a','title':'练习','category_id':'plan'}}[identity],'todo_id')\nassert rows[0]=={'todo_id':'a','reason':'changed','title':'练习','category_id':'plan'}\nassert rows[1]['title'] is None\n`])
})
