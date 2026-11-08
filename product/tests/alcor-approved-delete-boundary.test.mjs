import test, { before, after } from 'node:test'
import assert from 'node:assert/strict'
import { isDeepStrictEqual } from 'node:util'
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
import Approval, { setApprovalPolicy } from '@deepseek-ai/dsh-user-approval'
import * as Native from '@deepseek-ai/dsh-tool-executor-native'
import * as Ptc from '@deepseek-ai/dsh-tool-executor-ptc'
import { createDeletePageHost } from '../../packages/alcor-secretary/src/delete-page-host.js'
import { apply } from '../../packages/alcor-secretary/src/tools.js'
import { remote as organizerRemote } from '../../packages/alcor-organizer/src/remote.js'
import { remote as appleRemote } from '../../packages/alcor-apple-sync/src/remote.js'

let Organizer, AppleSync, output
before(async () => {
  output = await mkdtemp(join(process.cwd(), '.alcor-approved-delete-build-'))
  for (const [name, source] of [['organizer', 'alcor-organizer'], ['apple', 'alcor-apple-sync']]) {
    const outfile = join(output, name + '.mjs')
    await build({ entryPoints: [`packages/${source}/src/index.ts`], outfile, bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24', logLevel: 'silent' })
    const Klass = (await import(pathToFileURL(outfile))).default
    if (name === 'organizer') Organizer = Klass; else AppleSync = Klass
  }
})
after(async () => { if (output) await rm(output, { recursive: true, force: true }) })

// Real SDK Sessions, projections, approval and production tool/Host methods.
// Only business/native workers and the PTC program substrate are synthetic.
async function fixture(t, mode = 'native', kind = 'local') {
  const ctx = new Context(); t.after(() => ctx.fiber.dispose())
  for (const plugin of [Sessions, Projections, Agents, SystemPrompt, Tools, Approval]) await ctx.plugin(plugin, {})
  const state = { kind, answer: 'allowed-once', asks: [], events: [], ownerWrites: 0, nativeSends: 0, attempts: 0, privateActions: [],
    beforeAnswer: async () => {}, beforeOwner: async () => {}, beforeDispatch: async () => {}, snapshot: 'a'.repeat(64), host: 'allow', source: 'user' }
  ctx.on('session/event', (_session, event) => state.events.push(event))
  let ptcInput
  if (mode === 'ptc') {
    ctx.provide('ptcRuntime', { language: 'typescript', programFormat: 'function-body', isolation: 'synthetic', executionInstructions: '', resolve: value => value,
      run: async request => { try { return { logs: [], value: await request.bindings[0].functions.alcor_business(ptcInput) } } catch (error) { return { logs: [], error: { kind: 'exception', message: error.message } } } }, release: async () => {} })
    ctx.provide('workingDirectory', { ensure: async () => process.cwd() }); await ctx.plugin(Ptc)
  } else await ctx.plugin(Native)
  const owner = Object.create(Organizer.prototype), apple = Object.create(AppleSync.prototype)
  owner.active = true; owner.queue = Promise.resolve(); owner.deleteAuthorizers = new WeakMap()
  apple.active = true; apple.preparingDeletes = new Map(); apple.preparedDeletes = new Map(); apple.runningDeletes = new Map()
  owner.ctx = { get: name => name === 'alcorAppleSync' ? apple : undefined }
  apple.ctx = { alcorOrganizer: owner }
  owner.pageDeletes = createDeletePageHost({ prepare: (request, authorize) => owner.prepareApprovedDelete(request, authorize),
    commit: (prepared, authorize) => owner.commitApprovedDelete(prepared, authorize), confirm: async (request, signal) => {
      state.pagePrompts ??= []; state.pagePrompts.push(request); await state.beforePageConfirm?.(signal); return state.pageConfirmed === true
    } })
  const binding = { source_id: 'synthetic-source', group_id: 'synthetic-group', apple_id: 'synthetic-native', can_delete: true }
  owner.businessWorker = { call: async (action, input) => {
    state.ownerInput = input
    if (action === 'verify_confirmed_delete_finish') return state.finishVerified === true && isDeepStrictEqual(input.plan, state.confirmedPlan)
    if (action === 'prepare_confirmed_delete') { if (!state.confirmedPlan || state.continuationBlocked) throw new Error('confirmed pending record unavailable'); return structuredClone(state.confirmedPlan) }
    if (action === 'prepare_delete_receipt') return state.receipt ? { ...state.receipt, operation_id: input.operation_id } : null
    if (action === 'invoke') return { id: 'synthetic-id', title: 'Synthetic target', ...(['apple', 'import'].includes(state.kind) ? { apple: binding } : {}) }
    if (['local_delete_prepare', 'prepare_local_occurrence_delete', 'prepare_apple_delete', 'prepare_import_category_delete'].includes(action)) {
      const category = input.category || input.method === 'delete_category' || action === 'prepare_import_category_delete'
      const plan = { id: 'synthetic-id', snapshot: state.snapshot, targets: [{ id: 'synthetic-id', title: 'Synthetic target', ...(category ? { category: true } : {}) }] }
      if (state.receipt) return { ...state.receipt, operation_id: input.operation_id }
      if (action === 'prepare_local_occurrence_delete') Object.assign(plan, { occurrence_on: input.occurrence_on, scope: input.scope, reversible: input.scope === 'future' })
      if (action === 'prepare_apple_delete') Object.assign(plan, { apple: { entity: category ? 'category' : 'item', binding, scope: category ? 'category' : input.arguments.occurrence_on ? input.arguments.scope ?? 'this' : 'item', occurrence_on: input.arguments.occurrence_on ?? null } })
      state.proposal = plan; return plan
    }
    if (['local_delete_commit', 'commit_local_occurrence_delete', 'commit_apple_delete', 'commit_import_category_delete'].includes(action)) {
      await state.beforeOwner()
      if (input.snapshot !== state.snapshot) throw new Error('owner version changed')
      state.ownerActions ??= []; state.ownerActions.push(action); state.ownerWrites++; return { deleted: true, operation_id: input.operation_id, pending: action === 'commit_apple_delete' }
    }
    if (action.startsWith('apple_')) { state.privateActions.push({ action, input }); return { state: 'sending' } }
    throw new Error('Unexpected synthetic action ' + action)
  } }
  apple.worker = { call: async (action, input) => {
    if (action === 'prepare_approved_delete') {
      if (state.kind === 'import') {
        const args = { domain: input.domain, category_id: input.category_id, operation_id: input.operation_id }
        if (state.badPrepare) {
          for (const patch of [{ category_id: 'other' }, { domain: input.domain === 'tasks' ? 'calendar' : 'tasks' }, { operation_id: 'other' }])
            assert.throws(() => apple.approvedDeletePrepare({ route: 'approved_delete_prepare', action: 'prepare_import_category_delete', args: { ...args, ...patch } }), /不属于/)
        }
        const local_plan = await apple.approvedDeletePrepare({ route: 'approved_delete_prepare', action: 'prepare_import_category_delete', args })
        return { version: 1, domain: input.domain, kind: input.domain === 'tasks' ? 'reminders' : 'calendar', operation_id: input.operation_id,
          mode: 'local-import-unlink', local_plan, source_id: 'synthetic-source', plan_fingerprint: 'c'.repeat(64),
          link_snapshot: { apple_id: 'synthetic-native', alcor_id: state.badLink ? 'other' : input.category_id,
            direction: 'import', enabled: state.linkEnabled ?? true, apple_name: 'Synthetic Apple category', alcor_name: 'Synthetic target', name_conflict: false, updated_at: '2026-11-09T00:00:00Z' } }
      }
      if (input.category_id) input = { ...input, local_plan: await apple.approvedDeletePrepare({ route: 'approved_delete_prepare', action: 'prepare_apple_delete',
        args: { domain: input.domain, method: 'delete_category', arguments: { [input.domain === 'tasks' ? 'category_id' : 'id']: input.category_id }, operation_id: input.operation_id } }) }
      return { version: 1, domain: input.domain, kind: input.domain === 'tasks' ? 'reminders' : 'calendar', operation_id: input.operation_id,
      local_plan: input.local_plan, native: { version: 1, entity: input.local_plan.apple.entity, source_id: 'synthetic-source', group_id: input.local_plan.apple.entity === 'category' ? 'synthetic-native' : 'synthetic-group', series_apple_id: 'synthetic-native', instance_apple_id: 'synthetic-instance',
        native_fingerprint: 'b'.repeat(64), native_snapshot: { id: 'synthetic-native', version: 7 }, scope: input.local_plan.apple.scope, occurrence_on: input.local_plan.apple.occurrence_on }, plan_fingerprint: 'c'.repeat(64) }
    }
    if (action !== 'run_approved_delete') throw new Error('Unexpected synthetic Apple action')
    const plan = input.plan, peer = { operation_id: plan.operation_id, plan_fingerprint: plan.plan_fingerprint, local_id: plan.local_plan.id }
    state.runningPlan = plan; state.peer = peer
    await state.beforeDispatch()
    assert.equal(apple.authorizeDeleteDispatch({ route: 'approved_delete', ...peer }), !state.expectRevoked)
    if (state.expectRevoked) throw new Error('authorization revoked')
    if (plan.mode === 'local-import-unlink') {
      const args = { operation_id: plan.operation_id, category_id: plan.local_plan.id, snapshot: plan.local_plan.snapshot }
      for (const patch of [{ category_id: 'other' }, { snapshot: 'd'.repeat(64) }])
        assert.throws(() => apple.approvedDeleteOwner({ route: 'approved_delete_owner', action: 'commit_import_category_delete', ...peer, args: { ...args, ...patch } }), /不属于/)
      assert.throws(() => apple.approvedDeleteOwner({ route: 'approved_delete_owner', action: 'apple_begin_approved_delete', ...peer, args: { operation_id: plan.operation_id } }), /不属于/)
      await apple.approvedDeleteOwner({ route: 'approved_delete_owner', action: 'commit_import_category_delete', ...peer, args })
      return { status: 'done', operation_id: plan.operation_id, local_deleted: true, apple_unchanged: true }
    }
    if (state.ownerPeer) await apple.approvedDeleteOwner({ route: 'approved_delete_owner', action: 'apple_begin_approved_delete', ...peer,
      args: { operation_id: plan.operation_id, document: { synthetic: true }, binding } })
    state.attempts++
    if (state.safeRetry) { assert.equal(apple.authorizeDeleteDispatch({ route: 'approved_delete', ...peer }), true); state.attempts++ }
    state.nativeSends++
    if (state.unknown) throw new Error('synthetic response lost after send')
    return { status: 'done', operation_id: plan.operation_id, deleted: true }
  } }
  ctx.provide('alcorSecretary', {}); ctx.provide('alcorOrganizer', owner)
  const session = ctx.sessions.create('synthetic-delete-session'); session.append('turn/start', { turn: 1 })
  const agent = { ctx, options: { subagentDepth: 0 }, session }; const signal = new AbortController()
  const preset = {}; bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'alcorSecretary', 'alcorOrganizer'] }))
  ctx.on('tools/pre-execute', (execution, next) => { if (execution.name === 'alcor_business') state.execution = execution; return next() }, { prepend: true })
  ctx.on('tools/pre-execute', (_execution, next) => state.host === 'allow' ? next() : { kind: state.host, reason: 'Synthetic host policy' })
  ctx.on('approval/request', async (request, next) => { state.asks.push(request); state.answerSignalLive = !request.signal.aborted; await state.beforeAnswer(); return state.answer === 'none' ? next() : state.answer })
  const turn = source => ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: [{ source: { kind: source } }] }, async () => {})
  await turn('user')
  const call = async (domain = 'tasks', method = 'delete_todo', args = { todo_id: 'synthetic-id' }, callId = 'synthetic-call') => {
    ptcInput = { domain, method, arguments: args }
    const block = mode === 'ptc' ? { type: 'tool-call', id: callId, name: 'run_code', arguments: JSON.stringify({ code: 'synthetic', description: 'Synthetic non-native deletion validation' }) }
      : { type: 'tool-call', id: callId, name: 'alcor_business', arguments: JSON.stringify(ptcInput) }
    return (await ctx.tools.execute({ agent, signal: signal.signal, calls: [block] })).results[0].result
  }
  return { ctx, state, owner, apple, agent, session, signal, call, turn }
}

