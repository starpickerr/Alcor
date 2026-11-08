import test from 'node:test'
import assert from 'node:assert/strict'
import { Context } from '@deepseek-ai/cordis'
import { bindScopeParent, createScope, scopeTarget } from '@deepseek-ai/dsh-scope'
import Sessions from '@deepseek-ai/dsh-session'
import Projections from '@deepseek-ai/dsh-session-projection'
import Agents from '@deepseek-ai/dsh-agent'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Tools, { defineTool } from '@deepseek-ai/dsh-tools'
import { ToolExecutionPipeline, executeToolBatch } from '@deepseek-ai/dsh-tools/pipeline'
import Approval, { setApprovalPolicy } from '@deepseek-ai/dsh-user-approval'
import { apply as applySecretaryTools } from '../../packages/alcor-secretary/src/tools.js'
import { createAppleExportGate, isAppleExport } from '../../packages/alcor-secretary/src/security-apple-export-gate.js'

const inputFor = (kind = 'calendar', method = 'one_time') => ({ domain: 'apple', method, arguments: method === 'one_time'
  ? { kind, direction: 'export', [kind === 'calendar' ? 'calendar_id' : 'list_id']: 'destination', item_ids: ['local-item'], confirm: true }
  : { kind, direction: 'export', selections: [{ apple_id: 'destination', alcor_id: 'local-category' }], start: '2026-11-01', end: '2026-11-30', token: 'model-token', confirm: true, ...(kind === 'reminders' ? { include_undated: true, include_completed: false } : {}) } })

