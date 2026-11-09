import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { projectSpace, projectedStore, spaceHook } from '../../packages/alcor-secretary/src/spaces.js'
import { ProductSpaces } from '../../packages/alcor-secretary/src/space-host.js'
import { createSpaceNavigation } from '../../packages/alcor-secretary/src/space-navigation.js'

const store = value => ({ getSnapshot: () => value, subscribe: () => () => {} })
test('navigation keeps mixed legacy projects and QQ separate without moving sessions', () => {
  const rows = [{ id: 'a', projectionValues: { agentPreset: 'alcor' } }, { id: 'b', projectionValues: { agentPreset: 'default' } }, { id: 'old' }, { id: 'onebot-group-123' }]
  const catalog = { phase:'ready', ids: rows.map(row=>row.id), byId:Object.fromEntries(rows.map(row=>[row.id,row])) }
  const groups = [{workspaceId:'mixed',sessionIds:['a','b']},{workspaceId:'ad',sessionIds:['old']},{workspaceId:'q',sessionIds:['onebot-group-123']}]
  const meta = { defaults: { alcor:'ad',dsh:'dd' }, projects:{}, sessions:{old:'alcor'} }
  const a=projectSpace(catalog,groups,'alcor',meta),d=projectSpace(catalog,groups,'dsh',meta)
  assert.deepEqual(a.catalog.ids,['a','old']);assert.deepEqual(d.catalog.ids,['b'])
  assert.deepEqual(a.items,[{workspaceId:'mixed',sessionIds:['a']}]);assert.deepEqual(d.items,[{workspaceId:'mixed',sessionIds:['b']}])
  assert.equal(groups[0].sessionIds.length,2)
  assert.deepEqual(projectSpace(catalog,groups,'alcor').catalog.ids,[])
})
test('QQ channel folders stay out of projects even with a local chat or a stale project label', () => {
  const rows=[{id:'onebot-group-123'},{id:'local',agentPreset:'alcor'},{id:'work',agentPreset:'standard'}]
  const catalog={ids:rows.map(row=>row.id),byId:Object.fromEntries(rows.map(row=>[row.id,row]))}
  const mixed={workspaceId:'channel',title:'123',sessionIds:catalog.ids}
  const numeric={workspaceId:'ordinary',title:'456',sessionIds:[]}
  const info={defaults:{},projects:{channel:'alcor',ordinary:'alcor'},sessions:{}}
  assert.deepEqual(projectSpace(catalog,[mixed,numeric],'alcor',info).items,[numeric])
  assert.deepEqual(projectSpace(catalog,[mixed,numeric],'alcor',info).catalog.ids,['local'])
  assert.deepEqual(projectSpace(catalog,[mixed,numeric],'dsh',info).catalog.ids,['work'])
  assert.deepEqual(projectSpace(catalog,[mixed,numeric],'dsh',info).items,[])
})
test('space metadata survives recreation, protects defaults and serializes concurrent assignments',async t=>{
  const root=await mkdtemp(join(tmpdir(),'alcor-spaces-'));t.after(()=>rm(root,{recursive:true,force:true}))
  const rows=[],data=new Map(),disposed=[]
  const ctx={get:()=>({dir:root}), workspaceRegistry:{list:()=>rows,create:async(path,title)=>{let row=rows.find(row=>row.path===path);if(!row){row={id:'w'+rows.length,path,title,sessionIds:[]};rows.push(row)}return row}},
    sessionQuery:{observeSession:async id=>({header:{id},[Symbol.dispose]:()=>disposed.push(id)})}}
  const table={get:id=>structuredClone(data.get(id)),put:async(id,value)=>data.set(id,structuredClone(value))}
  const spaces=new ProductSpaces(ctx,table),first=await spaces.info()
  assert.notEqual(first.defaults.alcor,first.defaults.dsh)
  await assert.rejects(spaces.claim({workspaceId:first.defaults.alcor,space:'dsh'}),/默认/)
  await Promise.all([spaces.claim({sessionId:'chat-a',space:'alcor'}),spaces.claim({sessionId:'chat-b',space:'dsh'})])
  assert.equal(disposed.length,2)
  const restored=await new ProductSpaces(ctx,table).info();assert.deepEqual(restored.defaults,first.defaults)
  assert.deepEqual(restored.sessions,{'chat-a':'alcor','chat-b':'dsh'})
  await assert.rejects(spaces.claim({sessionId:'chat-a',space:'dsh'}),/另一个空间/)
})
test('global new chats use distinct defaults, project creation stays explicit, and QQ only selects history',async t=>{
  let mode='alcor',serial=0
  const requests=[],opened=[],panels=[],meta={defaults:{alcor:'ad',dsh:'dd'},projects:{project:'alcor'},sessions:{}}
  const ctx={sessions:{list:store({ids:[],byId:{}}),create:async args=>{requests.push(args);return 's'+ ++serial}},
    workspaces:{list:store({items:[{workspaceId:'ad',sessionIds:[]},{workspaceId:'dd',sessionIds:[]},{workspaceId:'project',sessionIds:[]},{workspaceId:'qq',sessionIds:['onebot-private-123']} ]})},
    remote:{alcorSecretary:{spaces:async()=>({ok:true,value:meta}),claimSpace:async request=>{meta.sessions[request.sessionId]=request.space;return{ok:true,value:meta}}},agentPresets:{list:async()=>({ok:true,value:{presets:[{id:'alcor',isDefault:true},{id:'default'}]}})}},
    uiWorkspace:{openSession:id=>opened.push(id)},layout:{selectPanel:panel=>panels.push(panel)}}
  const controller={getSnapshot:()=>mode,subscribe:()=>()=>{},switchMode:value=>{mode=value}}
  const nav=createSpaceNavigation(ctx,controller);t.after(()=>nav.dispose());await nav.ensure()
  await nav.startSession();await nav.startSession();await nav.startSession('project')
  mode='dsh';await nav.startSession()
  assert.deepEqual(requests.map(row=>row.workspaceId),['ad','ad','project','dd'])
  assert.deepEqual(requests.map(row=>row.agentPreset),['alcor','alcor','alcor','default'])
  assert.equal(new Set(opened).size,4)
  await assert.rejects(nav.startSession('qq'),/不可创建/)
  const count=requests.length
  assert.equal(nav.openQQ('onebot-group-123'),true)
  assert.equal(mode,'alcor');assert.equal(panels.at(-1),'alcor-qq-history');assert.equal(requests.length,count)
  assert.equal(nav.state.getSnapshot().selection,'onebot-group-123')
  nav.dispose();assert.equal(globalThis[spaceHook],undefined)
})
test('projected snapshots are stable until a source changes',()=>{
  const one=store({count:1});let calls=0
  const projected=projectedStore([one],value=>{calls++;return value})
  assert.equal(projected.getSnapshot(),projected.getSnapshot());assert.equal(calls,1)
})

