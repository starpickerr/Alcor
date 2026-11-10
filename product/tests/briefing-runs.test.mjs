import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'
import { BriefingGenerator } from '../../packages/alcor-organizer/src/briefing.js'
import { buildBriefing } from '../../packages/alcor-organizer/src/records.js'
import { schemas } from '../../packages/alcor-organizer/src/remote.js'
import { snapshotJsonValue } from '@deepseek-ai/dsh-util-values'

const source = { source: 'elearning', accountId: 'school', tool: 'mcp__elearning__fetch', method: 'fetch', arguments: { course_ids: ['selected'] } }
const options = { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) }
const route = { provider: 'synthetic', model: 'chosen-model', reasoningEffort: 'high' }

async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), 'stella-briefing-run-'))
  const workers = []
  const worker = () => { const value = new BusinessWorker(directory, options); workers.push(value); return value }
  t.after(async () => { for (const value of workers) await value.close(); await rm(directory, { recursive: true, force: true }) })
  return { worker: worker(), restart: worker }
}
function nativeContext() {
  const agents = new Map(), requests = [], creations = []
  let readCount = 0
  let respond = async request => structured(request)
  const ctx = {
    sessionController: {
      create: async request => {
        creations.push(request)
        const id = 'run-' + randomUUID(), definitions = new Map(), listeners = new Set()
        let model = { provider: 'synthetic', model: 'default-model' }, activity = Promise.resolve(), controller
        const agent = { session: { id, header: {}, preset: request.agentPreset }, model: { current: () => model, select: value => { model = value } },
          ctx: { tools: { restrict: () => () => {}, register: tool => { definitions.set(tool.name, tool); return () => definitions.delete(tool.name) } },
            on: (event, listener) => { if (event === 'agent/pre-step') return () => {}; assert.equal(event, 'tools/result'); listeners.add(listener); return () => listeners.delete(listener) } },
          followup: message => {
            controller = new AbortController()
            const request = { parent: agent, agentPreset: agent.session.preset, agentOptions: model,
              outputSchema: definitions.get('briefing_submit').parameters, prompt: message.content }
            requests.push(request)
            activity = (async () => {
              const value = await respond(request, controller.signal)
              if (value?.stopReason !== 'completed' || !value.structured) return
              const exec = { agent, signal: controller.signal, concludeTurn() {} }
              await definitions.get('briefing_submit').execute(value.structured, exec)
              for (const listener of listeners) listener(exec, { isError: false })
            })()
          },
          whenIdle: () => activity, cancel: () => controller?.abort(new Error('cancelled')),
        }
        agents.set(id, agent); return { sessionId: id }
      },
      rename: async () => {}, resolveAgent: async id => ({ agent: agents.get(id) }),
    },
    agentPresets: { presetForSession: session => session.preset },
    tools: { execute: async ({ calls: [call] }) => {
      readCount++; const args = JSON.parse(call.arguments); assert.deepEqual(args.course_ids, ['selected'])
      return { results: [{ result: { value: { structuredContent: { value: { source_items: [{ source_id: 'one', title: 'Synthetic task', snippet: 'secret-payload', payload: { resource_type: 'assignment' }, occurred_at: new Date(Date.now() - 1000).toISOString() }], cursors: { checkpoint: 'source-state' }, next_offset: null } } } } }] }
    } },
  }
  return { ctx, agents, creations, requests, reads: () => readCount, response: handler => { respond = handler } }
}
function structured(request) {
  const text = request.prompt[0].text
  const rows = JSON.parse(text.split('可阅读的原文数据：\n')[1])
  const decisions = rows.map(row => ({ id: row.id, selected: true, reason: 'Needed', section: 'action', score: 90, summary: row.snippet }))
  if (request.outputSchema.properties.decisions && !request.outputSchema.properties.reports) return { stopReason: 'completed', structured: { decisions } }
  const ids = request.outputSchema.properties.decisions ? rows.map(row => row.id) : request.outputSchema.properties.source_item_ids.items.enum
  return { stopReason: 'completed', structured: { ...(request.outputSchema.properties.decisions ? { decisions } : {}), body: '已完成独立简报。', source_item_ids: ids, reports: ids.map(id => ({ id, content: '具体报告' })) } }
}
const input = () => { const now = new Date().toISOString(); return { requestId: randomUUID(), record: buildBriefing([], now.slice(0, 10), '', now), sources: [source], parent: { session: { id: 'chat-owner', header: { cwd: '/tmp' } }, model: { current: () => route } } } }
const state = (worker, id) => worker.call('briefing_run', { method: 'get', request_id: id })
async function until(check) { for (let i = 0; i < 400; i++) { const value = await check(); if (value) return value; await new Promise(resolve => setTimeout(resolve, 10)) } throw new Error('Run did not settle') }