async function fixture(t, kind = 'calendar', method = 'one_time', action = 'create', production = false) {
  const ctx = new Context(); t.after(() => ctx.fiber.dispose())
  for (const plugin of [Sessions, Projections, Agents, SystemPrompt, Tools, Approval]) await ctx.plugin(plugin, {})
  const state = { actor: true, host: 'allow', answer: 'allowed-once', asks: [], events: [], reads: 0, writes: 0,
    beforeAnswer: async () => {}, beforeCommit: async () => {}, transformPlan: plan => plan, generation: 1, genericCalls: 0 }
  ctx.on('session/event', (_session, event) => state.events.push(event))
  const session = ctx.sessions.create('synthetic-export-session')
  session.append('turn/start', { turn: 1 })
  const agent = { ctx, session }, signal = new AbortController()
  const trustedTurnOf = execution => {
    const boundary = ctx.agents.turnBoundaryOf(execution.agent.session)
    return boundary.openTurnStartSeq === null ? undefined : boundary.lastTurn
  }
  const gateOptions = {
    getApproval: () => state.provider === undefined ? ctx.approval : state.provider,
    trustedTurnOf,
    actorStillAuthorized: execution => state.actor && ctx.tools.executable(execution.name, execution.agent, execution.toolSet) !== undefined && ctx.tools.checkGuards(execution) === undefined,
    prepare: async (request, authorize) => {
      assert.equal(authorize(), true); state.reads++; state.request = request
      assert.equal(Object.hasOwn(request.arguments, 'confirm'), false); assert.equal(Object.hasOwn(request.arguments, 'token'), false)
      state.proposal = state.transformPlan({ version: 1, operation_id: request.operation_id, kind: request.arguments.kind,
        mode: request.method === 'one_time' ? 'one_time' : 'batch', method: request.method, arguments: request.arguments,
        links_snapshot: [], skipped: method === 'transfer_batch' ? 2 : 0, plan_fingerprint: 'a'.repeat(64), items: [{
          local_id: 'local-item', source_id: 'source-account', group_id: 'destination', action,
          wire_payload: { title: 'Synthetic item', notes: 'Full notes\nwith second line', calendar_id: 'destination', recurrence: { frequency: 'daily' } },
          owner_snapshot: { generation: state.generation, item: { id: 'local-item' } }, destination_snapshot: { id: 'destination', title: 'Synthetic destination', source_id: 'source-account' },
          mapping_snapshot: action === 'update' ? { native_id: 'existing-native-item', generation: 1 } : null,
          native_snapshot: action === 'update' ? { title: 'Previous title', generation: 1 } : null,
          baseline_snapshot: action === 'update' ? { title: 'Previous title' } : null,
        }] })
      return state.proposal
    },
    commit: async (plan, authorize) => {
      state.committingPlan = plan; state.commitAuthorize = authorize
      await state.beforeCommit()
      if (!authorize()) throw new Error('revoked before write')
      if (plan.items.some(item => item.owner_snapshot.generation !== state.generation)) throw new Error('owner generation changed')
      assert.equal(Object.isFrozen(plan), true)
      for (const item of plan.items) { assert.equal(Object.isFrozen(item), true); assert.equal(Object.isFrozen(item.wire_payload.recurrence), true) }
      state.writes += plan.items.length
      return { exported: plan.items.filter(item => item.action === 'create').length, updated: plan.items.filter(item => item.action === 'update').length, skipped: plan.skipped }
    },
  }
  const gate = createAppleExportGate(gateOptions)
  if (production) {
    ctx.provide('alcorSecretary', {})
    ctx.provide('alcorOrganizer', { prepareApprovedExport: gateOptions.prepare, commitApprovedExport: gateOptions.commit, business: async () => { state.genericCalls++; throw new Error('Export bypass reached generic business') } })
    const preset = {}
    bindScopeParent(agent, preset)
    await ctx.plugin(Object.assign(inner => applySecretaryTools(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'alcorSecretary', 'alcorOrganizer'] }))
  } else {
    ctx.tools.register(defineTool({ name: 'alcor_business', description: 'Synthetic business owner',
      parameters: { domain: { type: 'string', required: true }, method: { type: 'string', required: true }, arguments: { type: 'object', additionalProperties: true }, requestId: { type: 'string' } },
      output: { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
      execute: (_args, execution) => gate.execute(execution) }))
    ctx.tools.guard(gate.guard)
    ctx.on('tools/pre-execute', (execution, next) => gate.preExecute(execution, next), { prepend: true })
    ctx.on('tools/result', execution => gate.release(execution))
  }
  ctx.on('tools/pre-execute', (execution, next) => { state.execution = execution; return next() }, { prepend: true })
  ctx.on('tools/pre-execute', (_execution, next) => state.host === 'allow' ? next()
    : state.host === 'ask' ? { kind: 'ask', reason: 'Host reason', displayReason: { en: 'Host English', zh: '宿主说明' } }
      : { kind: state.host, reason: 'Host refusal' })
  ctx.on('approval/request', async (request, next) => {
    state.asks.push(request)
    await state.beforeAnswer(request)
    if (state.answer === 'missing') return next()
    if (state.answer === 'throw') throw new Error('answerer unavailable')
    return state.answer
  })
  const pipeline = new ToolExecutionPipeline(ctx)
  const call = async (input = inputFor(kind, method), callId = 'synthetic-export-call') =>
    (await executeToolBatch({ agent, signal: signal.signal, calls: [{ type: 'tool-call', id: callId, name: 'alcor_business', arguments: JSON.stringify(input) }] }, pipeline, () => 1)).results[0].result
  return { ctx, state, agent, session, signal, gate, call, pipeline, kind, method }
}

