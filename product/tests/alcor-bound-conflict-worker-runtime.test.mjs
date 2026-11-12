import { python as testPython } from './platform-python.mjs'
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, rm, readFile, writeFile } from 'node:fs/promises'
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
// Real SDK, Host classes, two worker processes and SQLite. EventKit and prompt answerer alone are synthetic.
for(const kind of ['calendar','reminders'])for(const choice of ['keep_apple','keep_alcor'])for(const scenario of ['success','owner-reply-lost','changed-after-preview','wrong-source','permission-revoked','no-owner-receipt','owner-page',...(kind==='calendar'?['same-series-other-occurrence']:[])])test(`${kind} ${choice} ${scenario}: exact-bound conflict through real workers`,async t=>{
  const root=await mkdtemp(join(tmpdir(),'alcor-bound-conflict-wire-')),output=await mkdtemp(join(process.cwd(),'.bound-wire-build-')),ctx=new Context()
  const ownerWorker=new BusinessWorker(join(root,'owner'),{worker:new URL('../../packages/alcor-secretary/business/worker.py',import.meta.url)})
  let appleWorker
  t.after(async()=>{await ctx.fiber.dispose();if(appleWorker)await appleWorker.close();await ownerWorker.close();await rm(root,{recursive:true,force:true});await rm(output,{recursive:true,force:true})})
  const classes={}
  for(const name of ['organizer','apple-sync']){const outfile=join(output,name+'.mjs');await build({entryPoints:[`packages/alcor-${name}/src/index.ts`],outfile,bundle:true,packages:'external',format:'esm',platform:'node',target:'node24',logLevel:'silent'});classes[name]=(await import(pathToFileURL(outfile))).default}
  const owner=Object.create(classes.organizer.prototype),apple=Object.create(classes['apple-sync'].prototype)
  owner.active=true;owner.queue=Promise.resolve();owner.exportAuthorizers=new WeakMap();owner.businessWorker=ownerWorker;owner.ctx={get:key=>key==='alcorAppleSync'?apple:undefined}
  apple.active=true;apple.ctx={alcorOrganizer:owner};apple.preparedExports=new Map();apple.runningExports=new Map()
  const peers=[];let lost=false
  appleWorker=new BusinessWorker(join(root,'apple'),{worker:new URL('./fixtures/apple-bound-conflict-worker.py',import.meta.url),onRequest:async input=>{
    peers.push(structuredClone(input))
    if(input.route==='approved_export')return apple.authorizeExportDispatch(input)
    if(input.route==='bound_conflict_owner'){
      if(scenario==='no-owner-receipt'&&input.method==='apple_commit_bound_conflict')throw new Error('Synthetic unknown owner transport without receipt')
      const result=await apple.boundConflictOwner(input)
      if(scenario==='owner-reply-lost'&&!lost&&input.method==='apple_commit_bound_conflict'){lost=true;throw new Error('Synthetic lost response after durable owner commit')}
      return result
    }
    return owner.applePeer(input)
  }});apple.worker=appleWorker
  if(scenario==='same-series-other-occurrence'){await mkdir(join(root,'apple'),{recursive:true});await writeFile(join(root,'apple','same-event-occurrences'),'fake EventKit only')}
  await appleWorker.call('initialize');await apple.invoke({method:'start_sync',arguments:{kind}})
  const domain=kind==='calendar'?'calendar':'tasks',getter=kind==='calendar'?'get_event':'get_todo',idKey=kind==='calendar'?'id':'todo_id'
  const rows=await ownerWorker.call('invoke',{domain,method:kind==='calendar'?'list_events':'list_todos',arguments:{}})
  const row=rows.find(r=>r.title!=='Unrelated item'),healthy=rows.find(r=>r.title==='Unrelated item');assert(row);assert(healthy)
  const edit=title=>ownerWorker.call('invoke',{domain,method:kind==='calendar'?'update_event':'update_todo',arguments:{[idKey]:row.id,[kind==='calendar'?'patch':'payload']:{title}}})
  await edit('Chosen Alcor content')
  assert.equal((await ownerWorker.call('invoke',{domain,method:getter,arguments:{[idKey]:row.id}})).title,'Chosen Alcor content')
  await writeFile(join(root,'apple','native-change'),'fake EventKit only')
  await apple.invoke({method:'sync',arguments:{kind}})
  assert((await apple.invoke({method:'status',arguments:{kind}})).conflicts.some(r=>(r.alcor_id??r.todo_id)===row.id))
  for(const plugin of [Sessions,Projections,Agents,SystemPrompt,Tools,Approval,Native])await ctx.plugin(plugin,{})
  ctx.provide('alcorSecretary',{});ctx.provide('alcorOrganizer',owner)
  const session=ctx.sessions.create('synthetic-bound-conflict-wire');session.append('turn/start',{turn:1})
  const agent={ctx,options:{subagentDepth:0},session},preset={};bindScopeParent(agent,preset)
  await ctx.plugin(Object.assign(inner=>apply(createScope(inner,preset).ctx),{inject:['tools','systemPrompt','alcorSecretary','alcorOrganizer']}))
  let asks=0,answer='allowed-once';ctx.on('approval/request',request=>{asks++;for(const value of [row.id,'account1','Chosen Alcor content',row.title+' changed in Apple'])assert.ok(request.reason.includes(value),value);return answer})
  const turn=kind=>ctx.waterfall(scopeTarget(agent,agent),'agent/pre-step',{agent,messages:[{source:{kind}}]},async()=>{})
  let call=async id=>(await ctx.tools.execute({agent,signal:AbortSignal.timeout(10000),calls:[{type:'tool-call',id,name:'alcor_business',arguments:JSON.stringify({domain:'apple',method:'resolve_conflict',arguments:{kind,[kind==='calendar'?'alcor_id':'todo_id']:row.id,choice}})}]})).results[0].result
  const beforeScheduled=peers.length;await turn('schedule');assert.equal((await call('scheduled')).isError,true);assert.equal(peers.length,beforeScheduled)
  session.append('turn/end',{turn:1,reason:'completed'});session.append('turn/start',{turn:2});await turn('user')
  for(const pick of ['keep_apple','keep_alcor']){
    assert.throws(()=>apple.invoke({method:'resolve_conflict',arguments:{kind,choice:pick,[kind==='calendar'?'alcor_id':'todo_id']:row.id}}),/审批/)
    await assert.rejects(()=>appleWorker.call('invoke',{method:'resolve_conflict',arguments:{kind,choice:pick,[kind==='calendar'?'alcor_id':'todo_id']:row.id}}),/审批/)
  }
  await assert.rejects(()=>ownerWorker.call('apple_peer',{domain,method:'apple_commit_bound_conflict',args:[],kwargs:{}}))
  if(choice==='keep_alcor'&&scenario!=='owner-page'){answer='rejected';assert.equal((await call('denied')).isError,true);answer='allowed-once'}
  if(scenario==='owner-page'){
    const {TypertRemoteService}=await import('@deepseek-ai/dsh-typert-protocol')
    const {default:Typert}=await import('@deepseek-ai/dsh-typert-registry')
    const {default:Gateway}=await import('@deepseek-ai/dsh-api-gateway')
    const {default:RemoteAccess}=await import('@deepseek-ai/dsh-api-remote-access')
    const {HostConnectionService}=await import('@deepseek-ai/dsh-client-connection')
    const {remote}=await import('../../packages/alcor-apple-sync/src/remote.js')
    let confirmed=false,confirmationCalls=0
    class PageApple extends TypertRemoteService {constructor(ctx){super(ctx,'alcorAppleSync');this.active=true;this.worker=appleWorker;this.preparedExports=apple.preparedExports;this.runningExports=apple.runningExports;this.confirmConflict=async request=>{confirmationCalls++;assert.equal(request.kind,'apple-update');for(const value of [row.id,'account1','Chosen Alcor content'])assert.ok(request.detail.includes(value));return confirmed}}}
    for(const name of ['resolveItemConflict','prepareExport','commitExport'])Object.defineProperty(PageApple.prototype,name,Object.getOwnPropertyDescriptor(classes['apple-sync'].prototype,name))
    await ctx.plugin(Typert);await ctx.plugin(RemoteAccess);await ctx.plugin(Gateway,{});await ctx.plugin(PageApple)
    ctx.typert.register({package:'@alcor/apple-sync',face:'host',schemas:[],invocations:remote.descriptors,model:{services:[],events:[],objects:[]}})
    await ctx.plugin(inner=>{new HostConnectionService(inner,[],{isAuthenticated:()=>true,authenticateApi(){}})})
    const peer=ctx.connection.admit({headers:{host:'localhost',origin:'http://localhost'}}).peer
    const invoke=()=>ctx.typertGateway.invoke({namespace:'alcorAppleSync',method:'resolveItemConflict',args:{request:{kind,identity:row.id,choice}},peer})
    if(choice==='keep_alcor'){
      await assert.rejects(invoke(),/取消/)
      assert.equal(peers.filter(p=>p.route==='bound_conflict_owner').length,0)
      confirmed=true
    }
    call=async()=>{const value=await invoke();assert.equal(confirmationCalls,choice==='keep_alcor'?2:0);return {isError:false,value}}
  }
  const original=appleWorker.call.bind(appleWorker)
  appleWorker.call=async(action,input)=>{
    const value=await original(action,input)
    if(action==='prepare_approved_export'){
      if(scenario==='changed-after-preview')await edit('Concurrent local edit')
      if(scenario==='wrong-source')await writeFile(join(root,'apple','wrong-source'),'fake only')
      if(scenario==='permission-revoked')await writeFile(join(root,'apple','deny-access'),'fake only')
    }
    return value
  }
  const result=await call('resolve');assert.equal(result.isError,false,JSON.stringify(result))
  const countWrites=async()=>{try{return (await readFile(join(root,'apple','native-writes.jsonl'),'utf8')).trim().split('\n').filter(Boolean).length}catch(error){if(error.code==='ENOENT')return 0;throw error}}
  const blocked=['changed-after-preview','wrong-source','permission-revoked','no-owner-receipt'].includes(scenario)
  if(scenario==='owner-reply-lost'||scenario==='no-owner-receipt'){
    const pending=(await apple.invoke({method:'status',arguments:{kind}})).conflicts.find(r=>(r.alcor_id??r.todo_id)===row.id)
    assert.equal(pending.bound_conflict_pending.choice,choice)
    assert.equal(pending.bound_conflict_pending.phase,choice==='keep_alcor'?'native_saved':'owner_uncertain')
  }
  if(blocked){
    assert.match(JSON.stringify(result),/needs_review/)
    assert.equal(await countWrites(),choice==='keep_alcor'&&scenario==='no-owner-receipt'?1:0)
    assert.equal((await ownerWorker.call('invoke',{domain,method:getter,arguments:{[idKey]:row.id}})).title,scenario==='changed-after-preview'?'Concurrent local edit':'Chosen Alcor content')
    if(scenario==='no-owner-receipt'){
      const before=peers.length;const again=await call('verify-unknown');assert.match(JSON.stringify(again),/needs_review/)
      assert(peers.slice(before).some(p=>p.method==='apple_verify_bound_conflict'))
      assert(!peers.slice(before).some(p=>p.method==='apple_commit_bound_conflict'||p.route==='approved_export'))
      const writes=await countWrites();await writeFile(join(root,'apple','healthy-change'),'fake only')
      await apple.invoke({method:'sync',arguments:{kind}})
      assert.equal((await ownerWorker.call('invoke',{domain,method:getter,arguments:{[idKey]:healthy.id}})).title,'Unrelated item changed in Apple')
      assert.equal(await countWrites(),writes)
    }
    return
  }
  if(scenario==='owner-reply-lost'){
    assert.match(JSON.stringify(result),/needs_review/)
    const before=peers.length;const again=await call('verify-receipt');assert.match(JSON.stringify(again),/success/)
    assert(peers.slice(before).some(p=>p.method==='apple_verify_bound_conflict'))
    assert(!peers.slice(before).some(p=>p.method==='apple_commit_bound_conflict'||p.route==='approved_export'))
  }else assert.match(JSON.stringify(result),/success/)
  const current=await ownerWorker.call('invoke',{domain,method:getter,arguments:{[idKey]:row.id}})
  assert.equal(current.title,choice==='keep_apple'?row.title+' changed in Apple':'Chosen Alcor content')
  const writes=await countWrites();assert.equal(writes,choice==='keep_alcor'?1:0)
  const deliveries=async()=>JSON.parse((await exec(testPython,['-c',`import sqlite3,sys,json,pathlib\np=pathlib.Path(sys.argv[1]);assert p.parent.parent.name.startswith('alcor-bound-conflict-wire-')\nwith sqlite3.connect(p) as db:\n print(json.dumps({t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('reminder_deliveries','reminder_route_deliveries','reminder_occurrences')}))`,join(root,'owner','tasks.sqlite3')])).stdout)
  const beforeDeliveries=await deliveries();assert.deepEqual(beforeDeliveries,{reminder_deliveries:0,reminder_route_deliveries:0,reminder_occurrences:0})
  await apple.invoke({method:'sync',arguments:{kind}});await apple.invoke({method:'sync',arguments:{kind}})
  assert.equal(await countWrites(),writes);assert.deepEqual(await deliveries(),beforeDeliveries)
  assert.equal((await apple.invoke({method:'status',arguments:{kind}})).conflicts.length,0)
  if(scenario==='same-series-other-occurrence'){
    const all=await ownerWorker.call('invoke',{domain,method:'list_events',arguments:{}})
    assert.equal(all.filter(r=>r.title==='Different occurrence changed in Apple').length,1)
    assert.equal(all.length,3)
  }
  assert.equal(asks,choice==='keep_alcor'&&scenario!=='owner-page'?2:0)
  t.diagnostic(JSON.stringify({runtime:JSON.parse(await readFile(join(root,'apple','synthetic-runtime.json'),'utf8')),owner_spawn:ownerWorker.process.spawnargs,apple_spawn:appleWorker.process.spawnargs,native_writes:writes}))
})
