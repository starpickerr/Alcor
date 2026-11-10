import test from 'node:test'
import assert from 'node:assert/strict'
import { connectionStatus, formatSyncTime } from '../../packages/alcor-apple-sync/src/presentation.js'

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

test('legacy deletion remains visible as needing review without inventing a target', async () => {
  const { reviewItems } = await import('../../packages/alcor-apple-sync/src/presentation.js')
  const state = { authorization:'full_access', connected:true, auto_enabled:true, legacy_deletions:[{alcor_id:'synthetic',reason:'legacy retained'}] }
  assert.equal(connectionStatus(state,false).label,'部分同步 · 待处理')
  const [item] = reviewItems(state,'calendar')
  assert.equal(item.type,'legacy_deletion')
  assert.equal(item.title,'旧版日程删除待核对')
  assert.equal(item.reason,'legacy retained')
})

test('review rows retain all actionable types and use current category values without duplicating names', async () => {
  const { reviewItems, resolutionRequest } = await import('../../packages/alcor-apple-sync/src/presentation.js')
  assert.deepEqual(reviewItems(null, 'calendar'), [])
  const state = { categories: [{id:'local', name:'工作'}], calendars:[{id:'apple',title:'项目'}], links:[{apple_id:'apple',alcor_id:'local',name_conflict:'changed'}], category_conflicts:[{apple_id:'apple',alcor_id:'local',kind:'name'}], conflicts:[{alcor_id:'event',title:'开会'}], operations:[{id:'op',action:'delete',document:{title:'旧日程'}}] }
  const items = reviewItems(state,'calendar')
  assert.equal(items.length,3)
  assert.equal(items[0].title,'工作')
  assert.equal(items[0].native.title,'项目')
  assert.equal(items[2].title,'旧日程')
  assert.deepEqual(resolutionRequest(items[0],'calendar','keep_apple'),{method:'resolve_category_conflict',arguments:{apple_id:'apple',field:'name',choice:'keep_apple'}})
  assert.deepEqual(resolutionRequest(items[1],'calendar','keep_alcor'),{method:'resolve_conflict',arguments:{alcor_id:'event',choice:'keep_alcor'}})
  assert.deepEqual(resolutionRequest(items[2],'calendar','cancel'),{method:'cancel_operation',arguments:{operation_id:'op'}})
  assert.deepEqual(resolutionRequest({type:'record',todo_id:'task'},'reminders','keep_apple').arguments,{todo_id:'task',choice:'keep_apple'})
  const missing={type:'category',kind:'missing_source',apple_id:'old'}
  assert.deepEqual(resolutionRequest(missing,'calendar','rebind:new').arguments,{apple_id:'old',choice:'rebind',replacement_id:'new'})
  assert.deepEqual(resolutionRequest(missing,'calendar','keep_alcor','icloud').arguments,{apple_id:'old',choice:'keep_alcor',source_id:'icloud'})
  assert.equal(connectionStatus({authorization:'full_access',links:[{name_conflict:'changed'}]},false).tone,'warning')
  assert.equal(connectionStatus({authorization:'full_access',connected:true,auto_enabled:true},false,true).label,'状态读取失败')
  assert.equal(connectionStatus({authorization:'full_access',readonly_issues:[{}]},false).label,'部分同步 · 含只读内容')
})

test('conflict titles are local read-only summaries and tolerate missing records', async () => {
  const { execFile } = await import('node:child_process')
  const { promisify } = await import('node:util')
  const { fileURLToPath } = await import('node:url')
  const source=fileURLToPath(new URL('../../packages/alcor-apple-sync/python',import.meta.url))
  await promisify(execFile)('python3',['-c',`import sys\nsys.path.insert(0,${JSON.stringify(source)})\nfrom apple_sync.metadata import conflict_summaries\nassert conflict_summaries([],lambda identity:1/0,'alcor_id')==[]\nrows=conflict_summaries([{'todo_id':'a','conflict':'changed'},{'todo_id':'missing','conflict':'deleted'}],lambda identity:{'a':{'id':'a','title':'练习','category_id':'plan'}}[identity],'todo_id')\nassert rows[0]=={'todo_id':'a','reason':'changed','title':'练习','category_id':'plan','local_missing':False,'local_restore_pending':False}\nassert rows[1]['title'] is None and rows[1]['local_missing'] is True and rows[1]['local_restore_pending'] is False\n`])
})