for (const kind of ['calendar', 'reminders']) {
  for (const [method, action] of [['one_time', 'create'], ['one_time', 'update'], ['transfer_batch', 'create']]) {
    test(`${kind} ${method} ${action}: real SDK open-turn approval displays exact frozen target and full wire payload`, async t => {
      const f = await fixture(t, kind, method, action); f.state.host = 'ask'
      const result = await f.call()
      assert.equal(result.isError, false, JSON.stringify(result)); assert.equal(f.state.writes, 1); assert.equal(f.state.asks.length, 1)
      const request = f.state.asks[0]
      for (const text of ['Host reason', 'source-account', 'destination', 'Synthetic destination', 'Full notes\\nwith second line', 'daily', action === 'create' ? '新增' : '更新']) assert.ok(request.reason.includes(text), text)
      if (action === 'update') assert.match(request.reason, /existing-native-item/)
      assert.match(request.displayReason.en, /Host English/); assert.match(request.displayReason.zh, /宿主说明/)
      const audits = f.state.events.filter(event => event.type.startsWith('approval/'))
      assert.equal(audits.length, 2); assert.equal(audits[0].type, 'approval/asked'); assert.equal(audits[1].data.outcome, 'allowed-once')
      assert.equal(audits[0].data.id, audits[1].data.id); assert.match(JSON.stringify(result), /"skipped"/)
      await assert.rejects(() => f.gate.execute(f.state.execution), /有效批准/)
      assert.equal(f.state.commitAuthorize(), false)
    })
  }
}

test('only allowed-once is a grant; no answerer, bad answers, missing provider and idle turn fail closed', async t => {
  for (const answer of ['rejected', 'cancelled', 'unavailable', 'missing', 'throw', 'allowed', true]) {
    const f = await fixture(t); f.state.answer = answer
    assert.equal((await f.call()).isError, true, String(answer)); assert.equal(f.state.writes, 0)
  }
  const missing = await fixture(t); missing.state.provider = null
  assert.equal((await missing.call()).isError, true); assert.equal(missing.state.reads, 0)
  const idle = await fixture(t); idle.session.append('turn/end', { turn: 1, reason: 'completed' })
  assert.equal((await idle.call()).isError, true); assert.equal(idle.state.reads, 0); assert.equal(idle.state.asks.length, 0)
})

test('host denial, cancellation, guard and pre-existing never policy block preparation', async t => {
  for (const host of ['deny', 'cancel']) {
    const f = await fixture(t); f.state.host = host
    assert.equal((await f.call()).isError, true); assert.equal(f.state.reads, 0); assert.equal(f.state.asks.length, 0)
  }
  for (const revoke of [f => { f.state.actor = false }, f => f.ctx.tools.guard(() => 'Revoked'), f => setApprovalPolicy(f.session, 'never'), f => { f.ctx.approval.config.policy = 'never' }]) {
    const f = await fixture(t); revoke(f)
    assert.equal((await f.call()).isError, true); assert.equal(f.state.reads, 0)
  }
})

test('live authorization is rechecked during approval and immediately before owner write', async t => {
  const revocations = [
    f => { f.state.actor = false },
    f => f.ctx.tools.guard(() => 'Revoked guard'),
    f => setApprovalPolicy(f.session, 'never'),
    f => { f.ctx.approval.config.policy = 'never' },
    f => { f.state.provider = null },
    f => { f.state.provider = { request: async () => 'allowed-once', overrideOf: () => undefined } },
    f => f.signal.abort(),
    f => { f.session.append('turn/end', { turn: 1, reason: 'completed' }); f.session.append('turn/start', { turn: 2 }) },
    f => { f.agent.session = f.ctx.sessions.create('other-session'); f.agent.session.append('turn/start', { turn: 1 }) },
    f => { f.state.execution.callId = 'changed-call' },
    f => { f.state.execution.name = 'another-tool' },
    f => { f.state.execution.toolSet = {} },
    f => { f.state.execution.signal = new AbortController().signal },
    f => { f.state.execution.agent = { ...f.agent } },
    f => { f.state.execution.arguments = inputFor('reminders') },
  ]
  for (const phase of ['beforeAnswer', 'beforeCommit']) for (const revoke of revocations) {
    const f = await fixture(t); f.state[phase] = async () => revoke(f)
    assert.equal((await f.call()).isError, true, `${phase}: ${revoke}`); assert.equal(f.state.writes, 0)
  }
})

test('public overrideOf wins over configured policy without consulting a private method', async t => {
  const f = await fixture(t)
  f.ctx.approval.config.policy = 'never'; setApprovalPolicy(f.session, 'ask')
  assert.equal((await f.call()).isError, false); assert.equal(f.state.writes, 1)
})