test('search paginates before filtering so twenty workbench hits cannot hide an Alcor result',async()=>{
  const hit=id=>({header:{id},bestMatch:{sessionId:id,surface:'current',type:'user/message',snippet:'search result'}})
  let calls=0
  const spaces=new ProductSpaces({sessionQuery:{readSurface:async()=>({events:[{type:'assistant/message',data:{message:{content:[{type:'text',text:'query result'}]}}}]}),observeSession:async id=>({header:{id},projections:{values:{agentPreset:id==='wanted'?'alcor':'standard'}},[Symbol.dispose](){} }),searchSessions:async request=>{calls++;return request.cursor?{items:[hit('wanted')]}:{items:Array.from({length:20},(_,i)=>hit('other'+i)),nextCursor:'next'}}}}, {get:()=>({sessions:{}})})
  assert.deepEqual(await spaces.search('query',['wanted','other0'],'alcor'),{items:[{sessionId:'wanted',snippet:'query result'}],hasMore:false})
  assert.equal(calls,2)
})

test('a shared directory keeps independent project names, sessions and removal across a restart',async t=>{
  const path=await mkdtemp(join(tmpdir(),'alcor-shared-'));t.after(()=>rm(path,{recursive:true,force:true}))
  const workspace={id:'real',path,title:'Native',sessionIds:['a','d']},db=new Map()
  const ctx={get:()=>({dir:path}),workspaceRegistry:{list:()=>[workspace],create:async(_path,title)=>({id:title})},
    sessionQuery:{observeSession:async id=>({header:{id},projections:{values:{agentPreset:id==='a'?'alcor':'standard'}},[Symbol.dispose](){} })}}
  const table={get:key=>structuredClone(db.get(key)),put:async(key,v)=>db.set(key,structuredClone(v))}
  const api=new ProductSpaces(ctx,table)
  const group=[{workspaceId:'real',title:'Native',sessionIds:['a','d']}]
  const catalog={ids:['a','d'],byId:{a:{agentPreset:'alcor'},d:{agentPreset:'standard'}}}
  await api.editProject({space:'alcor',workspaceId:'real',action:'rename',title:'私人实验'})
  await api.editProject({space:'dsh',workspaceId:'real',action:'rename',title:'专业工程'})
  const restored=await new ProductSpaces(ctx,table).info()
  assert.deepEqual(projectSpace(catalog,group,'alcor',restored).items,[{workspaceId:'real',title:'私人实验',sessionIds:['a']}])
  assert.deepEqual(projectSpace(catalog,group,'dsh',restored).items,[{workspaceId:'real',title:'专业工程',sessionIds:['d']}])
  await assert.rejects(api.claim({sessionId:'d',space:'alcor'}),/另一个空间/)
  await api.editProject({space:'alcor',workspaceId:'real',action:'remove'})
  const removed=await api.info()
  assert.equal(projectSpace(catalog,group,'alcor',removed).items.length,0)
  assert.deepEqual(projectSpace(catalog,group,'dsh',removed).items,projectSpace(catalog,group,'dsh',restored).items)
  assert.deepEqual(workspace.sessionIds,['a','d'])
})
