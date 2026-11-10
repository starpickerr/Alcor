import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
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
import { apply } from '../../packages/alcor-secretary/src/tools.js'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'

// Real SDK/tools, both real Host classes and separate real owner/AppleWorker processes.
// Only the native EventKit dependency and approval answerer are synthetic.
const kind = 'calendar', domain = 'calendar'
for (const entity of ['this', 'future', 'future-changed', 'future-finish-recovery'])
 test(`${kind} ${entity}: real SDK approval enumerates frozen native instances and both real workers finish once`, async t => {
  const root = await mkdtemp(join(tmpdir(), 'alcor-delete-wire-')), output = await mkdtemp(join(process.cwd(), '.delete-wire-build-'))
  const ctx = new Context(), ownerWorker = new BusinessWorker(join(root, 'owner'), { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) })
  let appleWorker
  t.after(async () => { await ctx.fiber.dispose(); if (appleWorker) await appleWorker.close(); await ownerWorker.close(); await rm(root, { recursive: true, force: true }); await rm(output, { recursive: true, force: true }) })
  const classes = {}
  for (const name of ['organizer', 'apple-sync']) {
    const outfile = join(output, name+'.mjs')
    await build({ entryPoints: [`packages/alcor-${name}/src/index.ts`], outfile, bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24', logLevel: 'silent' })
    classes[name] = (await import(pathToFileURL(outfile))).default
  }
  const owner = Object.create(classes.organizer.prototype), apple = Object.create(classes['apple-sync'].prototype)
  owner.active = true; owner.queue = Promise.resolve(); owner.deleteAuthorizers = new WeakMap(); owner.businessWorker = ownerWorker
  owner.ctx = { get: key => key === 'alcorAppleSync' ? apple : undefined }
  apple.active = true; apple.ctx = { alcorOrganizer: owner }; apple.preparingDeletes = new Map(); apple.preparedDeletes = new Map(); apple.runningDeletes = new Map()
  const peers = [], requests = []; let failFinishOnce = entity === 'future-finish-recovery'
  appleWorker = new BusinessWorker(join(root, 'apple'), { worker: new URL('./fixtures/apple-native-range-worker.py', import.meta.url), onRequest: input => {
    peers.push(input.route ?? 'apple_peer'); requests.push(structuredClone(input))
    if (input.route === 'approved_delete_owner' && input.action === 'apple_finish_approved_delete' && failFinishOnce) { failFinishOnce = false; throw new Error('synthetic lost finish before owner') }
    if (input.route === 'approved_delete') return apple.authorizeDeleteDispatch(input)
    if (input.route === 'approved_delete_prepare') return apple.approvedDeletePrepare(input)
    if (input.route === 'approved_delete_owner') return apple.approvedDeleteOwner(input)
    if (input.route === 'approved_delete_continue') return apple.continueDelete(input)
    return owner.applePeer(input)
  } }); apple.worker = appleWorker
  await appleWorker.call('initialize')
  t.diagnostic(JSON.stringify({ runtime: JSON.parse(await readFile(join(root, 'apple', 'synthetic-runtime.json'), 'utf8')), owner_spawn: ownerWorker.process.spawnargs, apple_spawn: appleWorker.process.spawnargs }))
  await apple.invoke({ method: 'start_sync', arguments: { kind } })
  const rows = await ownerWorker.call('invoke', { domain, method: 'list_events', arguments: {} })
  assert.equal(rows.length,4); const row = rows.find(r=>r.start_at.startsWith('2026-11-08')&&r.apple.group_id==='a')
  const later=rows.find(r=>r.start_at.startsWith('2026-11-09')&&r.apple.group_id==='a')
  const selected=rows.filter(r=>r.apple.group_id==='a'&&(entity==='this'?r.id===row.id:r.start_at>=row.start_at))
  const scope=entity==='this'?'this':'future'
  for (const plugin of [Sessions, Projections, Agents, SystemPrompt, Tools, Approval, Native]) await ctx.plugin(plugin, {})
  ctx.provide('alcorSecretary', {}); ctx.provide('alcorOrganizer', owner)
  const session = ctx.sessions.create('synthetic-wire'); session.append('turn/start', { turn: 1 })
  const agent = { ctx, options: { subagentDepth: 0 }, session }, preset = {}; bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'alcorSecretary', 'alcorOrganizer'] }))
  const asks = []; let answer = 'rejected'
  ctx.on('approval/request', async request => {
    asks.push(request)
    for(const target of selected)assert(request.reason.includes(target.id),request.reason)
    if(entity==='future-changed'&&answer==='allowed-once')await ownerWorker.call('invoke',{domain:'calendar',method:'update_event',arguments:{id:later.id,patch:{title:'Changed after concrete preview'}}})
    return answer
  })
  await ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: [{ source: { kind: 'user' } }] }, async () => {})
  const input = { domain, method: 'delete_event', arguments: { id: row.id, scope, occurrence_on:'2026-11-08' } }
  const call = async id => (await ctx.tools.execute({ agent, signal: AbortSignal.timeout(10000), calls: [{ type: 'tool-call', id, name: 'alcor_business', arguments: JSON.stringify(input) }] })).results[0].result
  const denied = await call('deny'); assert.equal(denied.isError, true); assert.equal(asks.length, 1, JSON.stringify(denied))
  await assert.rejects(() => readFile(join(root, 'apple', 'synthetic-native-writes.jsonl')), { code: 'ENOENT' })
  answer = 'allowed-once'
  const result = await call('confirm'); assert.equal(asks.length, 2)
  if(entity==='future-changed'){
    assert.equal(result.isError,true,JSON.stringify(result))
    assert.equal((await ownerWorker.call('invoke',{domain:'calendar',method:'list_events',arguments:{}})).length,4)
    await assert.rejects(()=>readFile(join(root,'apple','synthetic-native-writes.jsonl')),{code:'ENOENT'})
    return
  }
  assert.equal(result.isError, false, JSON.stringify(result))
  assert.match(asks[1].reason, /account1/)
  const writeCount = async () => { try { return (await readFile(join(root, 'apple', 'synthetic-native-writes.jsonl'), 'utf8')).trim().split('\n').length } catch (error) { if (error.code === 'ENOENT') return 0; throw error } }
  assert.equal(await writeCount(), 1)
  assert(peers.includes('approved_delete')); assert(peers.includes('approved_delete_owner'))
  if (entity==='future-finish-recovery') {
    assert.match(JSON.stringify(result), /needs_review/)
    const before = requests.length
    await apple.invoke({ method: 'sync', arguments: { kind } })
    const resume = requests.slice(before)
    assert.equal(resume.filter(request => request.route === 'approved_delete_continue' && request.phase === 'finish_begin').length, 1)
    assert.equal(resume.filter(request => request.route === 'approved_delete').length, 0)
    assert.deepEqual(resume.filter(request => request.route === 'approved_delete_owner').map(request => request.action), ['apple_finish_approved_delete'])
    assert.equal(apple.runningDeletes.size, 0); assert.equal(asks.length, 2)
    await assert.rejects(() => ownerWorker.call('invoke', { domain, method: 'get_event', arguments: { id: row.id } }), /not found/)
  }
  const remaining=await ownerWorker.call('invoke',{domain:'calendar',method:'list_events',arguments:{}})
  assert.deepEqual(new Set(remaining.map(r=>r.id)),new Set(rows.filter(r=>!selected.some(s=>s.id===r.id)).map(r=>r.id)))
  assert(remaining.some(r=>r.apple.group_id==='other'))
  await apple.invoke({method:'sync',arguments:{kind}});await apple.invoke({method:'sync',arguments:{kind}})
  const after=await ownerWorker.call('invoke',{domain:'calendar',method:'list_events',arguments:{}})
  assert(!after.some(r=>selected.some(s=>s.id===r.id)));assert.equal(await writeCount(),1)
  const replay = await call('confirm'); assert.equal(replay.isError, false, JSON.stringify(replay)); assert.equal(asks.length, 2)
  assert.equal(await writeCount(), 1)
})