test('public run output is lossless JSON across optional fields and phases', () => {
  const generator = new BriefingGenerator({}, {})
  const common = { requestId: randomUUID(), date: '2026-11-07', message: '状态', createdAt: '2026-11-07T00:00:00Z', updatedAt: '2026-11-07T00:00:00Z', collected: [], submissions: [] }
  for (const [phase, optional] of [
    ['starting', {}], ['collecting', { sessionId: 'session' }],
    ['model', { sessionId: 'session', model: { provider: 'synthetic', model: 'demo' } }],
    ['failed', { sessionId: 'session', model: { provider: 'synthetic', model: 'demo', reasoningEffort: 'high' } }],
    ['completed', { sessionId: 'session', model: { provider: 'synthetic', model: 'demo' }, briefingId: 'briefing' }],
  ]) {
    const value = generator.publicRun({ ...common, phase, ...optional })
    assert.deepEqual(snapshotJsonValue(value), value, phase)
    assert.deepEqual(Object.fromEntries(Object.keys(optional).map(key => [key, value[key]])), optional)
    for (const key of ['sessionId', 'model', 'briefingId']) if (!(key in optional)) assert.equal(Object.hasOwn(value, key), false)
    schemas.startBriefing[1].parse(value)
  }
})

test('briefing owns its root, freezes model and rules, and returns before model completion', async t => {
  const { worker } = await fixture(t), native = nativeContext()
  const generator = new BriefingGenerator(native.ctx, worker); t.after(() => generator.close())
  let release
  native.response(async request => { if (!release) await new Promise(resolve => { release = resolve }); return structured(request) })
  const request = input()
  const started = await generator.start(request)
  schemas.startBriefing[1].parse(started)
  assert.deepEqual(snapshotJsonValue(started), started)
  assert.notEqual(started.sessionId, 'chat-owner')
  assert.deepEqual(native.creations, [{ agentPreset: 'alcor-briefing', cwd: '/tmp' }])
  await until(() => release)
  request.parent.model.current = () => ({ provider: 'changed', model: 'other-model' })
  await worker.call('invoke', { domain: 'briefing', method: 'create_rule', arguments: { payload: { text: 'NEW RULE MUST NOT ENTER THIS RUN', source: '*', enabled: true } } })
  const status = await generator.status()
  assert.deepEqual(snapshotJsonValue(status), status)
  assert(!JSON.stringify(status).includes('secret-payload'))
  assert(!('sources' in status)); assert(!('submissions' in status))
  release()
  const result = await generator.wait(request.requestId)
  assert.equal(result.body, '已完成独立简报。')
  assert.equal(native.requests.length, 1)
  for (const call of native.requests) {
    assert.equal(call.parent.session.id, started.sessionId)
    assert.equal(call.agentPreset, 'alcor-briefing')
    assert.deepEqual(call.agentOptions, route)
    assert(!call.prompt[0].text.includes('NEW RULE MUST NOT ENTER THIS RUN'))
  }
  const saved = await state(worker, request.requestId)
  assert.equal(saved.completedBatches, 1); assert.equal(saved.completedSources, 1)
  assert.deepEqual(saved.collected, []); assert.deepEqual(saved.submissions, [])
  assert.equal((await generator.generate(request)).id, result.id)
  const completed = await generator.start(request)
  assert.equal(completed.phase, 'completed')
  assert.equal(completed.briefingId, result.business.briefingId)
  assert.deepEqual(snapshotJsonValue(completed), completed)
  assert.equal(native.requests.length, 1); assert.equal(native.reads(), 1)
  assert.equal(native.creations.length, 1)
})