for (const mode of ['native', 'ptc']) for (const kind of ['local', 'apple']) test(`${mode} ${kind}: production entry asks once for the exact plan, then uses private Host routes`, async t => {
  const f = await fixture(t, mode, kind); f.state.ownerPeer = kind === 'apple'
  const result = await f.call()
  assert.equal(result.isError, false, JSON.stringify(result)); assert.equal(f.state.asks.length, 1); assert.equal(f.state.ownerWrites, 1)
  assert.match(f.state.asks[0].reason, /Synthetic target/); assert.match(f.state.asks[0].reason, /aaaaaaaa/)
  assert.equal(f.state.asks[0].agent, f.agent); assert.equal(f.state.answerSignalLive, true)
  assert.equal(f.state.nativeSends, kind === 'apple' ? 1 : 0)
  if (kind === 'apple') { assert.match(f.state.asks[0].reason, /synthetic-source/); assert.equal(f.state.privateActions[0].input.domain, 'tasks'); assert.equal(f.apple.runningDeletes.size, 0) }
})

for (const [domain, method, args] of [
  ['tasks', 'delete_category', { category_id: 'synthetic-id' }], ['tasks', 'delete_external_todo', { source_key: 'synthetic-key', confirm: true }],
  ['calendar', 'delete_event', { id: 'synthetic-id' }], ['calendar', 'delete_category', { id: 'synthetic-id' }],
  ['calendar', 'exclude_occurrence', { series_id: 'synthetic-id', occurrence_on: '2026-11-09' }],
  ['calendar', 'delete_event', { id: 'synthetic-id', occurrence_on: '2026-11-09', scope: 'future' }],
]) test(`Apple target and scope: ${domain}.${method} ${JSON.stringify(args)}`, async t => {
  const f = await fixture(t, 'native', 'apple'); assert.equal((await f.call(domain, method, args)).isError, false)
  assert.equal(f.state.asks.length, 1); assert.equal(f.state.nativeSends, 1)
})

