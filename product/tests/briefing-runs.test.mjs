import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { BusinessWorker } from '../../packages/stella-organizer/src/business.js'
import { BriefingGenerator } from '../../packages/stella-organizer/src/briefing.js'
import { buildBriefing } from '../../packages/stella-organizer/src/records.js'
import { schemas } from '../../packages/stella-organizer/src/remote.js'
import { snapshotJsonValue } from '@deepseek-ai/dsh-util-values'

const source = { source: 'elearning', accountId: 'school', tool: 'mcp__elearning__fetch', method: 'fetch', arguments: { course_ids: ['selected'] } }
const options = { worker: new URL('../../packages/stella-secretary/business/worker.py', import.meta.url) }
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
      create: async request => { creations.push(request); const id = 'run-' + randomUUID(); let model = { provider: 'synthetic', model: 'default-model' }; agents.set(id, { session: { id, header: {} }, model: { current: () => model, select: value => { model = value } } }); return { sessionId: id } },
      rename: async () => {}, resolveAgent: async id => ({ agent: agents.get(id) }),
    },
    tools: { execute: async ({ calls: [call] }) => {
      readCount++; const args = JSON.parse(call.arguments); assert.deepEqual(args.course_ids, ['selected'])
      return { results: [{ result: { value: { structuredContent: { value: { source_items: [{ source_id: 'one', title: 'Synthetic task', snippet: 'secret-payload', occurred_at: new Date(Date.now() - 1000).toISOString() }], cursors: { checkpoint: 'source-state' }, next_offset: null } } } } }] }
    } },
    subagents: { startActivation: async ({ request, signal }) => {
      requests.push(request)
      return { childId: 'batch-' + requests.length, dispose: async () => {}, result: respond(request, signal) }
    } },
  }
  return { ctx, agents, creations, requests, reads: () => readCount, response: handler => { respond = handler } }
}
function structured(request) {
  const text = request.prompt[0].text
  const rows = JSON.parse(text.split('可阅读的原文数据：\n')[1])
  if (request.outputSchema.properties.decisions) return { stopReason: 'completed', structured: { decisions: rows.map(row => ({ id: row.id, selected: true, reason: 'Needed', section: 'action', score: 90, summary: row.snippet })) } }
  const ids = request.outputSchema.properties.source_item_ids.items.enum
  return { stopReason: 'completed', structured: { body: '已完成独立简报。', source_item_ids: ids, reports: ids.map(id => ({ id, content: '具体报告' })) } }
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
  assert.deepEqual(native.creations, [{ agentPreset: 'stella-briefing', cwd: '/tmp' }])
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
  assert.equal(native.requests.length, 2)
  for (const call of native.requests) {
    assert.equal(call.parent.session.id, started.sessionId)
    assert.equal(call.agentPreset, 'stella-briefing')
    assert.deepEqual(call.agentOptions, route)
    assert(!call.prompt[0].text.includes('NEW RULE MUST NOT ENTER THIS RUN'))
  }
  const saved = await state(worker, request.requestId)
  assert.equal(saved.completedBatches, 2); assert.equal(saved.completedSources, 1)
  assert.deepEqual(saved.collected, []); assert.deepEqual(saved.submissions, [])
  assert.equal((await generator.generate(request)).id, result.id)
  const completed = await generator.start(request)
  assert.equal(completed.phase, 'completed')
  assert.equal(completed.briefingId, result.business.briefingId)
  assert.deepEqual(snapshotJsonValue(completed), completed)
  assert.equal(native.requests.length, 2); assert.equal(native.reads(), 1)
  assert.equal(native.creations.length, 1)
})

test('failed runs resume saved batches after worker restart, preserve scope and require explicit retry', async t => {
  const fixtureData = await fixture(t), native = nativeContext(), request = input()
  let worker = fixtureData.worker
  let generator = new BriefingGenerator(native.ctx, worker)
  native.response(async request => request.outputSchema.properties.decisions ? structured(request) : { stopReason: 'failed' })
  await assert.rejects(generator.generate(request), /模型未提交/)
  assert.equal((await state(worker, request.requestId)).submissions.length, 1)
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

test('hard host loss is marked interrupted without replay and resumes only its unfinished batch', async t => {
  const f = await fixture(t), native = nativeContext(), request = input()
  let generator = new BriefingGenerator(native.ctx, f.worker)
  native.response(async (request, signal) => {
    if (request.outputSchema.properties.decisions) return structured(request)
    return new Promise(resolve => signal.addEventListener('abort', () => resolve({ stopReason: 'cancelled' }), { once: true }))
  })
  await generator.start(request)
  await until(async () => (await state(f.worker, request.requestId)).submissions.length === 1 && native.requests.length === 2)
  f.worker.process.kill('SIGKILL')
  await new Promise(resolve => f.worker.process.once('exit', resolve))
  await generator.close()
  const worker = f.restart()
  generator = new BriefingGenerator(native.ctx, worker); t.after(() => generator.close())
  assert.equal((await generator.status()).phase, 'interrupted')
  assert.equal(native.requests.length, 2)
  native.response(async request => structured(request))
  await generator.resume(request.requestId)
  await generator.wait(request.requestId)
  assert.equal(native.requests.length, 3); assert.equal(native.reads(), 1)
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
  native.response(async request => request.outputSchema.properties.decisions ? structured(request) : { stopReason: 'failed' })
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
  assert.equal(recovered.completedBatches, 2)
  assert.equal((await restarted.generate(request)).id, result.id)
  assert.equal(native.requests.length, 2)
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