test('failed global runs resume the same session after worker restart and require explicit retry', async t => {
  const fixtureData = await fixture(t), native = nativeContext(), request = input()
  let worker = fixtureData.worker
  let generator = new BriefingGenerator(native.ctx, worker)
  native.response(async () => ({ stopReason: 'failed' }))
  await assert.rejects(generator.generate(request), /模型未提交/)
  assert.equal((await state(worker, request.requestId)).submissions.length, 0)
  assert.equal(await worker.call('invoke', { domain: 'briefing', method: 'get_cursor', arguments: { source: 'elearning', account_id: 'school', cursor_key: 'until' } }), null)
  await generator.close(); await worker.close()
  worker = fixtureData.restart(); generator = new BriefingGenerator(native.ctx, worker); t.after(() => generator.close())
  assert.equal((await generator.status()).phase, 'failed')
  const before = native.requests.length
  const failed = await generator.start(request)
  assert.equal(failed.phase, 'failed')
  assert.deepEqual(snapshotJsonValue(failed), failed)
  assert.equal(native.requests.length, before)
  await assert.rejects(generator.resume(request.requestId, async () => { throw new Error('授权已撤销') }), /授权已撤销/)
  assert.equal(native.requests.length, before)
  await assert.rejects(generator.start({ ...request, sources: [{ ...source, accountId: 'unselected' }] }), /不同简报或来源/)
  native.response(async request => structured(request))
  const resumed = await generator.resume(request.requestId, async () => {})
  assert.deepEqual(snapshotJsonValue(resumed), resumed)
  assert.equal(resumed.sessionId, native.requests[0].parent.session.id)
  const result = await generator.wait(request.requestId)
  assert.equal(native.requests.length, before + 1)
  assert.equal(native.reads(), 1)
  assert.deepEqual(native.requests.at(-1).agentOptions, route)
  assert.equal(result.business.snapshot.revisions.length, 1)
})

test('hard host loss is marked interrupted and resumes its unfinished global request', async t => {
  const f = await fixture(t), native = nativeContext(), request = input()
  let generator = new BriefingGenerator(native.ctx, f.worker)
  native.response(async (request, signal) => {
    return new Promise(resolve => signal.addEventListener('abort', () => resolve({ stopReason: 'cancelled' }), { once: true }))
  })
  await generator.start(request)
  await until(async () => native.requests.length === 1)
  f.worker.process.kill('SIGKILL')
  await new Promise(resolve => f.worker.process.once('exit', resolve))
  await generator.close()
  const worker = f.restart()
  generator = new BriefingGenerator(native.ctx, worker); t.after(() => generator.close())
  assert.equal((await generator.status()).phase, 'interrupted')
  assert.equal(native.requests.length, 1)
  native.response(async request => structured(request))
  await generator.resume(request.requestId)
  await generator.wait(request.requestId)
  assert.equal(native.requests.length, 2); assert.equal(native.reads(), 1)
})

test('malformed model results never become checkpoints, and revoked authorization prevents commit', async t => {
  const { worker } = await fixture(t), native = nativeContext(), request = input()
  const generator = new BriefingGenerator(native.ctx, worker); t.after(() => generator.close())
  native.response(async () => ({ stopReason: 'completed', structured: { decisions: [] } }))
  await assert.rejects(generator.generate(request), /不能遗漏/)
  assert.equal((await state(worker, request.requestId)).submissions.length, 0)
  let allowed = true
  native.response(async request => { allowed = false; return structured(request) })
  await generator.resume(request.requestId, async () => { if (!allowed) throw new Error('已撤销') })
  await assert.rejects(generator.wait(request.requestId), /已撤销/)
  assert.equal((await state(worker, request.requestId)).submissions.length, 0)
  assert.equal((await worker.call('invoke', { domain: 'briefing', method: 'list_briefings' })).length, 0)
})

test('a stale interrupted run cannot overwrite a newer report or rewind source cursors', async t => {
  const { worker } = await fixture(t), native = nativeContext(), old = input()
  const generator = new BriefingGenerator(native.ctx, worker); t.after(() => generator.close())
  native.response(async () => ({ stopReason: 'failed' }))
  await assert.rejects(generator.generate(old), /模型未提交/)
  native.response(async request => structured(request))
  await generator.generate(input())
  const count = native.requests.length
  await assert.rejects(generator.resume(old.requestId), /已有更新/)
  assert.equal(native.requests.length, count)
})

