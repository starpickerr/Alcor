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
let Apple, Organizer, output
before(async () => {
  output = await mkdtemp(join(process.cwd(), '.conflict-build-'))
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
  apple.active = true; apple.preparedExports = new Map(); apple.runningExports = new Map(); apple.ctx = { alcorOrganizer: owner }
  owner.active = true; owner.exportAuthorizers = new WeakMap(); owner.queue = Promise.resolve(); owner.ctx = { get: key => key === 'alcorAppleSync' ? apple : undefined }
  const state = { writes: 0, native: 0, verifies: 0, asks: [], prepares: 0, confirmations: [], confirm: false, calls: [] }
  apple.confirmConflict = async request => { state.confirmations.push(request); await state.beforeConfirm?.(); return state.confirm }
  owner.businessWorker = { call: async (action, request) => {
    assert.equal(action, 'bound_conflict_peer'); state.calls.push(request)
    if (request.method === 'apple_verify_bound_conflict') { state.verifies++; return { local: { id: request.args[1].local_id }, receipt: 'synthetic' } }
    assert.equal(request.method, 'apple_commit_bound_conflict'); state.writes++; return { local: { id: request.args[1].local_id }, receipt: 'synthetic' }
  } }
  apple.worker = { call: async (action, request) => {
    if (action === 'prepare_approved_export') {
      state.prepares++; assert.equal(request.method, 'resolve_conflict')
      if (state.finish) return { mode: 'receipt_finish', plan: structuredClone(state.plan) }
      const args = request.arguments, nativeKey = args.kind === 'calendar' ? 'event_id' : 'reminder_id', groupKey = args.kind === 'calendar' ? 'calendar_id' : 'list_id'
      state.plan = { version: 1, operation_id: request.operation_id, kind: args.kind, mode: 'conflict', method: request.method, arguments: args,
        links_snapshot: [{ enabled: true, apple_id: 'container' }], skipped: 0, plan_fingerprint: 'f'.repeat(64), items: [{
          local_id: args.identity, source_id: 'source-account', group_id: 'container', action: args.choice === 'keep_apple' ? 'local_update' : 'update',
          wire_payload: { title: args.choice === 'keep_apple' ? 'Apple changed' : 'Alcor changed', notes: 'Exact full text', [groupKey]: 'container', [nativeKey]: 'native-id' },
          owner_snapshot: { generation: 'local-generation', local: { id: args.identity, title: 'Alcor changed' } },
          owner_inbound_snapshot: { generation: 'local-generation', related: 'frozen' }, mapping_snapshot: { state: 'conflict', [nativeKey]: 'native-id' },
          native_snapshot: { title: 'Apple changed', source_id: 'source-account', [groupKey]: 'container', [nativeKey]: 'native-id' },
          destination_snapshot: { id: 'container', source_id: 'source-account' }, baseline_snapshot: { version: 'old' },
        }] }
      await state.afterPrepare?.(state.plan)
      return structuredClone(state.plan)
    }
    assert.equal(action, 'run_approved_export')
    const finish = request.plan.mode === 'receipt_finish', plan = finish ? request.plan.plan : request.plan, item = plan.items[0]
    const auth = { route: 'approved_export', operation_id: plan.operation_id, plan_fingerprint: plan.plan_fingerprint, local_id: null }
    if (finish) assert.equal(apple.authorizeExportDispatch(auth), false)
    else {
      assert.equal(apple.authorizeExportDispatch(auth), true)
      await state.beforeRun?.()
      if (!apple.authorizeExportDispatch({ ...auth, local_id: item.local_id })) throw new Error('revoked before dispatch')
      if (plan.arguments.choice === 'keep_alcor') state.native++
    }
    const native = plan.arguments.choice === 'keep_apple' ? item.native_snapshot : { ...item.native_snapshot, title: item.wire_payload.title }
    const peer = { route: 'bound_conflict_owner', kind: plan.kind, operation_id: plan.operation_id, plan_fingerprint: plan.plan_fingerprint,
      local_id: item.local_id, method: finish ? 'apple_verify_bound_conflict' : 'apple_commit_bound_conflict', args: [plan.operation_id, item, plan.arguments.choice, native], kwargs: {} }
    for (const patch of [{ kind: 'other' }, { local_id: 'other' }, { plan_fingerprint: '0'.repeat(64) }, { kwargs: { confirmed: true } }, { args: [plan.operation_id, { ...item, local_id: 'other' }, plan.arguments.choice, native] }, { args: [plan.operation_id, item, plan.arguments.choice, { ...native, source_id: 'other' }] }]) assert.throws(() => apple.boundConflictOwner({ ...peer, ...patch }), /不属于/)
    if (finish) assert.throws(() => apple.boundConflictOwner({ ...peer, method: 'apple_commit_bound_conflict' }), /不属于/)
    state.peer = peer; state.auth = auth
    await apple.boundConflictOwner(peer)
    return { status: 'success', operation_id: plan.operation_id }
  } }
  return { apple, owner, state }
}
async function sdk(t, mode = 'native') {
  const f = host(), ctx = new Context(); t.after(() => ctx.fiber.dispose())
  for (const plugin of [Sessions, Projections, Agents, SystemPrompt, Tools, Approval]) await ctx.plugin(plugin, {})
  let ptcInput
  if (mode === 'ptc') {
    ctx.provide('ptcRuntime', { language:'typescript', programFormat:'function-body', isolation:'synthetic', executionInstructions:'', resolve:value=>value,
      run:async request=>{ try { return { logs:[], value:await request.bindings[0].functions.alcor_business(ptcInput) } } catch(error) { return { logs:[], error:{kind:'exception',message:error.message} } } }, release:async()=>{} })
    ctx.provide('workingDirectory',{ensure:async()=>process.cwd()}); await ctx.plugin(Ptc)
  } else await ctx.plugin(Native)
  ctx.provide('alcorSecretary',{});ctx.provide('alcorOrganizer',f.owner)
  const session=ctx.sessions.create('synthetic-bound-conflict');session.append('turn/start',{turn:1})
  const agent={ctx,options:{subagentDepth:0},session},preset={};bindScopeParent(agent,preset)
  await ctx.plugin(Object.assign(inner=>apply(createScope(inner,preset).ctx),{inject:['tools','systemPrompt','alcorSecretary','alcorOrganizer']}))
  f.state.answer = 'allowed-once';ctx.on('approval/request',async request=>{f.state.asks.push(request);await f.state.beforeAnswer?.();return f.state.answer})
  const turn=kind=>ctx.waterfall(scopeTarget(agent,agent),'agent/pre-step',{agent,messages:[{source:{kind}}]},async()=>{})
  const call=async(choice='keep_apple',kind='calendar',id='call',extra={},signal=AbortSignal.timeout(5000))=>{
    const args={domain:'apple',method:'resolve_conflict',arguments:{kind,[kind==='calendar'?'alcor_id':'todo_id']:'local-id',choice,...extra}};ptcInput=args
    const block=mode==='ptc'?{type:'tool-call',id,name:'run_code',arguments:JSON.stringify({code:'synthetic',description:'Resolve exact bound item'})}:{type:'tool-call',id,name:'alcor_business',arguments:JSON.stringify(args)}
    return (await ctx.tools.execute({agent,signal,calls:[block]})).results[0].result
  }
  return {...f,ctx,agent,session,turn,call}
}
for(const mode of ['native','ptc']) for(const choice of ['keep_apple','keep_alcor']) test(`${mode} ${choice}: real user turn, exact plan and separate external approval`,async t=>{
  const f=await sdk(t,mode);await f.turn('schedule');assert.equal((await f.call(choice)).isError,true);assert.equal(f.state.prepares,0)
  f.session.append('turn/end',{turn:1,reason:'completed'});f.session.append('turn/start',{turn:2});await f.turn('user')
  const result=await f.call(choice,'reminders');assert.equal(result.isError,false,JSON.stringify(result));assert.equal(f.state.writes,1)
  assert.equal(f.state.asks.length,choice==='keep_alcor'?1:0);assert.equal(f.state.native,choice==='keep_alcor'?1:0)
  if(choice==='keep_alcor')for(const text of ['local-id','source-account','container','native-id','Alcor changed','Apple changed','Exact full text'])assert.ok(f.state.asks[0].reason.includes(text),text)
  assert.equal(f.apple.runningExports.size,0);assert.equal(f.apple.authorizeExportDispatch(f.state.auth),false);assert.throws(()=>f.apple.boundConflictOwner(f.state.peer),/不属于/)
})
for(const changed of ['deny','turn','owner','abort'])test(`external ${changed} closes before dispatch`,async t=>{
  const f=await sdk(t),controller=new AbortController();await f.turn('user')
  f.state.beforeAnswer=async()=>{if(changed==='deny')f.state.answer='rejected';if(changed==='turn')f.session.append('turn/end',{turn:1,reason:'completed'});if(changed==='owner')f.owner.active=false;if(changed==='abort')controller.abort()}
  assert.equal((await f.call('keep_alcor','calendar','call',{},controller.signal)).isError,true);assert.equal(f.state.writes,0);assert.equal(f.state.native,0)
})
test('model flags and malformed original conflict arguments never become authorization',async t=>{
  const f=await sdk(t);await f.turn('user')
  for(const extra of [{confirm:true},{identity:'other'},{source:'user'},{operation_id:'a'.repeat(64)}])assert.equal((await f.call('keep_alcor','calendar',JSON.stringify(extra),extra)).isError,true)
  assert.equal(f.state.prepares,0);assert.equal(f.state.asks.length,0)
})
test('receipt-only new call does not repeat approval, owner write or native dispatch',async t=>{
  const f=await sdk(t);await f.turn('user');assert.equal((await f.call('keep_alcor')).isError,false)
  f.state.finish=true;assert.equal((await f.call('keep_alcor','calendar','new-call')).isError,false)
  assert.equal(f.state.asks.length,1);assert.equal(f.state.writes,1);assert.equal(f.state.native,1);assert.equal(f.state.verifies,1)
})
test('page owner is not an Apple-write confirmation; cancel then exact helper result controls commit',async()=>{
  const f=host(),peer={principal:{kind:'owner'},signal:new AbortController().signal}
  f.apple.ctx.invocation={peer,signal:new AbortController().signal}
  await assert.rejects(()=>f.apple.resolveItemConflict({kind:'calendar',identity:'local-id',choice:'keep_alcor'}),/取消/)
  assert.equal(f.state.writes,0);assert.equal(f.state.native,0);assert.equal(f.apple.preparedExports.size,0)
  f.state.confirm=true;assert.equal((await f.apple.resolveItemConflict({kind:'calendar',identity:'local-id',choice:'keep_alcor'})).status,'success')
  assert.equal(f.state.writes,1);assert.equal(f.state.confirmations[1].kind,'apple-update');assert.match(f.state.confirmations[1].detail,/Apple changed/)
  assert.equal((await f.apple.resolveItemConflict({kind:'calendar',identity:'local-id',choice:'keep_apple'})).status,'success');assert.equal(f.state.confirmations.length,2);assert.equal(f.state.native,1)
  assert.throws(()=>f.apple.invoke({method:'resolve_conflict',arguments:{choice:'keep_alcor'}}),/审批/)
})
test('page abort after prompt, non-owner and forged public plan remain closed',async()=>{
  const f=host(),controller=new AbortController(),peer={principal:{kind:'owner'},signal:controller.signal}
  f.apple.ctx.invocation={peer,signal:new AbortController().signal};f.state.confirm=true;f.state.beforeConfirm=async()=>controller.abort()
  await assert.rejects(()=>f.apple.resolveItemConflict({kind:'calendar',identity:'local-id',choice:'keep_alcor'}),/无效|消费/);assert.equal(f.state.writes,0)
  f.apple.ctx.invocation={peer:{...peer,principal:{kind:'delegate'}},signal:new AbortController().signal}
  await assert.rejects(()=>f.apple.resolveItemConflict({kind:'calendar',identity:'local-id',choice:'keep_apple'}),/所有者/)
  await assert.rejects(()=>f.apple.resolveItemConflict({kind:'calendar',identity:'local-id',choice:'keep_apple',plan:{}}))
})
test('actual Gateway exposes the page flow but owner identity alone cannot approve an Apple write',async t=>{
  const {TypertRemoteService}=await import('@deepseek-ai/dsh-typert-protocol')
  const {default:Typert}=await import('@deepseek-ai/dsh-typert-registry')
  const {default:Gateway}=await import('@deepseek-ai/dsh-api-gateway')
  const {default:RemoteAccess}=await import('@deepseek-ai/dsh-api-remote-access')
  const {HostConnectionService}=await import('@deepseek-ai/dsh-client-connection')
  const f=host(),ctx=new Context();t.after(()=>ctx.fiber.dispose())
  class TestApple extends TypertRemoteService {constructor(ctx){super(ctx,'alcorAppleSync');this.active=true;this.preparedExports=f.apple.preparedExports;this.runningExports=f.apple.runningExports;this.worker=f.apple.worker;this.confirmConflict=f.apple.confirmConflict}}
  for(const name of ['resolveItemConflict','prepareExport','commitExport','prepareApprovedExport','commitApprovedExport','boundConflictOwner','authorizeExportDispatch','invoke'])Object.defineProperty(TestApple.prototype,name,Object.getOwnPropertyDescriptor(Apple.prototype,name))
  await ctx.plugin(Typert);await ctx.plugin(RemoteAccess);await ctx.plugin(Gateway,{})
  ctx.provide('alcorOrganizer',f.owner);await ctx.plugin(TestApple);f.apple.ctx=ctx.alcorAppleSync.ctx
  ctx.typert.register({package:'@alcor/apple-sync',face:'host',schemas:[],invocations:remote.descriptors,model:{services:[],events:[],objects:[]}})
  await ctx.plugin(inner=>{new HostConnectionService(inner,[],{isAuthenticated:()=>true,authenticateApi(){}})})
  const owner=ctx.connection.admit({headers:{host:'localhost',origin:'http://localhost'}}).peer
  const outsider=ctx.connection.createPeer({principal:{kind:'synthetic-conflict-delegate'},interactions:'none'})
  ctx.remoteAccess.registerPolicy('synthetic-conflict-delegate',{bindings:()=>[{resource:{kind:'global'},role:{name:'test-conflict',resource:'global',actions:['alcorAppleSync/resolveItemConflict']}}]})
  const call=(method,request,peer=owner)=>ctx.typertGateway.invoke({namespace:'alcorAppleSync',method,args:{request},peer})
  const input={kind:'calendar',identity:'local-id',choice:'keep_alcor'}
  await assert.rejects(call('resolveItemConflict',input),/取消/);assert.equal(f.state.writes,0)
  f.state.confirm=true;assert.equal((await call('resolveItemConflict',input)).status,'success');assert.equal(f.state.writes,1)
  assert.equal((await call('resolveItemConflict',{...input,choice:'keep_apple'})).status,'success');assert.equal(f.state.native,1)
  await assert.rejects(call('resolveItemConflict',input,outsider),/所有者/)
  await assert.rejects(call('resolveItemConflict',{...input,plan:{approved:true}}))
  for(const method of ['prepareExport','commitExport','boundConflictOwner','prepareApprovedExport','commitApprovedExport'])await assert.rejects(call(method,{}))
  for(const choice of ['keep_apple','keep_alcor'])await assert.rejects(call('invoke',{method:'resolve_conflict',arguments:{choice}}),/审批/)
})

test('normal Host ask is preserved for local choice, without adding an Apple-write approval',async t=>{
  const f=await sdk(t);await f.turn('user')
  f.ctx.on('tools/pre-execute',()=>({kind:'ask',reason:'Existing Host policy'}))
  assert.equal((await f.call('keep_apple')).isError,false);assert.equal(f.state.asks.length,1);assert.equal(f.state.asks[0].reason,'Existing Host policy');assert.equal(f.state.native,0)
})
test('a timed-out worker closes a queued owner write scope',async()=>{
  const f=host(),input={method:'resolve_conflict',arguments:{kind:'calendar',identity:'local-id',choice:'keep_apple'},operation_id:'a'.repeat(64)}
  const authorize=()=>true,plan=await f.owner.prepareApprovedExport(input,authorize)
  let release,queued
  f.owner.queue=new Promise(resolve=>{release=resolve})
  f.apple.worker.call=async()=>{
    const item=plan.items[0]
    queued=f.apple.boundConflictOwner({route:'bound_conflict_owner',kind:plan.kind,operation_id:plan.operation_id,plan_fingerprint:plan.plan_fingerprint,
      local_id:item.local_id,method:'apple_commit_bound_conflict',args:[plan.operation_id,item,'keep_apple',item.native_snapshot],kwargs:{}})
    queued.catch(()=>{})
    throw new Error('synthetic timeout')
  }
  assert.equal((await f.owner.commitApprovedExport(plan,authorize)).status,'needs_review')
  release();await assert.rejects(queued,/失效/);assert.equal(f.state.writes,0)
})