test('identity candidates and unknown owner commits are visible and cannot dispatch recovery writes', async () => {
  const { reviewItems, resolutionRequest } = await import('../../packages/alcor-apple-sync/src/presentation.js')
  const state={authorization:'full_access',connected:true,auto_enabled:true,
    identity_candidates:[{key:'candidate',state:'pending',native:{title:'保留候选',list_id:'b',source_id:'account2'},reason:'identity differs'}],
    scan_claims:[{attempt_id:'claim',group_id:'a',source_id:'account1',state:'uncertain',stage:'owner_claiming',reason:'lost response'}]}
  assert.equal(connectionStatus(state,false).tone,'warning')
  const items=reviewItems(state,'reminders')
  assert.equal(items.length,2);assert.equal(items[0].title,'保留候选');assert.equal(items[0].reason,'identity differs')
  assert.equal(items[1].label,'本地提交结果待核对');assert.equal(items[1].stage,'owner_claiming');assert.equal(items[1].reason,'lost response')
  for(const item of items) for(const choice of ['keep_alcor','keep_apple','cancel']) assert.throws(()=>resolutionRequest(item,'reminders',choice),/不允许重发/)
  assert.equal(reviewItems({scan_claims:[{attempt_id:'active',state:'scanning',stage:'claimed'}]},'calendar')[0].label,'同步操作仍占用此来源')
})

test('category create unknown and proven pre-send waiting stay visible without retry buttons', async () => {
  const { reviewItems, resolutionRequest } = await import('../../packages/alcor-apple-sync/src/presentation.js')
  for (const state of ['native_unknown', 'owner_claiming', 'owner_committed', 'legacy_unknown', 'waiting']) {
    const status={authorization:'full_access',connected:true,auto_enabled:true,category_creates:[{local_id:'category',title:'Work',source_id:'source',state,reason:'retained'}]}
    assert.equal(connectionStatus(status,false).tone,'warning')
    const [item]=reviewItems(status,'calendar')
    assert.equal(item.type,'category_create');assert.equal(item.title,'Work');assert.equal(item.reason,'retained')
    assert.throws(()=>resolutionRequest(item,'calendar','keep_alcor'),/不允许重发/)
  }
})


test('known marker rollback is visible without granting recovery or retry', async () => {
  const { reviewItems, resolutionRequest } = await import('../../packages/alcor-apple-sync/src/presentation.js')
  for (const kind of ['calendar','reminders']) {
    const state={authorization:'full_access',connected:true,auto_enabled:true,marker_conflicts:[{local_id:'local',title:'Retained marker',group_id:'a',source_id:'s',state:'marker_conflict',reason:'changed'}]}
    assert.equal(connectionStatus(state,false).tone,'warning')
    const [item]=reviewItems(state,kind)
    assert.equal(item.type,'marker_conflict');assert.equal(item.title,'Retained marker')
    for (const choice of ['keep_alcor','keep_apple','cancel']) assert.throws(()=>resolutionRequest(item,kind,choice),/不允许重发/)
  }
})

test('bound conflict receipts remain reachable without offering a different write choice',async()=>{
  const {reviewItems,resolutionRequest,canRestoreLocalItem,boundConflictPending}=await import('../../packages/alcor-apple-sync/src/presentation.js')
  for(const choice of ['keep_apple','keep_alcor'])for(const phase of ['owner_uncertain','native_saved','native_uncertain','finalizing']){
    const [item]=reviewItems({conflicts:[{alcor_id:'exact-id',local_missing:true,bound_conflict_pending:{choice,phase}}]},'calendar')
    assert.equal(item.label,'冲突处理结果待核对');assert.equal(canRestoreLocalItem(item),false)
    assert.deepEqual(resolutionRequest(item,'calendar',choice),{method:'resolve_conflict',arguments:{alcor_id:'exact-id',choice}})
    assert.throws(()=>resolutionRequest(item,'calendar',choice==='keep_apple'?'keep_alcor':'keep_apple'),/只能核对/)
    assert.equal(boundConflictPending({...item,bound_conflict_pending:{choice,phase:'guess'}}),undefined)
  }
})