test('cancellation during session setup waits for durable interruption and never starts a model', async t => {
  const { worker } = await fixture(t), native = nativeContext(), request = input()
  const create = native.ctx.sessionController.create
  let release
  native.ctx.sessionController.create = async input => { await new Promise(resolve => { release = resolve }); return create(input) }
  const generator = new BriefingGenerator(native.ctx, worker)
  const starting = generator.start(request)
  const rejected = assert.rejects(starting, /中断/)
  await until(() => release)
  const closing = generator.close()
  release()
  await closing; await rejected
  assert.equal((await generator.status()).phase, 'interrupted')
  assert.equal(native.requests.length, 0)
})

test('startup reconciles committed results without replaying a model or changing the saved revision', async t => {
  const f = await fixture(t), native = nativeContext(), request = input()
  const generator = new BriefingGenerator(native.ctx, f.worker)
  const result = await generator.generate(request)
  await generator.close()
  await f.worker.call('briefing_run', { method: 'save', request_id: request.requestId, patch: { phase: 'model' } })
  await f.worker.close()
  const worker = f.restart(), restarted = new BriefingGenerator(native.ctx, worker)
  t.after(() => restarted.close())
  const recovered = await restarted.status()
  assert.equal(recovered.phase, 'completed')
  assert.equal(recovered.completedBatches, 1)
  assert.equal((await restarted.generate(request)).id, result.id)
  assert.equal(native.requests.length, 1)
})

test('session setup persistence failures release the run instead of leaving shutdown waiting forever', async t => {
  const { worker } = await fixture(t), native = nativeContext()
  const originalCall = worker.call.bind(worker)
  worker.call = (action, args) => action === 'briefing_run' && args.method === 'save' && args.patch.phase === 'failed'
    ? Promise.reject(new Error('Synthetic write failure')) : originalCall(action, args)
  native.ctx.sessionController.create = async () => { throw new Error('Synthetic setup failure') }
  const generator = new BriefingGenerator(native.ctx, worker)
  await assert.rejects(generator.start(input()), /Synthetic write failure/)
  assert.equal(generator.current, undefined)
  await generator.close()
})

function directAgent(run) {
  const tools = new Map(), events = new Map(), controller = new AbortController()
  let activity = Promise.resolve()
  const agent = {
    session: { id: 'same-session' }, model: { select() {} },
    ctx: {
      tools: { restrict: () => () => {}, register: tool => { tools.set(tool.name, tool); return () => tools.delete(tool.name) } },
      on: (name, listener) => { events.set(name, listener); return () => events.delete(name) },
    },
    followup: message => { activity = run({ tools, events, agent, message, controller }) },
    whenIdle: () => activity, cancel: () => controller.abort(),
  }
  return agent
}
function execution(agent, parent) { return { agent, signal: new AbortController().signal, parent, token: Symbol(), concludeTurn() {} } }

test('large source JSON is read to its final character before submission in the same session', async () => {
  const body = '前'.repeat(26000) + '末尾重要事实'
  let reconstructed = '', submits = 0
  const agent = directAgent(async ({ tools, events, agent }) => {
    const submit = tools.get('briefing_submit'), read = tools.get('briefing_read')
    await assert.rejects(submit.execute({ decisions: [] }, execution(agent)), /完整读取/)
    let offset = 0
    while (offset !== null) {
      const exec = execution(agent), result = await read.execute({ index: 0, offset }, exec)
      events.get('tools/result')(exec, { isError: false })
      reconstructed += result.chunk; offset = result.next_offset
    }
    assert.equal(JSON.parse(reconstructed).body, body)
    const exec = execution(agent)
    await submit.execute({ decisions: [{ id: 'one' }] }, exec)
    events.get('tools/result')(exec, { isError: false }); submits++
  })
  const generator = new BriefingGenerator({}, {})
  const state = { requestId: 'r', run: { sessionId: 'same-session', model: route }, parent: agent, controller: new AbortController() }
  generator.current = state
  const result = await generator.runAgent(state, { entries: [{ id: 'one', body }], task: 'task', rules: 'rules', schema: { type: 'object' } }, async () => {}, async () => {})
  assert.deepEqual(result, { decisions: [{ id: 'one' }] }); assert.equal(submits, 1)
})