test('reject, cancel, missing answerer, invalid outcome, never, idle, schedule and delegation perform no owner write', async t => {
  for (const scenario of ['rejected', 'cancelled', 'none', 'allowed', 'never', 'idle', 'schedule', 'child', 'host-deny']) {
    const f = await fixture(t)
    if (scenario === 'never') setApprovalPolicy(f.session, 'never')
    else if (scenario === 'idle') f.session.append('turn/end', { turn: 1, reason: 'completed' })
    else if (scenario === 'schedule') { f.session.append('turn/end', { turn: 1, reason: 'completed' }); f.session.append('turn/start', { turn: 2 }); await f.turn('schedule') }
    else if (scenario === 'child') { f.agent.session = f.ctx.sessions.create('synthetic-child', { meta: { parentSession: 'synthetic-parent' } }); f.agent.session.append('turn/start', { turn: 1 }); await f.turn('user') }
    else if (scenario === 'host-deny') f.state.host = 'deny'
    else f.state.answer = scenario
    assert.equal((await f.call()).isError, true, scenario); assert.equal(f.state.ownerWrites, 0, scenario)
  }
})

test('plan is frozen; changed parameters, policy, turn, provider or owner version stop before writes', async t => {
  for (const change of [f => { f.state.execution.arguments.arguments.todo_id = 'other' }, f => setApprovalPolicy(f.session, 'never'),
    f => { f.session.append('turn/end', { turn: 1, reason: 'completed' }); f.session.append('turn/start', { turn: 2 }) },
    f => f.signal.abort(), f => { f.state.snapshot = 'd'.repeat(64) }, f => { f.state.execution.callId = 'other' },
    f => { f.state.execution.agent = { ...f.agent } }, f => f.ctx.tools.guard(() => 'revoked')]) {
    const f = await fixture(t); f.state.beforeAnswer = () => change(f)
    assert.equal((await f.call()).isError, true, String(change)); assert.equal(f.state.ownerWrites, 0)
  }
  const frozen = await fixture(t); frozen.state.beforeAnswer = () => { frozen.state.proposal.targets[0].title = 'Mutated proposal' }
  assert.equal((await frozen.call()).isError, false); assert.match(frozen.state.asks[0].reason, /Synthetic target/)
})

