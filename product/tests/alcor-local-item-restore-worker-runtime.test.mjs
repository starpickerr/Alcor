import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { build } from 'esbuild'
import { Context } from '@deepseek-ai/cordis'
import { bindScopeParent, createScope, scopeTarget } from '@deepseek-ai/dsh-scope'
import Sessions from '@deepseek-ai/dsh-session'
import Projections from '@deepseek-ai/dsh-session-projection'
import Agents from '@deepseek-ai/dsh-agent'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Tools from '@deepseek-ai/dsh-tools'
import Approval from '@deepseek-ai/dsh-user-approval'
import * as Native from '@deepseek-ai/dsh-tool-executor-native'
import { apply } from '../../packages/alcor-secretary/src/tools.js'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'
const exec=promisify(execFile)
// Both production workers and SQLite/Host/SDK are real. Only EventKit is a read-only synthetic dependency.
for(const kind of ['calendar','reminders'])for(const scenario of ['restore','owner-reply-lost','recreated-id','wrong-source','permission-revoked','same-operation','no-receipt','owner-page'])test(`${kind} ${scenario}: current tool restores only missing local data with real workers`,async t=>{
  const root=await mkdtemp(join(tmpdir(),'alcor-local-restore-wire-')),output=await mkdtemp(join(process.cwd(),'.restore-wire-build-'))
  const ctx=new Context(),ownerWorker=new BusinessWorker(join(root,'owner'),{worker:new URL('../../packages/alcor-secretary/business/worker.py',import.meta.url)})
  let appleWorker
  t.after(async()=>{
    try {
      await ctx.fiber.dispose();if(appleWorker)await appleWorker.close();await ownerWorker.close()
      for(const line of (await readFile(join(root,'apple','synthetic-native-calls.jsonl'),'utf8')).trim().split('\n'))assert(['status','reminder_status','calendars','reminder_lists','events','reminders','lookup','reminder_lookup'].includes(JSON.parse(line).op),line)
    } finally {await rm(root,{recursive:true,force:true});await rm(output,{recursive:true,force:true})}
  })
  const classes={}
  for(const name of ['organizer','apple-sync']){const outfile=join(output,name+'.mjs');await build({entryPoints:[`packages/alcor-${name}/src/index.ts`],outfile,bundle:true,packages:'external',format:'esm',platform:'node',target:'node24',logLevel:'silent'});classes[name]=(await import(pathToFileURL(outfile))).default}
  const owner=Object.create(classes.organizer.prototype),apple=Object.create(classes['apple-sync'].prototype)
  owner.active=true;owner.queue=Promise.resolve();owner.businessWorker=ownerWorker;owner.ctx={get:key=>key==='alcorAppleSync'?apple:undefined}
  apple.active=true;apple.ctx={alcorOrganizer:owner};apple.itemRestores=new Map()
  const peers=[];let lost=false,rawRow,recreated=false
  const domain=kind==='calendar'?'calendar':'tasks',idKey=kind==='calendar'?'id':'todo_id',getter=kind==='calendar'?'get_event':'get_todo'
  const table=kind==='calendar'?'calendar_events':'todos',dbPath=join(root,'owner',domain+'.sqlite3')
  appleWorker=new BusinessWorker(join(root,'apple'),{worker:new URL('./fixtures/apple-restore-native-worker.py',import.meta.url),onRequest:async input=>{
    peers.push(structuredClone(input))
    if(input.route==='item_restore')return apple.authorizeItemRestore(input)
    if(['item_restore_prepare','item_restore_owner'].includes(input.route)){
      if(scenario==='recreated-id'&&!recreated&&input.method==='apple_restore_item'){
        recreated=true
        await exec('python3',['-c',`import sqlite3,sys,json,pathlib
p=pathlib.Path(sys.argv[1]);assert p.parent.parent.name.startswith('alcor-local-restore-wire-')
row=json.loads(sys.argv[3]);row['title']='Concurrent local incarnation'
with sqlite3.connect(p) as db:db.execute('INSERT INTO '+sys.argv[2]+' ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))`,dbPath,table,JSON.stringify(rawRow)])
      }
      if(scenario==='no-receipt'&&input.method==='apple_restore_item')throw new Error('synthetic unknown owner transport; no receipt')
      const result=await apple.itemRestoreOwner(input)
      if(scenario==='owner-reply-lost'&&!lost&&input.method==='apple_restore_item'){lost=true;throw new Error('Synthetic response loss after durable owner commit')}
      return result
    }
    return owner.applePeer(input)
  }});apple.worker=appleWorker
  await appleWorker.call('initialize');await apple.invoke({method:'start_sync',arguments:{kind}})
  const rows=await ownerWorker.call('invoke',{domain,method:kind==='calendar'?'list_events':'list_todos',arguments:{}});assert(rows.length)
  const row=rows[0]
  // Fixture-only SQL removes one synthetic owner row; leaves old generation/binding evidence for the real CAS.
  const removed=await exec('python3',['-c',`import sqlite3,sys,pathlib,json
p=pathlib.Path(sys.argv[1]);assert p.parent.parent.name.startswith('alcor-local-restore-wire-')
with sqlite3.connect(p) as db:
 db.row_factory=sqlite3.Row
 raw=dict(db.execute('SELECT * FROM '+sys.argv[2]+' WHERE id=?',(sys.argv[3],)).fetchone())
 db.execute('DELETE FROM '+sys.argv[2]+' WHERE id=?',(sys.argv[3],))
 print(json.dumps(raw))`,dbPath,table,row.id])
  rawRow=JSON.parse(removed.stdout)
  await writeFile(join(root,'apple','synthetic-change-native'),'synthetic bridge only')
  await apple.invoke({method:'sync',arguments:{kind}})
  const status=await apple.invoke({method:'status',arguments:{kind}})
  assert(status.conflicts.some(item=>(item.alcor_id??item.todo_id)===row.id&&item.local_missing===true),JSON.stringify(status.conflicts))
  for(const plugin of [Sessions,Projections,Agents,SystemPrompt,Tools,Approval,Native])await ctx.plugin(plugin,{})
  ctx.provide('alcorSecretary',{});ctx.provide('alcorOrganizer',owner)
  const session=ctx.sessions.create('synthetic-restore-wire');session.append('turn/start',{turn:1})
  const agent={ctx,options:{subagentDepth:0},session},preset={};bindScopeParent(agent,preset)
  await ctx.plugin(Object.assign(inner=>apply(createScope(inner,preset).ctx),{inject:['tools','systemPrompt','alcorSecretary','alcorOrganizer']}))
  let asks=0;ctx.on('approval/request',()=>{asks++;return'rejected'})
  const turn=kind=>ctx.waterfall(scopeTarget(agent,agent),'agent/pre-step',{agent,messages:[{source:{kind}}]},async()=>{})
  let call=async id=>(await ctx.tools.execute({agent,signal:AbortSignal.timeout(10000),calls:[{type:'tool-call',id,name:'alcor_business',arguments:JSON.stringify({domain:'apple',method:'restore_local_item',arguments:{kind,identity:row.id}})}]})).results[0].result
  for(const choice of ['keep_apple','keep_alcor']) {
    assert.throws(()=>apple.invoke({method:'resolve_conflict',arguments:{kind,choice,[kind==='calendar'?'alcor_id':'todo_id']:row.id}}),/审批/)
    await assert.rejects(()=>appleWorker.call('invoke',{method:'resolve_conflict',arguments:{kind,choice,[kind==='calendar'?'alcor_id':'todo_id']:row.id}}),/审批/)
  }
  await assert.rejects(()=>ownerWorker.call('apple_peer',{domain,method:'apple_restore_item',args:['a'.repeat(64),{}],kwargs:{}}))
  const beforeScheduled=peers.length
  await turn('schedule');assert.equal((await call('scheduled')).isError,true);assert.equal(peers.length,beforeScheduled)
  session.append('turn/end',{turn:1,reason:'completed'});session.append('turn/start',{turn:2});await turn('user')
  if(scenario==='owner-page'){
    const {TypertRemoteService}=await import('@deepseek-ai/dsh-typert-protocol')
    const {default:Typert}=await import('@deepseek-ai/dsh-typert-registry')
    const {default:Gateway}=await import('@deepseek-ai/dsh-api-gateway')
    const {default:RemoteAccess}=await import('@deepseek-ai/dsh-api-remote-access')
    const {HostConnectionService}=await import('@deepseek-ai/dsh-client-connection')
    const {remote}=await import('../../packages/alcor-apple-sync/src/remote.js')
    class PageApple extends TypertRemoteService {constructor(ctx){super(ctx,'alcorAppleSync');this.active=true;this.worker=appleWorker;this.itemRestores=apple.itemRestores}}
    for(const name of ['restoreLocalItem','runItemRestore'])Object.defineProperty(PageApple.prototype,name,Object.getOwnPropertyDescriptor(classes['apple-sync'].prototype,name))
    await ctx.plugin(Typert);await ctx.plugin(RemoteAccess);await ctx.plugin(Gateway,{});await ctx.plugin(PageApple)
    ctx.typert.register({package:'@alcor/apple-sync',face:'host',schemas:[],invocations:remote.descriptors,model:{services:[],events:[],objects:[]}})
    await ctx.plugin(inner=>{new HostConnectionService(inner,[],{isAuthenticated:()=>true,authenticateApi(){}})})
    const peer=ctx.connection.admit({headers:{host:'localhost',origin:'http://localhost'}}).peer
    call=async()=>{
      const value=await ctx.typertGateway.invoke({namespace:'alcorAppleSync',method:'restoreLocalItem',args:{request:{kind,identity:row.id}},peer})
      return {isError:false,value}
    }
  }
  const actualCall=appleWorker.call.bind(appleWorker);let prepared,release
  const preparedReady=new Promise(resolve=>{prepared=resolve}),released=new Promise(resolve=>{release=resolve})
  appleWorker.call=async(action,input)=>{
    const value=await actualCall(action,input)
    if(action==='prepare_item_restore'){
      if(scenario==='wrong-source')await writeFile(join(root,'apple','synthetic-wrong-source'),'synthetic only')
      if(scenario==='permission-revoked')await writeFile(join(root,'apple','synthetic-deny-access'),'synthetic only')
      if(scenario==='same-operation'){prepared();await released}
    }
    return value
  }
  const waiting=call('restore')
  if(scenario==='same-operation'){
    await preparedReady
    assert.equal((await call('restore')).isError,true)
    release()
  }
  const first=await waiting;assert.equal(first.isError,false,JSON.stringify(first))
  const blocked=['wrong-source','permission-revoked','recreated-id','no-receipt'].includes(scenario)
  if(blocked){
    assert.match(JSON.stringify(first),/needs_review/)
    if(scenario==='recreated-id')assert.equal((await ownerWorker.call('invoke',{domain,method:getter,arguments:{[idKey]:row.id}})).title,'Concurrent local incarnation')
    else await assert.rejects(()=>ownerWorker.call('invoke',{domain,method:getter,arguments:{[idKey]:row.id}}),/not found/)
    if(scenario==='no-receipt'){
      const beforeRetry=peers.length;const retry=await call('retry');assert.equal(retry.isError,false);assert.match(JSON.stringify(retry),/needs_review/)
      assert(peers.slice(beforeRetry).some(x=>x.method==='apple_verify_item_restore'))
      assert(!peers.slice(beforeRetry).some(x=>x.method==='apple_restore_item'||x.method==='apple_capture_item_restore'||x.route==='item_restore'))
    }
    assert.equal(peers.filter(x=>x.method==='apple_restore_item').length,['recreated-id','no-receipt'].includes(scenario)?1:0)
    assert.equal(asks,0);assert.equal(apple.itemRestores.size,0)
    return
  }
  if(scenario==='owner-reply-lost'){
    const pending=await apple.invoke({method:'status',arguments:{kind}})
    const summary=pending.conflicts.find(item=>(item.alcor_id??item.todo_id)===row.id)
    assert.equal(summary.local_missing,false);assert.equal(summary.local_restore_pending,true)
    const start=peers.length;const retry=await call('retry');assert.equal(retry.isError,false,JSON.stringify(retry));assert.match(JSON.stringify(retry),/success/)
    const repeat=peers.slice(start).filter(x=>x.route?.startsWith('item_restore'))
    assert(repeat.some(x=>x.method==='apple_verify_item_restore'));assert(!repeat.some(x=>x.method==='apple_restore_item'||x.method==='apple_capture_item_restore'||x.route==='item_restore'))
  }else assert.match(JSON.stringify(first),/success/)
  const restored=await ownerWorker.call('invoke',{domain,method:getter,arguments:{[idKey]:row.id}});assert.equal(restored.id,row.id);assert.equal(restored.title,row.title+' changed in Apple')
  assert.equal(asks,0);assert.equal(peers.filter(x=>x.method==='apple_restore_item').length,1);assert.equal(apple.itemRestores.size,0)
  const deliveries=async()=>JSON.parse((await exec('python3',['-c',`import sqlite3,sys,json,pathlib
p=pathlib.Path(sys.argv[1]);assert p.parent.parent.name.startswith('alcor-local-restore-wire-')
with sqlite3.connect(p) as db:
 tables=('reminder_deliveries','reminder_route_deliveries','reminder_occurrences')
 print(json.dumps({t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in tables}))`,join(root,'owner','tasks.sqlite3')])).stdout)
  const beforeDeliveries=await deliveries(),before=peers.length
  assert.deepEqual(beforeDeliveries,{reminder_deliveries:0,reminder_route_deliveries:0,reminder_occurrences:0})
  await apple.invoke({method:'sync',arguments:{kind}});await apple.invoke({method:'sync',arguments:{kind}})
  assert.deepEqual(await deliveries(),beforeDeliveries)
  if(kind==='reminders'){assert.equal(restored.native_managed,true);assert.equal(restored.source_type,'apple_reminder')}
  assert(!peers.slice(before).some(x=>x.route?.startsWith('item_restore')))
  assert.equal((await apple.invoke({method:'status',arguments:{kind}})).conflicts.length,0)
  for(const line of (await readFile(join(root,'apple','synthetic-native-calls.jsonl'),'utf8')).trim().split('\n'))assert(['status','reminder_status','calendars','reminder_lists','events','reminders','lookup','reminder_lookup'].includes(JSON.parse(line).op))
  t.diagnostic(JSON.stringify({runtime:JSON.parse(await readFile(join(root,'apple','synthetic-runtime.json'),'utf8')),owner_spawn:ownerWorker.process.spawnargs,apple_spawn:appleWorker.process.spawnargs,restore_calls:peers.filter(x=>x.route?.startsWith('item_restore')).map(x=>x.method??x.route)}))
})