test('frozen private copy is shown and committed despite mutation of returned proposal; owner generations still bind', async t => {
  const f = await fixture(t)
  f.state.beforeAnswer = async () => { f.state.proposal.items[0].wire_payload.title = 'Injected after display'; f.state.proposal.items[0].destination_snapshot.id = 'different-destination' }
  assert.equal((await f.call()).isError, false)
  assert.equal(f.state.committingPlan.items[0].wire_payload.title, 'Synthetic item'); assert.equal(f.state.committingPlan.items[0].destination_snapshot.id, 'destination')
  const changed = await fixture(t); changed.state.beforeAnswer = async () => { changed.state.generation++ }
  assert.equal((await changed.call()).isError, true); assert.equal(changed.state.writes, 0)
})

test('same call cannot concurrently request approval and a grant never transfers to a cloned execution', async t => {
  const f = await fixture(t); let ready, resume
  const entered = new Promise(resolve => { ready = resolve }), waiting = new Promise(resolve => { resume = resolve })
  f.state.beforeAnswer = async () => { ready(); await waiting }
  const first = f.call(); await entered
  const approvedExecution = f.state.execution
  assert.equal((await f.call()).isError, true); assert.equal(f.state.asks.length, 1)
  await assert.rejects(() => f.gate.execute({ ...approvedExecution }), /有效批准/)
  resume(); assert.equal((await first).isError, false); assert.equal(f.state.writes, 1)
})

test('host wrappers cannot turn denial or omitted approval into an export', async t => {
  for (const bypass of ['overwrite', 'skip']) {
    const f = await fixture(t); f.state.answer = 'rejected'
    f.ctx.on('tools/pre-execute', async (_execution, next) => { if (bypass === 'overwrite') await next(); return { kind: 'allow' } }, { prepend: true })
    assert.equal((await f.call()).isError, true); assert.equal(f.state.writes, 0)
  }
  const f = await fixture(t)
  const dispose = f.ctx.on('tools/pre-execute', async (_execution, next) => { await next(); return { kind: 'deny', reason: 'Late host refusal' } }, { prepend: true })
  assert.equal((await f.call()).isError, true); assert.equal(f.state.writes, 0)
  dispose(); assert.equal((await f.call()).isError, false); assert.equal(f.state.asks.length, 2)
})

test('unknown model fields and nested authorization injection are rejected before preparation', async t => {
  for (const kind of ['calendar', 'reminders']) for (const method of ['one_time', 'transfer_batch']) {
    for (const field of ['operation_id', 'approval', 'approval_key', 'plan', 'wire_payload', 'generation', 'native_snapshot']) {
      const f = await fixture(t, kind, method), input = inputFor(kind, method); input.arguments[field] = 'forged'
      assert.equal((await f.call(input)).isError, true, field); assert.equal(f.state.reads, 0)
    }
    if (method === 'transfer_batch') {
      const f = await fixture(t, kind, method), input = inputFor(kind, method); input.arguments.selections[0].approval_key = 'forged'
      assert.equal((await f.call(input)).isError, true); assert.equal(f.state.reads, 0)
    }
  }
})

test('malformed, lossy, oversized and batch-update plans never reach the approval channel', async t => {
  for (const transform of [
    plan => ({ ...plan, operation_id: 'forged' }), plan => ({ ...plan, kind: 'unsupported' }), plan => ({ ...plan, plan_fingerprint: 'forged' }),
    plan => ({ ...plan, skipped: -1 }),
    plan => { plan.items[0].owner_snapshot = null; return plan },
    plan => { plan.items[0].wire_payload = new Map([['hidden', 'content']]); return plan },
    plan => { plan.items[0].wire_payload.notes = 'x'.repeat(120001); return plan },
    plan => { plan.items = Array.from({ length: 501 }, (_, i) => ({ ...plan.items[0], local_id: `item-${i}` })); return plan },
    plan => { plan.items.push(structuredClone(plan.items[0])); return plan },
  ]) {
    const f = await fixture(t); f.state.transformPlan = transform
    assert.equal((await f.call()).isError, true); assert.equal(f.state.asks.length, 0); assert.equal(f.state.writes, 0)
  }
  const batch = await fixture(t, 'reminders', 'transfer_batch', 'update')
  assert.equal((await batch.call()).isError, true); assert.equal(batch.state.asks.length, 0)
})