test('nested results become authoritative only after successful enclosing execution', async () => {
  const agent = directAgent(async ({ tools, events, agent }) => {
    const parent = Symbol(), child = execution(agent, parent)
    await tools.get('briefing_submit').execute({ answer: 'uncommitted' }, child)
    events.get('tools/result')(child, { isError: false })
    events.get('tools/result')({ token: parent }, { isError: true })
  })
  const generator = new BriefingGenerator({}, {}), state = { requestId: 'r', run: { sessionId: 'same-session', model: route }, parent: agent, controller: new AbortController() }
  generator.current = state
  await assert.rejects(generator.runAgent(state, { entries: [], task: '', rules: '', schema: { type: 'object' } }, async () => {}, async () => {}), /模型未提交/)
})

test('old unfinished protocol requires a new run without advancing cursors', async t => {
  const { worker } = await fixture(t), request = input()
  let run = await worker.call('briefing_run', { method: 'prepare', request_id: request.requestId, record: request.record, sources: request.sources, context: {} })
  await worker.call('briefing_run', { method: 'save', request_id: request.requestId, patch: { protocol: null, phase: 'interrupted', submissions: [{ decisions: [] }] } })
  await assert.rejects(worker.call('briefing_run', { method: 'prepare', request_id: request.requestId, record: request.record, sources: request.sources, context: {} }), /旧版简报任务无法继续/)
  assert.equal(await worker.call('invoke', { domain: 'briefing', method: 'get_cursor', arguments: { source: 'elearning', account_id: 'school', cursor_key: 'until' } }), null)
})

test('a changed model is refused at step admission before source data reaches a provider', async () => {
  let entered = false
  const agent = directAgent(async ({ events }) => {
    try { await events.get('agent/pre-step')({ modelSelection: { provider: 'other', model: 'other' } }, async () => { entered = true }) } catch {}
  })
  const generator = new BriefingGenerator({}, {}), state = { requestId: 'r', run: { sessionId: 'same-session', model: route }, parent: agent, controller: new AbortController() }
  generator.current = state
  await assert.rejects(generator.runAgent(state, { entries: [{ id: 'one' }], task: '', rules: '', schema: { type: 'object' } }, async () => {}, async () => {}), /模型已变化/)
  assert.equal(entered, false)
})

test('revocation during source paging prevents reading and submission', async () => {
  let authorized = true
  const agent = directAgent(async ({ tools, agent }) => {
    authorized = false
    await assert.rejects(tools.get('briefing_read').execute({ index: 0, offset: 0 }, execution(agent)), /已撤销/)
  })
  const generator = new BriefingGenerator({}, {}), state = { requestId: 'r', run: { sessionId: 'same-session', model: route }, parent: agent, controller: new AbortController() }
  generator.current = state
  const authorize = async () => { if (!authorized) throw new Error('已撤销') }
  await assert.rejects(generator.runAgent(state, { entries: [{ id: 'one', body: 'x'.repeat(25000) }], task: '', rules: '', schema: { type: 'object' } }, authorize, async () => {}), /已撤销/)
})

test('failed validation cannot submit truncated output or advance a run', async () => {
  let concluded = false
  const agent = directAgent(async ({ tools, events, agent }) => {
    const exec = execution(agent); exec.concludeTurn = () => { concluded = true }
    await assert.rejects(tools.get('briefing_submit').execute({ decisions: [] }, exec), /缺少/)
    assert.equal(concluded, false)
  })
  const generator = new BriefingGenerator({}, {}), state = { requestId: 'r', run: { sessionId: 'same-session', model: route }, parent: agent, controller: new AbortController() }
  generator.current = state
  await assert.rejects(generator.runAgent(state, { entries: [{ id: 'one' }], task: '', rules: '', schema: { type: 'object' } }, async () => {}, async () => { throw new Error('缺少 ID') }), /模型未提交/)
})