test('Host retains one grant for worker retry requests; unknown response is not retried (unsent proof belongs to Python)', async t => {
  const retry = await fixture(t, 'native', 'apple'); retry.state.safeRetry = true
  assert.equal((await retry.call()).isError, false); assert.equal(retry.state.asks.length, 1); assert.equal(retry.state.attempts, 2); assert.equal(retry.state.nativeSends, 1)
  const unknown = await fixture(t, 'native', 'apple'); unknown.state.unknown = true
  const result = await unknown.call(); assert.match(JSON.stringify(result), /needs_review/); assert.equal(unknown.state.attempts, 1)
  assert.equal(unknown.apple.authorizeDeleteDispatch({ route: 'approved_delete', ...unknown.state.peer }), false)
  await assert.rejects(() => unknown.apple.commitApprovedDelete(unknown.state.runningPlan, () => true), /无效或已消费/)
})

test('revocation between owner queue and native dispatch does not send; owner route cannot change target or domain', async t => {
  const f = await fixture(t, 'native', 'apple'); f.state.expectRevoked = true; f.state.beforeDispatch = () => f.signal.abort()
  await f.call(); assert.equal(f.state.nativeSends, 0)
  const bad = await fixture(t, 'native', 'apple'); bad.state.beforeDispatch = async () => {
    for (const patch of [{ operation_id: 'e'.repeat(64) }, { local_id: 'other' }, { plan_fingerprint: 'f'.repeat(64) }]) assert.equal(bad.apple.authorizeDeleteDispatch({ route: 'approved_delete', ...bad.state.peer, ...patch }), false)
    assert.throws(() => bad.apple.approvedDeleteOwner({ route: 'approved_delete_owner', ...bad.state.peer, action: 'apple_finish_approved_delete', args: { operation_id: bad.state.peer.operation_id, domain: 'calendar' } }), /不属于/)
  }
  assert.equal((await bad.call()).isError, false)
})