test('expired grant and aborted unanswered approval cannot write or be replayed', async t => {
  const f = await fixture(t), now = Date.now
  f.state.beforeCommit = async () => { Date.now = () => now() + 5 * 60 * 1000 }
  try { assert.equal((await f.call()).isError, true); assert.equal(f.state.writes, 0) } finally { Date.now = now }
  const waiting = await fixture(t); let ready
  const entered = new Promise(resolve => { ready = resolve })
  waiting.state.beforeAnswer = () => { ready(); return new Promise(() => {}) }
  const pending = waiting.call(); await entered; waiting.signal.abort()
  assert.equal((await pending).isError, true); assert.equal(waiting.state.writes, 0)
})

test('an all-mapped batch returns only its skipped count and requires a fresh single-use decision', async t => {
  const f = await fixture(t, 'reminders', 'transfer_batch')
  f.state.transformPlan = plan => ({ ...plan, items: [], skipped: 3 })
  assert.equal((await f.call()).isError, false); assert.equal(f.state.writes, 0)
  assert.match(f.state.asks[0].reason, /导出 0 项，跳过 3 项/)
  assert.equal(f.state.committingPlan.items.length, 0)
  await assert.rejects(() => f.gate.execute(f.state.execution), /有效批准/)
})

test('classification keeps both domains while import and unrelated tools pass through', async () => {
  for (const kind of ['calendar', 'reminders']) for (const method of ['one_time', 'transfer_batch']) assert.equal(isAppleExport({ name: 'alcor_business', arguments: inputFor(kind, method) }), true)
  const gate = createAppleExportGate({})
  for (const execution of [{ name: 'other', arguments: inputFor() }, { name: 'alcor_business', arguments: { ...inputFor(), arguments: { direction: 'import' } } }]) {
    let calls = 0
    assert.deepEqual(await gate.preExecute(execution, async () => { calls++; return { kind: 'allow' } }), { kind: 'allow' })
    assert.equal(calls, 1); assert.equal(gate.guard(execution), undefined)
  }
})

const preStep = (f, kinds = ['user']) => f.ctx.waterfall(scopeTarget(f.agent, f.agent), 'agent/pre-step',
  { agent: f.agent, messages: kinds.map(kind => ({ source: { kind } })) }, async () => {})
const nextTurn = f => {
  const turn = f.ctx.agents.turnBoundaryOf(f.agent.session).lastTurn
  f.agent.session.append('turn/end', { turn, reason: 'completed' })
  f.agent.session.append('turn/start', { turn: turn + 1 })
}

for (const kind of ['calendar', 'reminders']) for (const method of ['one_time', 'transfer_batch']) {
  test(`registered tools.js ${kind} ${method}: current user provenance reaches native approval and private owner only`, async t => {
    const f = await fixture(t, kind, method, 'create', true)
    assert.equal((await f.call()).isError, true); assert.equal(f.state.reads, 0)
    await preStep(f, ['schedule'])
    assert.equal((await f.call()).isError, true); assert.equal(f.state.reads, 0)
    nextTurn(f); await preStep(f)
    f.state.answer = 'rejected'
    assert.equal((await f.call()).isError, true); assert.equal(f.state.writes, 0); assert.equal(f.state.asks.length, 1)
    f.state.answer = 'allowed-once'
    const result = await f.call()
    assert.equal(result.isError, false, JSON.stringify(result)); assert.equal(f.state.writes, 1); assert.equal(f.state.asks.length, 2)
    assert.equal(f.state.genericCalls, 0)
    const body = f.ctx.tools.executable('alcor_business', f.agent)
    await assert.rejects(() => body.execute(inputFor(kind, method), { ...f.state.execution }), /有效批准/)
    assert.equal(f.state.writes, 1)
  })
}