test('a late result cannot overwrite a newer briefing or advance its source cursor', async t => {
  const { worker } = await fixture(t), native = nativeContext(), request = input()
  const generator = new BriefingGenerator(native.ctx, worker); t.after(() => generator.close())
  let release
  native.response(async call => { await new Promise(resolve => { release = resolve }); return structured(call) })
  await generator.start(request)
  await until(() => release)
  const newerId = randomUUID()
  await worker.call('local_briefing', { record: { ...request.record, notes: 'Newer report' }, request_id: newerId })
  release()
  await assert.rejects(generator.wait(request.requestId), /已有更新/)
  assert.equal(await worker.call('invoke', { domain: 'briefing', method: 'get_cursor', arguments: { source: 'elearning', account_id: 'school', cursor_key: 'until' } }), null)
  assert.equal((await worker.call('briefing_run', { method: 'result', request_id: request.requestId })), null)
})

test('a durably accepted cancellation wins over a queued model commit', async t => {
  const { worker } = await fixture(t), native = nativeContext(), request = input()
  const call = worker.call.bind(worker); let release
  worker.call = async (action, args) => {
    if (action === 'generated_briefing' && args.submissions?.length && !args.validate_only) await new Promise(resolve => { release = resolve })
    return call(action, args)
  }
  const generator = new BriefingGenerator(native.ctx, worker); t.after(() => generator.close())
  await generator.start(request); await until(() => release)
  const stopping = generator.cancel(request.requestId)
  await until(async () => (await state(worker, request.requestId)).phase === 'cancelled')
  release(); await stopping
  assert.equal((await state(worker, request.requestId)).phase, 'cancelled')
  assert.equal(await worker.call('briefing_run', { method: 'result', request_id: request.requestId }), null)
  assert.equal(await worker.call('invoke', { domain: 'briefing', method: 'get_cursor', arguments: { source: 'elearning', account_id: 'school', cursor_key: 'until' } }), null)
})

test('legacy unfinished briefing resumes its existing session after restart', async t => {
  const data = await fixture(t), native = nativeContext(), request = input()
  let worker = data.worker, generator = new BriefingGenerator(native.ctx, worker)
  native.response(async () => ({ stopReason: 'failed' }))
  await assert.rejects(generator.generate(request), /模型未提交/)
  const previous = native.requests[0].parent.session
  previous.preset = 'stella-briefing'
  await generator.close(); await worker.close()
  worker = data.restart(); generator = new BriefingGenerator(native.ctx, worker); t.after(() => generator.close())
  native.response(async call => structured(call))
  const resumed = await generator.resume(request.requestId, async () => {})
  assert.equal(resumed.sessionId, previous.id)
  assert.equal((await generator.wait(request.requestId)).body, '已完成独立简报。')
  assert.equal(native.creations.length, 1)
  assert.equal(native.requests.at(-1).agentPreset, 'stella-briefing')
})

test('a briefing cannot change between legacy and new presets while running', async () => {
  let entered = false, preset = 'stella-briefing'
  const agent = directAgent(async ({ events }) => {
    preset = 'alcor-briefing'
    try { await events.get('agent/pre-step')({ modelSelection: route }, async () => { entered = true }) } catch {}
  })
  const generator = new BriefingGenerator({ agentPresets: { presetForSession: () => preset } }, {})
  const state = { requestId: 'r', preset: 'stella-briefing', run: { sessionId: 'same-session', model: route }, parent: agent, controller: new AbortController() }
  generator.current = state
  await assert.rejects(generator.runAgent(state, { entries: [], task: '', rules: '', schema: { type: 'object' } }, async () => {}, async () => {}), /会话的配置已改变/)
  assert.equal(entered, false)
})

test('unfinished briefing refuses unrelated preset before model use', async () => {
  const generator = new BriefingGenerator({
    sessionController: { resolveAgent: async () => ({ agent: { session: { preset: 'owner-default' } } }) },
    agentPresets: { presetForSession: session => session.preset },
  }, {})
  const state = { run: { sessionId: 'unrelated', context: {} }, controller: new AbortController() }
  await assert.rejects(generator.prepareSession(state, async () => {}), /会话的配置已改变/)
})