test('completed or pending replay is read-only, never a fresh dispatch grant', async t => {
  const f = await fixture(t, 'native', 'apple'); f.state.receipt = { replayed: true, operation_id: 'synthetic-prior', state: 'uncertain', pending: true, targetConflict: false }
  const result = await f.call(); assert.equal(result.isError, false); assert.equal(f.state.asks.length, 0); assert.equal(f.state.ownerWrites, 0); assert.equal(f.state.nativeSends, 0)
})

test('private methods are absent from Remote descriptors; public direct deletion and page ticket remain non-authorizing', async t => {
  assert.doesNotMatch(JSON.stringify([organizerRemote.descriptors, appleRemote.descriptors]), /prepareApprovedDelete|commitApprovedDelete|approvedDeletePeer|approvedDeleteOwner|approvedDeletePrepare|authorizeDeleteDispatch|prepareConfirmedDelete|verifyConfirmedDeleteFinish|continueDelete/)
  const f = await fixture(t)
  await assert.rejects(() => f.owner.business({ domain: 'tasks', method: 'delete_todo', arguments: { todo_id: 'synthetic-id' } }), /审批|确认/)
  await assert.rejects(() => f.owner.preparePageDelete({ domain: 'tasks', kind: 'item', id: 'synthetic-id' }), /所有者/)
  f.owner.ctx.invocation = { peer: { principal: { kind: 'owner' } } }
  await assert.rejects(() => f.owner.prepareApprovedDelete({ domain: 'tasks', method: 'delete_todo', arguments: { todo_id: 'synthetic-id' }, operation_id: 'a'.repeat(64) }, () => true), /真实会话/)
  assert.equal(f.state.ownerWrites, 0)
})

for (const domain of ['tasks', 'calendar']) test(`import category ${domain}: concrete local-unlink approval, no native/queued Apple deletion`, async t => {
  const f = await fixture(t, 'native', 'import'); f.state.badPrepare = true
  const result = await f.call(domain, 'delete_category', { [domain === 'tasks' ? 'category_id' : 'id']: 'synthetic-id' })
  assert.equal(result.isError, false, JSON.stringify(result)); assert.equal(f.state.asks.length, 1)
  assert.match(f.state.asks[0].reason, /停止导入/); assert.match(f.state.asks[0].reason, /Apple 不变/)
  assert.match(f.state.asks[0].reason, /Synthetic Apple category/); assert.match(f.state.asks[0].reason, /synthetic-source/)
  assert.deepEqual(f.state.ownerActions, ['commit_import_category_delete']); assert.equal(f.state.nativeSends, 0)
  assert.equal(f.apple.preparingDeletes.size, 0); assert.equal(f.apple.runningDeletes.size, 0)
  assert.throws(() => f.apple.approvedDeletePrepare({ route: 'approved_delete_prepare', action: 'prepare_import_category_delete', args: f.state.ownerInput }), /不属于/)
})

