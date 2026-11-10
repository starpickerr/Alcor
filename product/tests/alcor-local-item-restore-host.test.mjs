import test, { before, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
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
import * as Ptc from '@deepseek-ai/dsh-tool-executor-ptc'
import { apply } from '../../packages/alcor-secretary/src/tools.js'
import { remote } from '../../packages/alcor-apple-sync/src/remote.js'
import { canRestoreLocalItem, resolutionRequest } from '../../packages/alcor-apple-sync/src/presentation.js'
let Apple, Organizer, output
before(async () => {
  output = await mkdtemp(join(process.cwd(), '.restore-build-'))
  for (const name of ['apple-sync', 'organizer']) {
    const outfile = join(output, name + '.mjs')
    await build({ entryPoints: [`packages/alcor-${name}/src/index.ts`], outfile, bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24', logLevel: 'silent' })
    const klass = (await import(pathToFileURL(outfile))).default
    if (name === 'organizer') Organizer = klass; else Apple = klass
  }
})
after(async () => { if (output) await rm(output, { recursive: true, force: true }) })
function host() {
  const apple = Object.create(Apple.prototype), owner = Object.create(Organizer.prototype)
  apple.active = true; apple.itemRestores = new Map(); apple.ctx = { alcorOrganizer: owner }
  owner.active = true; owner.queue = Promise.resolve(); owner.ctx = { get: key => key === 'alcorAppleSync' ? apple : undefined }
  const state = { writes: 0, calls: [], prepares: 0, nativeWrites: 0, beforeRun: async () => {}, beforeOwner: async () => {} }
  owner.businessWorker = { call: async (action, request) => {
    assert.equal(action, 'item_restore_peer'); state.calls.push(request.method)
    if (request.method === 'apple_capture_item_restore') return { identity: request.args[0], missing: true, generation: 'frozen-generation', native: request.args[3] }
    if (request.method === 'apple_verify_item_restore') return null
    assert.equal(request.method, 'apple_restore_item'); state.writes++; return { local: { id: request.args[1].identity }, generation: 'new-generation', receipt: 'synthetic-receipt' }
  } }
  apple.worker = { call: async (action, request) => {
    if (action === 'prepare_item_restore') {
      state.prepares++
      const capture = [request.identity, { title: 'synthetic' }, 'selected-category', { id: 'native-item', source_id: 'selected-source' }, null]
      const peer = { route: 'item_restore_prepare', kind: request.kind, operation_id: request.operation_id, local_id: request.identity, method: 'apple_capture_item_restore', args: capture, kwargs: {} }
      const expected = await apple.itemRestoreOwner(peer)
      state.capture = capture; state.plan = { version: 1, kind: request.kind, operation_id: request.operation_id, local_id: request.identity, owner_expected: expected,
        mapping: { state: 'conflict' }, native: capture[3], links: [], groups: [], inventory: [], baseline: [], plan_fingerprint: 'f'.repeat(64) }
      await state.afterPrepare?.(state.plan)
      return structuredClone(state.plan)
    }
    assert.equal(action, 'run_item_restore'); await state.beforeRun(request)
    const { plan, kind } = request
    const auth = { route: 'item_restore', kind, operation_id: plan.operation_id, local_id: plan.local_id, plan_fingerprint: plan.plan_fingerprint }
    assert.equal(apple.authorizeItemRestore({ ...auth, local_id: 'other' }), false)
    assert.equal(apple.authorizeItemRestore({ ...auth, kind: kind === 'calendar' ? 'reminders' : 'calendar' }), false)
    assert.equal(apple.authorizeItemRestore({ ...auth, source: 'user' }), false)
    if (!apple.authorizeItemRestore(auth)) throw new Error('current restore not authorized')
    const peer = { ...auth, route: 'item_restore_owner', method: 'apple_restore_item', args: [plan.operation_id, plan.owner_expected], kwargs: {} }
    for (const patch of [{ method: 'delete_event' }, { args: [plan.operation_id, { ...plan.owner_expected, identity: 'other' }] }, { local_id: 'other' }, { plan_fingerprint: '0'.repeat(64) }, { kwargs: { confirmed: true } }]) assert.throws(() => apple.itemRestoreOwner({ ...peer, ...patch }), /不属于/)
    await state.beforeOwner()
    const result = await apple.itemRestoreOwner(peer)
    state.lastAuth = auth; state.lastPeer = peer
    return { status: 'success', restored: result.local.id }
  } }
  return { apple, owner, state }
}
async function sdk(t, mode='native') {
  const { apple, owner, state } = host(), ctx = new Context(); t.after(() => ctx.fiber.dispose())
  for (const plugin of [Sessions, Projections, Agents, SystemPrompt, Tools, Approval]) await ctx.plugin(plugin, {})
  let ptcInput
  if (mode === 'ptc') {
    ctx.provide('ptcRuntime', { language:'typescript', programFormat:'function-body', isolation:'synthetic', executionInstructions:'', resolve:value=>value,
      run:async request=>{ try { return { logs:[], value:await request.bindings[0].functions.alcor_business(ptcInput) } } catch(error) { return { logs:[], error:{kind:'exception',message:error.message} } } }, release:async()=>{} })
    ctx.provide('workingDirectory',{ensure:async()=>process.cwd()}); await ctx.plugin(Ptc)
  } else await ctx.plugin(Native)
  ctx.provide('alcorSecretary',{}); ctx.provide('alcorOrganizer',owner)
  const session=ctx.sessions.create('synthetic-local-restore');session.append('turn/start',{turn:1})
  const agent={ctx,options:{subagentDepth:0},session},preset={};bindScopeParent(agent,preset)
  await ctx.plugin(Object.assign(inner=>apply(createScope(inner,preset).ctx),{inject:['tools','systemPrompt','alcorSecretary','alcorOrganizer']}))
  let asks=0;ctx.on('approval/request',()=>{asks++;return 'rejected'})
  const turn=kind=>ctx.waterfall(scopeTarget(agent,agent),'agent/pre-step',{agent,messages:[{source:{kind}}]},async()=>{})
  const call=async(id,args={domain:'apple',method:'restore_local_item',arguments:{kind:'calendar',identity:'local-id'}},signal=AbortSignal.timeout(5000))=>{
    ptcInput=args
    const block=mode==='ptc'?{type:'tool-call',id,name:'run_code',arguments:JSON.stringify({code:'synthetic',description:'Restore missing local item'})}:{type:'tool-call',id,name:'alcor_business',arguments:JSON.stringify(args)}
    return (await ctx.tools.execute({agent,signal,calls:[block]})).results[0].result
  }
  return {apple,owner,state,ctx,agent,session,turn,call,asks:()=>asks}
}
for(const mode of ['native','ptc']) test(`${mode}: current user only, no deletion approval, scoped restore then no reusable callback`,async t=>{
  const f=await sdk(t,mode);await f.turn('schedule');assert.equal((await f.call('scheduled')).isError,true);assert.equal(f.state.prepares,0)
  f.session.append('turn/end',{turn:1,reason:'completed'});f.session.append('turn/start',{turn:2});await f.turn('user')
  const result=await f.call('restore');assert.equal(result.isError,false,JSON.stringify(result));assert.equal(f.state.writes,1);assert.equal(f.asks(),0)
  assert.equal(f.apple.itemRestores.size,0);assert.equal(f.apple.authorizeItemRestore(f.state.lastAuth),false);assert.throws(()=>f.apple.itemRestoreOwner(f.state.lastPeer),/不属于/)
  for(const choice of ['keep_alcor','keep_apple']) assert.equal((await f.call(choice,{domain:'apple',method:'resolve_conflict',arguments:{kind:'calendar',alcor_id:'local-id',choice}})).isError,true)
  assert.equal(f.state.writes,1)
})
for(const changed of ['turn','abort','plugin','owner','args']) test(`revocation during prepare: ${changed} produces no owner write`,async t=>{
  const f=await sdk(t),abort=new AbortController();await f.turn('user')
  f.state.afterPrepare=async()=>{
    if(changed==='turn'){f.session.append('turn/end',{turn:1,reason:'completed'});f.session.append('turn/start',{turn:2})}
    if(changed==='abort')abort.abort()
    if(changed==='plugin')f.apple.active=false
    if(changed==='owner')f.owner.active=false
    if(changed==='args')f.state.plan.local_id='other'
  }
  const result=await f.call('changed',undefined,abort.signal);assert.equal(result.isError,true,JSON.stringify(result));assert.equal(f.state.writes,0);assert.equal(f.apple.itemRestores.size,0)
})
test('owner page is a low-impact explicit restore; abort and non-owner are blocked',async()=>{
  const {apple,state}=host(),peer={principal:{kind:'owner'},signal:new AbortController().signal},controller=new AbortController()
  apple.ctx.invocation={peer,signal:controller.signal}
  assert.equal((await apple.restoreLocalItem({kind:'reminders',identity:'local-id'})).status,'success');assert.equal(state.writes,1)
  controller.abort();await assert.rejects(()=>apple.restoreLocalItem({kind:'reminders',identity:'local-id'}),/失效/)
  apple.ctx.invocation={peer:{...peer,principal:{kind:'delegate'}},signal:new AbortController().signal}
  assert.throws(()=>apple.restoreLocalItem({kind:'reminders',identity:'local-id'}),/所有者/)
  assert.equal(state.writes,1)
})
test('prepare scope cannot write; run cannot change capture/native or bypass raw invoke',async()=>{
  const {apple,owner,state}=host();let live=true
  state.afterPrepare=async plan=>{
    const request={route:'item_restore_prepare',kind:plan.kind,operation_id:plan.operation_id,local_id:plan.local_id,method:'apple_restore_item',args:[plan.operation_id,plan.owner_expected],kwargs:{}}
    assert.throws(()=>apple.itemRestoreOwner(request),/不属于/)
  }
  state.beforeRun=async({plan})=>{
    assert.throws(()=>apple.itemRestoreOwner({route:'item_restore_owner',kind:plan.kind,operation_id:plan.operation_id,local_id:plan.local_id,plan_fingerprint:plan.plan_fingerprint,method:'apple_capture_item_restore',args:[plan.local_id,{},'other',{},null],kwargs:{}}),/不属于/)
  }
  assert.equal((await owner.restoreAppleItem({kind:'calendar',identity:'local-id',operation_id:'a'.repeat(64)},()=>live)).status,'success')
  assert.throws(()=>apple.invoke({method:'resolve_conflict',arguments:{choice:'keep_apple'}}),/审批/)
  assert.throws(()=>apple.invoke({method:'resolve_conflict',arguments:{choice:'keep_alcor'}}),/审批/)
  assert.equal(state.nativeWrites,0)
  await assert.rejects(()=>owner.restoreAppleItem({kind:'calendar',identity:'local-id',operation_id:'a'.repeat(64),confirmed:true},()=>true))
})
test('owner serialization rechecks the live call immediately before commit',async()=>{
  const {owner,state}=host();let live=true;state.beforeOwner=async()=>{live=false}
  assert.equal((await owner.restoreAppleItem({kind:'calendar',identity:'local-id',operation_id:'a'.repeat(64)},()=>live)).status,'needs_review');assert.equal(state.writes,0)
})

test('receipt-only repeat has no prepare/write authorization and cannot dispatch another restore', async()=>{
  const {apple,owner,state}=host(),old='b'.repeat(64),plan={version:1,kind:'calendar',operation_id:old,local_id:'local-id',owner_expected:{identity:'local-id'},plan_fingerprint:'c'.repeat(64)}
  apple.worker.call=async(action,request)=>{
    if(action==='prepare_item_restore')return {mode:'receipt_finish',plan}
    assert.equal(action,'run_item_restore');assert.deepEqual(request.plan,{mode:'receipt_finish',plan})
    const auth={route:'item_restore',kind:'calendar',operation_id:old,local_id:'local-id',plan_fingerprint:plan.plan_fingerprint}
    assert.equal(apple.authorizeItemRestore(auth),false)
    const peer={...auth,route:'item_restore_owner',method:'apple_verify_item_restore',args:[old,plan.owner_expected],kwargs:{}}
    for(const method of ['apple_restore_item','apple_capture_item_restore'])assert.throws(()=>apple.itemRestoreOwner({...peer,method}),/不属于/)
    await apple.itemRestoreOwner(peer)
    return {status:'success',receipt_finished:true}
  }
  const value=await owner.restoreAppleItem({kind:'calendar',identity:'local-id',operation_id:'a'.repeat(64)},()=>true)
  assert.equal(value.receipt_finished,true);assert.deepEqual(state.calls,['apple_verify_item_restore']);assert.equal(state.writes,0);assert.equal(apple.itemRestores.size,0)
})
test('only an explicit missing summary selects the special local restore method',()=>{
  const item={type:'record',alcor_id:'local-id',local_missing:true}
  assert.equal(canRestoreLocalItem(item),true)
  assert.equal(canRestoreLocalItem({...item,local_missing:false,local_restore_pending:true}),true)
  assert.equal(canRestoreLocalItem({...item,local_missing:false,local_restore_pending:'true'}),false)
  assert.deepEqual(resolutionRequest(item,'calendar','keep_apple'),{method:'restore_local_item',arguments:{identity:'local-id'}})
  for(const local_missing of [false,undefined,'true',1])assert.equal(resolutionRequest({...item,local_missing},'calendar','keep_apple').method,'resolve_conflict')
  assert.equal(resolutionRequest(item,'calendar','keep_alcor').method,'resolve_conflict')
})

test('actual Gateway exposes only owner local restore and preserves the internal owner scope',async t=>{
  const {TypertRemoteService}=await import('@deepseek-ai/dsh-typert-protocol')
  const {default:Typert}=await import('@deepseek-ai/dsh-typert-registry')
  const {default:Gateway}=await import('@deepseek-ai/dsh-api-gateway')
  const {default:RemoteAccess}=await import('@deepseek-ai/dsh-api-remote-access')
  const {HostConnectionService}=await import('@deepseek-ai/dsh-client-connection')
  const fixture=host(),ctx=new Context();t.after(()=>ctx.fiber.dispose())
  class TestApple extends TypertRemoteService {constructor(ctx){super(ctx,'alcorAppleSync');this.active=true;this.itemRestores=fixture.apple.itemRestores;this.worker=fixture.apple.worker}}
  for(const name of ['restoreLocalItem','restoreItem','runItemRestore','authorizeItemRestore','itemRestoreOwner','invoke'])Object.defineProperty(TestApple.prototype,name,Object.getOwnPropertyDescriptor(Apple.prototype,name))
  await ctx.plugin(Typert);await ctx.plugin(RemoteAccess);await ctx.plugin(Gateway,{})
  ctx.provide('alcorOrganizer',fixture.owner);await ctx.plugin(TestApple)
  fixture.apple.ctx=ctx.alcorAppleSync.ctx
  ctx.typert.register({package:'@alcor/apple-sync',face:'host',schemas:[],invocations:remote.descriptors,model:{services:[],events:[],objects:[]}})
  await ctx.plugin(inner=>{new HostConnectionService(inner,[],{isAuthenticated:()=>true,authenticateApi(){}})})
  const owner=ctx.connection.admit({headers:{host:'localhost',origin:'http://localhost'}}).peer
  const outsider=ctx.connection.createPeer({principal:{kind:'synthetic-restore-delegate'},interactions:'none'})
  ctx.remoteAccess.registerPolicy('synthetic-restore-delegate',{bindings:()=>[{resource:{kind:'global'},role:{name:'test-restore',resource:'global',actions:['alcorAppleSync/restoreLocalItem']}}]})
  const call=(method,request,peer=owner)=>ctx.typertGateway.invoke({namespace:'alcorAppleSync',method,args:{request},peer})
  const result=await call('restoreLocalItem',{kind:'calendar',identity:'local-id'});assert.equal(result.status,'success')
  assert.equal(fixture.state.writes,1)
  await assert.rejects(call('restoreLocalItem',{kind:'calendar',identity:'local-id'},outsider),/所有者/)
  await assert.rejects(call('restoreLocalItem',{kind:'calendar',identity:'local-id',plan:{approved:true}}))
  for(const method of ['restoreItem','runItemRestore','itemRestoreOwner','authorizeItemRestore'])await assert.rejects(call(method,{}))
  for(const choice of ['keep_apple','keep_alcor'])await assert.rejects(call('invoke',{method:'resolve_conflict',arguments:{choice}}),/审批/)
})

test('a forged restore callback or background call cannot acquire an owner scope', async()=>{
  const {apple,owner,state}=host()
  const peer={route:'item_restore_owner',kind:'calendar',operation_id:'a'.repeat(64),local_id:'local-id',plan_fingerprint:'f'.repeat(64),method:'apple_restore_item',args:['a'.repeat(64),{identity:'local-id'}],kwargs:{}}
  assert.throws(()=>apple.itemRestoreOwner(peer),/不属于/)
  await assert.rejects(()=>owner.restoreAppleItem({kind:'calendar',identity:'local-id',operation_id:'a'.repeat(64)},()=>false),/失效/)
  assert.equal(state.prepares,0);assert.equal(state.writes,0)
})
test('Host catalog advertises only the restricted restore shape, not a raw keep_apple bypass',async()=>{
  const {apple}=host();apple.worker.call=async()=>[{name:'status',parameters:'()'}]
  const catalog=await apple.invoke({method:'catalog',arguments:{kind:'calendar'}})
  assert.equal(catalog.filter(x=>x.name==='restore_local_item').length,1)
  assert.match(catalog.find(x=>x.name==='restore_local_item').description,/不向 Apple 写入/)
})

test('a worker timeout ends authorization even if a peer commit is still in the owner queue',async()=>{
  const {apple,owner,state}=host(),original=apple.worker.call
  let release,queued
  apple.worker.call=async(action,request)=>{
    if(action==='prepare_item_restore')return original(action,request)
    const plan=request.plan
    owner.queue=new Promise(resolve=>{release=resolve})
    queued=apple.itemRestoreOwner({route:'item_restore_owner',kind:plan.kind,operation_id:plan.operation_id,local_id:plan.local_id,plan_fingerprint:plan.plan_fingerprint,method:'apple_restore_item',args:[plan.operation_id,plan.owner_expected],kwargs:{}})
    queued.catch(()=>{})
    throw new Error('synthetic worker timeout while owner queue is busy')
  }
  const result=await owner.restoreAppleItem({kind:'calendar',identity:'local-id',operation_id:'a'.repeat(64)},()=>true)
  assert.equal(result.status,'needs_review');release();await assert.rejects(queued,/失效/);assert.equal(state.writes,0)
})
