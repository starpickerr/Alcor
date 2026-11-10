import test from 'node:test'
import assert from 'node:assert/strict'
import { createCategoryRebuildPage } from '../../packages/alcor-apple-sync/src/category-rebuild-page.js'
import { createCategoryRebuildHost } from '../../packages/alcor-apple-sync/src/category-rebuild-host.js'
import { rebuildCommitSchema, rebuildPreviewSchema } from '../../packages/alcor-apple-sync/src/remote.js'
import { reviewItems, connectionStatus, resolutionRequest } from '../../packages/alcor-apple-sync/src/presentation.js'
const plan = {ticket:'00000000-0000-4000-8000-000000000000',kind:'calendar',apple_id:'old',source_id:'source',source:{id:'source',title:'Account'},title:'Work',local_id:'local',snapshot:'a'.repeat(64),mode:'empty_container_only',retained_members:[{id:'item',title:'Retain me'}]}
const ok=value=>({ok:true,value})
function fixture(overrides={}) {
  const calls=[]
  const api={cancelCategoryRebuild:async()=>ok(true),previewCategoryRebuild:async input=>{calls.push(['preview',input]);return ok(structuredClone(plan))},rebuildCategory:async input=>{calls.push(['commit',input]);return ok({retained_categories:[{local_id:'local'}]})},...overrides}
  return {calls,page:createCategoryRebuildPage({api,kind:'calendar',appleId:'old'})}
}

test('preview then exact confirmed empty-container snapshot, no generic invoke and no repeated commit',async()=>{
  const {page,calls}=fixture();page.activate()
  await assert.rejects(page.confirm(plan),/预览/)
  const prepared=await page.preview('source');assert.deepEqual(calls.map(x=>x[0]),['preview'])
  assert.ok(Object.isFrozen(prepared));assert.ok(Object.isFrozen(prepared.retained_members[0]))
  await assert.rejects(page.confirm({...prepared}),/预览/)
  await page.confirm(prepared)
  assert.deepEqual(calls[1],['commit',{ticket:plan.ticket}])
  await assert.rejects(page.confirm(prepared),/预览/);assert.equal(calls.length,2)
})
test('closing or Back invalidates prepared data; late preview cannot dispatch',async()=>{
  for(const action of ['dispose','invalidate']) {
    const {page,calls}=fixture();const prepared=await page.preview('source');page[action]()
    await assert.rejects(page.confirm(prepared),/预览/);assert.equal(calls.length,1)
  }
  let finish
  const {page,calls}=fixture({previewCategoryRebuild:()=>new Promise(resolve=>{finish=resolve})})
  const pending=page.preview('source');page.dispose();finish(ok(structuredClone(plan)))
  await assert.rejects(pending,/失效/);assert.equal(calls.length,0)
  page.activate();await assert.rejects(page.confirm(plan),/预览/)
})
test('unknown commit result cannot reuse confirmation; target mismatch is rejected',async()=>{
  const {page}=fixture({rebuildCategory:async()=>{throw new Error('response lost')}})
  const prepared=await page.preview('source');await assert.rejects(page.confirm(prepared),/response lost/)
  await assert.rejects(page.confirm(prepared),/预览/)
  const wrong=fixture({previewCategoryRebuild:async()=>ok({...plan,source_id:'other'})})
  await assert.rejects(wrong.page.preview('source'),/身份/)
})
test('double click has one page dispatch',async()=>{
  let finish
  const {page}=fixture({rebuildCategory:()=>new Promise(resolve=>{finish=resolve})})
  const prepared=await page.preview('source');const pending=page.confirm(prepared)
  await assert.rejects(page.confirm(prepared),/预览/);finish(ok({}));await pending

})
test('retained members are visible as incomplete association, with no destructive or upload shortcut',()=>{
  const state={authorization:'full_access',connected:true,retained_categories:[{old_group:'old',new_group:'new',member_count:3,title:'Work',source_id:'source',state:'members_retained',reason:'Only an empty container was created'}]}
  assert.equal(connectionStatus(state,false).tone,'warning')
  const [row]=reviewItems(state,'calendar');assert.match(row.label,/3 项成员保留本地/)
  for(const choice of ['keep_alcor','keep_apple','cancel'])assert.throws(()=>resolutionRequest(row,'calendar',choice),/不允许重发/)
})

function hostFixture() {
  const calls=[],controller=new AbortController(),peerController=new AbortController()
  const peer={principal:{kind:'owner'},signal:peerController.signal}
  const invocation={signal:controller.signal,peer}
  let time=100
  const host=createCategoryRebuildHost(()=>({call:async(action,input)=>{calls.push([action,input]);return action==='preview_category_rebuild'?structuredClone(plan):{saved:true}}}),()=>time)
  return {host,calls,invocation,controller,peerController,setTime:v=>{time=v}}
}
const scope={kind:'calendar',apple_id:'old',source_id:'source'}
test('Host retains exact prepared scope; one-use ticket is bound to actual owner peer',async()=>{
  const {host,calls,invocation}=hostFixture()
  for(const kind of ['model','plugin','unknown'])await assert.rejects(host.preview({...invocation,peer:{...invocation.peer,principal:{kind}}},scope),/所有者/)
  const prepared=await host.preview(invocation,scope)
  await assert.rejects(host.commit({...invocation,peer:{...invocation.peer}},{ticket:prepared.ticket}),/失效/)
  assert.equal(calls.length,1)
  await assert.rejects(host.commit(invocation,{ticket:prepared.ticket}),/可信页面或单次批准/)
  assert.equal(calls.length,1)
  await assert.rejects(host.commit(invocation,{ticket:prepared.ticket}),/失效/)
  assert.equal(calls.length,1)
  assert.throws(()=>rebuildCommitSchema.parse({...scope,snapshot:plan.snapshot,ticket:prepared.ticket}))
  assert.throws(()=>rebuildPreviewSchema.parse({...scope,owner:true}))
})
test('Host expired, canceled, closed or disconnected confirmation never dispatches',async()=>{
  for(const mode of ['expired','cancel','closed','disconnected','invocation-aborted']) {
    const f=hostFixture(),prepared=await f.host.preview(f.invocation,scope)
    if(mode==='expired')f.setTime(300101)
    if(mode==='cancel')assert.equal(f.host.cancel(f.invocation,{ticket:prepared.ticket}),true)
    if(mode==='closed')f.host.close()
    if(mode==='disconnected')f.peerController.abort()
    if(mode==='invocation-aborted')f.controller.abort()
    await assert.rejects(f.host.commit(f.invocation,{ticket:prepared.ticket}))
    assert.equal(f.calls.length,1)
  }
})
test('Host owner API cannot self-approve and late disconnected preview issues no confirmation',async()=>{
  const invocation=hostFixture().invocation;let sends=0,finish
  const host=createCategoryRebuildHost(()=>({call:async(action)=>{
    if(action==='preview_category_rebuild')return structuredClone(plan)
    sends++;throw new Error('reply lost')
  }}))
  const p=await host.preview(invocation,scope)
  await assert.rejects(host.commit(invocation,{ticket:p.ticket}),/可信页面或单次批准/)
  await assert.rejects(host.commit(invocation,{ticket:p.ticket}),/失效/);assert.equal(sends,0)
  const f=hostFixture(),late=createCategoryRebuildHost(()=>({call:()=>new Promise(resolve=>{finish=resolve})}))
  const pending=late.preview(f.invocation,scope);f.peerController.abort();finish(structuredClone(plan))
  await assert.rejects(pending)
})