test('import category rejects wrong link and declined approval without local or Apple write', async t => {
  for (const scenario of ['wrong-link', 'rejected', 'cancelled']) {
    const f = await fixture(t, 'native', 'import'); if (scenario === 'wrong-link') f.state.badLink = true; else f.state.answer = scenario
    assert.equal((await f.call('tasks', 'delete_category', { category_id: 'synthetic-id' })).isError, true)
    assert.equal(f.state.ownerWrites, 0); assert.equal(f.state.nativeSends, 0)
  }
})

for (const kind of ['local', 'apple', 'import']) test(`page ${kind}: owner ticket only previews; separate helper confirms frozen scope`, async t => {
  const f = await fixture(t, 'native', kind), peer = { principal: { kind: 'owner' }, signal: new AbortController().signal }
  const invocation = { peer, signal: new AbortController().signal }
  const input = { domain: 'tasks', kind: kind === 'import' ? 'category' : 'item', id: 'synthetic-id' }
  const preview = await f.owner.pageDeletes.preview(invocation, input)
  assert.equal(f.state.ownerWrites, 0); assert.equal(f.state.nativeSends, 0); assert.equal(f.state.pagePrompts, undefined)
  assert.equal(f.state.asks.length, 0)
  if (kind === 'import') assert.equal(preview.importUnlink.source_id, 'synthetic-source')
  await assert.rejects(() => f.owner.pageDeletes.commit(invocation, preview), /取消|未获确认/)
  assert.equal(f.state.ownerWrites, 0); assert.equal(f.state.nativeSends, 0)
  const approved = await f.owner.pageDeletes.preview(invocation, input); f.state.pageConfirmed = true
  const result = await f.owner.pageDeletes.commit(invocation, approved)
  assert.equal(result.deleted || result.local_deleted, true); assert.equal(f.state.ownerWrites, 1)
  assert.equal(f.state.nativeSends, kind === 'apple' ? 1 : 0); assert.equal(f.state.asks.length, 0)
  assert.match(f.state.pagePrompts[1].detail, /Synthetic target/)
  await assert.rejects(() => f.owner.pageDeletes.commit(invocation, approved), /失效/)
})

test('page cancellation, disconnect, changed owner version and different peer cannot consume a preview', async t => {
  for (const scenario of ['cancel', 'disconnect', 'version', 'other-peer']) {
    const f = await fixture(t), abort = new AbortController(), peer = { principal: { kind: 'owner' }, signal: abort.signal }
    const invocation = { peer, signal: new AbortController().signal }, input = { domain: 'tasks', kind: 'item', id: 'synthetic-id' }
    const preview = await f.owner.pageDeletes.preview(invocation, input); f.state.pageConfirmed = true
    if (scenario === 'other-peer') {
      await assert.rejects(() => f.owner.pageDeletes.commit({ ...invocation, peer: { ...peer } }, preview), /失效/)
      assert.equal(f.state.pagePrompts, undefined)
    } else {
      f.state.beforePageConfirm = () => {
        if (scenario === 'cancel') f.owner.pageDeletes.cancel(invocation, preview)
        if (scenario === 'disconnect') abort.abort()
        if (scenario === 'version') f.state.snapshot = 'e'.repeat(64)
      }
      await assert.rejects(() => f.owner.pageDeletes.commit(invocation, preview))
    }
    assert.equal(f.state.ownerWrites, 0); assert.equal(f.state.nativeSends, 0)
  }
})