test('registered tools.js does not inherit user provenance across turns or mixed-source steps', async t => {
  const f = await fixture(t, 'calendar', 'one_time', 'create', true)
  await preStep(f); await preStep(f, [])
  assert.equal((await f.call()).isError, false)
  await preStep(f, ['schedule'])
  assert.equal((await f.call()).isError, true)
  nextTurn(f); await preStep(f, [])
  assert.equal((await f.call()).isError, true)
  await preStep(f, ['user', 'schedule'])
  assert.equal((await f.call()).isError, true)
  nextTurn(f); await preStep(f, ['user'])
  assert.equal((await f.call()).isError, false)
  assert.equal(f.state.writes, 2); assert.equal(f.state.asks.length, 2); assert.equal(f.state.genericCalls, 0)
})

test('registered tools.js refuses delegated sessions even with user-looking messages', async t => {
  for (const meta of [{ origin: 'subagent' }, { parentSession: 'parent-session' }]) {
    const f = await fixture(t, 'reminders', 'one_time', 'create', true)
    f.agent.session = f.ctx.sessions.create('delegated-session', { meta })
    f.agent.session.append('turn/start', { turn: 1 }); await preStep(f)
    assert.equal((await f.call()).isError, true); assert.equal(f.state.reads, 0); assert.equal(f.state.asks.length, 0); assert.equal(f.state.genericCalls, 0)
  }
})

test('registered production guard remains monotonic when a host wrapper skips or overwrites approval', async t => {
  for (const bypass of ['skip', 'overwrite']) {
    const f = await fixture(t, 'calendar', 'one_time', 'create', true)
    await preStep(f); f.state.answer = 'rejected'
    f.ctx.on('tools/pre-execute', async (_execution, next) => { if (bypass === 'overwrite') await next(); return { kind: 'allow' } }, { prepend: true })
    const result = await f.call()
    assert.equal(result.isError, true); assert.equal(f.state.writes, 0); assert.equal(f.state.genericCalls, 0)
    assert.match(JSON.stringify(result), /具体目标审批/)
  }
})

test('registered production authorization revokes on provenance, policy, external guard and turn changes', async t => {
  for (const phase of ['beforeAnswer', 'beforeCommit']) for (const revoke of [
    f => preStep(f, ['schedule']),
    f => setApprovalPolicy(f.agent.session, 'never'),
    f => f.ctx.tools.guard(() => 'Host revoked this export'),
    f => nextTurn(f),
  ]) {
    const f = await fixture(t, 'reminders', 'transfer_batch', 'create', true)
    await preStep(f); f.state[phase] = async () => { await revoke(f) }
    assert.equal((await f.call()).isError, true, `${phase}: ${revoke}`); assert.equal(f.state.writes, 0); assert.equal(f.state.genericCalls, 0)
  }
})

test('registered tools reject missing conflict targets, unsupported category choices and broad deletion flags', async t => {
  const f = await fixture(t, 'calendar', 'one_time', 'create', true); await preStep(f)
  for (const method of ['resolve_conflict', 'resolve_category_conflict']) for (const choice of ['keep_alcor', 'keep_apple']) {
    assert.equal((await f.call({ domain: 'apple', method, arguments: { choice } })).isError, true)
  }
  for (const method of ['sync', 'start_sync']) for (const field of ['confirm_deletions', 'confirm_local_deletions']) {
    assert.equal((await f.call({ domain: 'apple', method, arguments: { [field]: true } })).isError, true)
  }
  assert.equal(f.state.reads, 0); assert.equal(f.state.asks.length, 0); assert.equal(f.state.genericCalls, 0)
})