test('continuation reads a separate confirmed owner record, not an old execution or an SDK grant', async t => {
  const f = await fixture(t, 'native', 'apple')
  const empty = { route: 'approved_delete_continue', phase: 'begin', domain: 'tasks', operation_id: 'f'.repeat(64) }
  await assert.rejects(() => f.apple.continueDelete(empty), /unavailable/)
  await f.call(); f.state.confirmedPlan = f.state.runningPlan
  f.signal.abort(); setApprovalPolicy(f.session, 'never')
  const begin = { ...empty, operation_id: f.state.confirmedPlan.operation_id }
  const plan = await f.apple.continueDelete(begin)
  const peer = { route: 'approved_delete', operation_id: plan.operation_id, plan_fingerprint: plan.plan_fingerprint, local_id: plan.local_plan.id }
  assert.equal(f.apple.authorizeDeleteDispatch(peer), true)
  assert.equal(f.apple.authorizeDeleteDispatch({ ...peer, local_id: 'another-target' }), false)
  await assert.rejects(() => f.apple.continueDelete(begin), /正在执行/)
  const end = { ...peer, route: 'approved_delete_continue', phase: 'end' }
  assert.equal(await f.apple.continueDelete({ ...end, plan_fingerprint: 'f'.repeat(64) }), false)
  assert.equal(await f.apple.continueDelete(end), true); assert.equal(f.apple.authorizeDeleteDispatch(peer), false)
  f.state.continuationBlocked = true
  await assert.rejects(() => f.apple.continueDelete(begin), /unavailable/)
  assert.equal(f.state.asks.length, 1)
})

test('continuation cannot overlap a live approved run; stopping the plugin invalidates its exact scope', async t => {
  const f = await fixture(t, 'native', 'apple')
  f.state.beforeDispatch = async () => {
    await assert.rejects(() => f.apple.continueDelete({ route: 'approved_delete_continue', phase: 'begin', domain: 'tasks', operation_id: f.state.peer.operation_id }), /正在执行/)
  }
  await f.call(); f.state.confirmedPlan = f.state.runningPlan
  const plan = await f.apple.continueDelete({ route: 'approved_delete_continue', phase: 'begin', domain: 'tasks', operation_id: f.state.runningPlan.operation_id })
  f.apple.active = false
  assert.equal(f.apple.authorizeDeleteDispatch({ route: 'approved_delete', operation_id: plan.operation_id, plan_fingerprint: plan.plan_fingerprint, local_id: plan.local_plan.id }), false)
})

test('receipt continuation can finish only the verified operation and cannot dispatch native writes', async t => {
  const f = await fixture(t, 'native', 'apple')
  await f.call(); f.state.confirmedPlan = structuredClone(f.state.runningPlan)
  const begin = { route: 'approved_delete_continue', phase: 'finish_begin', domain: 'tasks', operation_id: f.state.confirmedPlan.operation_id, plan: f.state.confirmedPlan }
  await assert.rejects(() => f.apple.continueDelete(begin), /无法验证/)
  f.state.finishVerified = true
  const changed = structuredClone(begin); changed.plan.local_plan.snapshot = 'd'.repeat(64)
  await assert.rejects(() => f.apple.continueDelete(changed), /无法验证/)
  const plan = await f.apple.continueDelete(begin)
  const peer = { operation_id: plan.operation_id, plan_fingerprint: plan.plan_fingerprint, local_id: plan.local_plan.id }
  assert.equal(f.apple.authorizeDeleteDispatch({ route: 'approved_delete', ...peer }), false)
  for (const action of ['apple_begin_approved_delete', 'apple_reset_approved_delete', 'commit_import_category_delete'])
    assert.throws(() => f.apple.approvedDeleteOwner({ route: 'approved_delete_owner', ...peer, action, args: { operation_id: plan.operation_id } }), /不属于/)
  const finish = { route: 'approved_delete_owner', ...peer, action: 'apple_finish_approved_delete', args: { operation_id: plan.operation_id } }
  assert.throws(() => f.apple.approvedDeleteOwner({ ...finish, args: { ...finish.args, document: {} } }), /不属于/)
  await f.apple.approvedDeleteOwner(finish)
  assert.equal(f.state.privateActions.at(-1).action, 'apple_finish_approved_delete')
  assert.equal(await f.apple.continueDelete({ route: 'approved_delete_continue', phase: 'end', ...peer }), true)
  assert.throws(() => f.apple.approvedDeleteOwner(finish), /不属于/)
  assert.equal(f.state.asks.length, 1); assert.equal(f.state.nativeSends, 1)
})

test('import unlink accepts only the stored enabled flag, never truthy strings', async t => {
  for (const enabled of [false, 0, '1', 2]) {
    const f = await fixture(t, 'native', 'import'); f.state.linkEnabled = enabled
    const result = await f.call('tasks', 'delete_category', { category_id: 'synthetic-id' })
    assert.equal(result.isError, true); assert.equal(f.state.asks.length, 0)
    assert.equal(f.state.ownerWrites, 0); assert.equal(f.state.nativeSends, 0)
  }
})
