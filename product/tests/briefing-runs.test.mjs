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
  const decisions = rows.map(row => ({ id: row.id, selected: true, reason: 'Needed', section: 'action', score: 90, summary: row.snippet ?? row.body }))
  const fields = text.match(/本批字段：([^。]+)。/)[1].split(', ')
  const expected = text.includes('一次提交本批') ? ['decisions'] : ['decisions', 'body', 'source_item_ids', 'reports']
  assert.deepEqual(Object.keys(request.outputSchema.properties), expected)
  if (!fields.includes('reports')) return { stopReason: 'completed', structured: { decisions } }
  const ids = fields.includes('decisions') ? rows.map(row => row.id) : JSON.parse(text.match(/"expected_ids": (\[[^\]]*\])/)[1])
  return { stopReason: 'completed', structured: { ...(fields.includes('decisions') ? { decisions } : {}), body: '已完成独立简报。', source_item_ids: ids, reports: ids.map(id => ({ id, content: '具体报告' })) } }
}
const input = () => { const now = new Date().toISOString(); return { requestId: randomUUID(), record: buildBriefing([], now.slice(0, 10), '', now), sources: [source], parent: { session: { id: 'chat-owner', header: { cwd: '/tmp' } }, model: { current: () => route } } } }
const state = (worker, id) => worker.call('briefing_run', { method: 'get', request_id: id })
async function until(check) { for (let i = 0; i < 400; i++) { const value = await check(); if (value) return value; await new Promise(resolve => setTimeout(resolve, 10)) } throw new Error('Run did not settle') }

for (const trigger of ['manual', 'scheduled']) test(trigger + ' briefing saves schoolwork when mail times out and catches up missed mail', async t => {
  const { worker } = await fixture(t), native = nativeContext()
  const generator = new BriefingGenerator(native.ctx, worker); t.after(() => generator.close())
  const email = { source: 'email', accountId: 'inbox', tool: 'mcp__email__fetch_messages', method: 'fetch_messages', arguments: { account_id: 'inbox' } }
  const school = native.ctx.tools.execute
  const reads = []
  let offline = true
  const occurred = new Date(Date.now() - 3000).toISOString()
  native.ctx.tools.execute = async request => {
    if (request.calls[0].name !== email.tool) return school(request)
    reads.push(JSON.parse(request.calls[0].arguments))
    if (offline) throw new Error('Mail read timed out')
    return { results: [{ result: { value: { items: [{ source_id: 'missed', title: 'Missed mail', occurred_at: occurred, snippet: 'Mail body' }], cursors: { imap_uid: '10' } } } }] }
  }
  const generate = () => generator.generate({ ...input(), sources: [email, source], trigger })
  const partial = await generate()
  const snapshot = partial.business.snapshot
  assert.equal(snapshot.briefing.status, 'partial')
  assert.equal(snapshot.briefing.error_message, '邮件未覆盖')
  assert.deepEqual(snapshot.items.map(row => row.source), ['elearning'])
  assert.deepEqual(snapshot.briefing.updated_sources, ['elearning:school'])
  assert.equal(snapshot.revisions[0].trigger, trigger)
  const cursors = config => worker.call('source_cursors', { source: config.source, account_id: config.accountId, configuration: config })
  assert.deepEqual(await cursors(email), {})
  assert.equal((await cursors(source)).checkpoint, 'source-state')
  assert.equal((await generator.status()).completedSources, 1)
  offline = false
  const recovered = await generate()
  assert.equal(reads[1].since, reads[0].since)
  assert.deepEqual(recovered.business.snapshot.items.map(row => row.source).sort(), ['elearning', 'email'])
  assert.equal(recovered.business.snapshot.briefing.status, 'completed')
  assert.equal(recovered.business.snapshot.briefing.error_message, '')
  assert.equal((await cursors(email)).imap_uid, '10')
  assert.equal((await generator.generate({ ...input(), sources: [email], trigger })).business.snapshot.items.length, 1)
})

test('all failed sources leave no report and an explicit resume re-reads them', async t => {
  const { worker } = await fixture(t), native = nativeContext(), request = input()
  const generator = new BriefingGenerator(native.ctx, worker); t.after(() => generator.close())
  const school = native.ctx.tools.execute
  native.ctx.tools.execute = async () => { throw new Error('offline') }
  await assert.rejects(generator.generate(request), /offline/)
  assert.equal((await state(worker, request.requestId)).phase, 'failed')
  assert.deepEqual(await worker.call('invoke', { domain: 'briefing', method: 'list_briefings' }), [])
  native.ctx.tools.execute = school
  await generator.resume(request.requestId)
  assert.equal((await generator.wait(request.requestId)).business.snapshot.briefing.status, 'completed')
})

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

test('briefing history paginates by creation time and reveals only public run metadata', async t => {
  const { worker } = await fixture(t)
  const ids = []
  for (let index = 0; index < 3; index++) {
    const request = input(); ids.push(request.requestId)
    await worker.call('briefing_run', { method: 'prepare', request_id: request.requestId, record: { ...request.record, notes: 'private source material' }, sources: [source], context: index === 0 ? {} : { trigger: 'scheduled', parentSessionId: `conversation-${index}` } })
    await worker.call('briefing_run', { method: 'save', request_id: request.requestId, patch: { phase: 'cancelled', sessionId: `execution-${index}` } })
  }
  // Updating an older run must not move it ahead of a newer creation or skip a page.
  await worker.call('briefing_run', { method: 'save', request_id: ids[0], patch: { message: '旧任务已更新' } })
  const first = await worker.call('briefing_run', { method: 'history', limit: 2 })
  schemas.briefingRuns[1].parse(first)
  assert.deepEqual(first.runs.map(row => row.requestId), ids.slice(1).reverse())
  assert.equal(first.runs[0].conversationSessionId, 'conversation-2')
  assert.equal(first.runs[0].trigger, 'scheduled')
  const last = await worker.call('briefing_run', { method: 'history', limit: 2, before: first.nextCursor })
  assert.deepEqual(last.runs.map(row => row.requestId), [ids[0]])
  assert.equal(last.runs[0].trigger, 'manual')
  assert.equal(last.nextCursor, null)
  assert(!JSON.stringify([first, last]).includes('private source material'))
  assert(!JSON.stringify([first, last]).includes('sources'))
  for (const limit of [0, 51, 1.5, true]) await assert.rejects(worker.call('briefing_run', { method: 'history', limit }))
  assert.equal(schemas.briefingRuns[0].safeParse({ limit: 51 }).success, false)
})

test('briefing button preferences persist alongside timezone and reject invalid writes atomically', async t => {
  const f = await fixture(t)
  const call = (worker, method, payload) => worker.call('invoke', { domain: 'briefing', method, arguments: payload ? { payload } : {} })
  const defaults = await call(f.worker, 'get_preferences')
  assert.equal(defaults.task_target, 'new')
  await call(f.worker, 'update_preferences', { time_zone: 'Asia/Shanghai', task_prompt: '生成重点通知简报', task_target: 'current', task_workspace_id: 'work' })
  const saved = await call(f.restart(), 'get_preferences')
  assert.equal(saved.task_prompt, '生成重点通知简报')
  assert.equal(saved.time_zone, 'Asia/Shanghai')
  for (const payload of [{ task_prompt: '' }, { task_prompt: 'x'.repeat(12001) }, { task_target: 'schedule' }, { task_workspace_id: 'x'.repeat(201) }]) {
    await assert.rejects(call(f.worker, 'update_preferences', payload))
    assert.deepEqual(await call(f.worker, 'get_preferences'), saved)
  }
})

test('briefing reuse binding survives worker restart and cannot be changed through preferences', async t => {
  const f = await fixture(t)
  const binding = { sessionId: 'owned-session', workspaceId: 'selected-workspace' }
  assert.equal(await f.worker.call('briefing_run', { method: 'session' }), null)
  await f.worker.call('briefing_run', { method: 'session', binding })
  assert.deepEqual(await f.restart().call('briefing_run', { method: 'session' }), binding)
  await f.worker.call('invoke', { domain: 'briefing', method: 'update_preferences', arguments: { payload: { task_target: 'new_once' } } })
  await assert.rejects(f.worker.call('invoke', { domain: 'briefing', method: 'update_preferences', arguments: { payload: { task_session_id: 'foreign' } } }))
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
  await until(() => release)
  const executionId = (await generator.status()).sessionId
  assert.deepEqual(native.creations, [{ agentPreset: 'alcor-briefing', cwd: '/tmp' }])
  request.parent.model.current = () => ({ provider: 'changed', model: 'other-model' })
  await worker.call('invoke', { domain: 'briefing', method: 'create_rule', arguments: { payload: { text: 'NEW RULE MUST NOT ENTER THIS RUN', source: '*', enabled: true } } })
  const status = await generator.status()
  assert.deepEqual(snapshotJsonValue(status), status)
  assert(!JSON.stringify(status).includes('secret-payload'))
  assert(!('sources' in status)); assert(!('submissions' in status))
  release()
  const result = await generator.wait(request.requestId)
  assert.equal(result.body, '## 需要关注\n\n### Synthetic task\nsecret-payload')
  assert.equal(native.requests.length, 1)
  for (const call of native.requests) {
    assert.equal(call.parent.session.id, executionId)
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
  const running = generator.generate(request)
  const rejected = assert.rejects(running, /中断/)
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
  await assert.rejects(generator.generate(input()), /Synthetic write failure/)
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

test('briefing prompt sends each full body once and retains independent summaries', async () => {
  const entries = [
    { id: 'short', snippet: '周五交作业', body: '周五交作业' },
    { id: 'long', snippet: '周五交作业', body: '周五交作业，提交 PDF。' },
    { id: 'different', snippet: '截止日已更正', body: '周一 17:00 提交。' },
    { id: 'preview', snippet: '仅有摘要' },
  ]
  const agent = directAgent(async ({ message, tools, events, agent }) => {
    const material = JSON.parse(message.content[0].text.split('可阅读的原文数据：\n')[1])
    assert.deepEqual(material, [
      { id: 'short', body: entries[0].body }, { id: 'long', body: entries[1].body }, entries[2], entries[3],
    ])
    const exec = execution(agent)
    await tools.get('briefing_submit').execute({}, exec)
    events.get('tools/result')(exec, { isError: false })
  })
  const generator = new BriefingGenerator({}, {})
  const state = { run: { sessionId: 'same-session', model: route }, parent: agent, controller: new AbortController() }
  generator.current = state
  await generator.runAgent(state, { entries, task: '', rules: '', schema: {}, submitSchema: { type: 'object' } }, async () => {}, async () => {})
  assert.equal(entries[0].snippet, '周五交作业')
})

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
    assert.deepEqual(JSON.parse(reconstructed), { id: 'one', body })
    const exec = execution(agent)
    await submit.execute({ decisions: [{ id: 'one' }] }, exec)
    events.get('tools/result')(exec, { isError: false }); submits++
  })
  const generator = new BriefingGenerator({}, {})
  const state = { requestId: 'r', run: { sessionId: 'same-session', model: route }, parent: agent, controller: new AbortController() }
  generator.current = state
  const result = await generator.runAgent(state, { entries: [{ id: 'one', body, snippet: body.slice(0, 1000) }], task: 'task', rules: 'rules', schema: { type: 'object' } }, async () => {}, async () => {})
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
  await worker.call('briefing_run', { method: 'save', request_id: request.requestId, patch: { routingVersion: null } })
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

test('a file fact is saved without a model and excluded from every mixed-run prompt', async t => {
  for (const mixed of [false, true]) {
    const { worker } = await fixture(t), native = nativeContext(), inputData = input()
    const generator = new BriefingGenerator(native.ctx, worker)
    t.after(() => generator.close())
    const time = new Date(Date.now() - 1000).toISOString()
    const fact = { source_id: 'f', title: 'Course file', occurred_at: time,
      payload: { resource_type: 'file', course_id: 'school', body: 'FILE_ONLY_SENTINEL' } }
    const notice = { source_id: 'n', title: 'Teacher notice', occurred_at: time,
      payload: { resource_type: 'announcement', body: 'NOTICE_ONLY_SENTINEL' } }
    native.ctx.tools.execute = async () => ({ results: [{ result: { value: { structuredContent: {
      value: { source_items: mixed ? [fact, notice] : [fact], cursors: {}, next_offset: null },
    } } } }] })
    const result = await generator.generate(inputData)
    assert.equal(native.creations.length, mixed ? 1 : 0)
    assert.equal(native.requests.length, mixed ? 1 : 0)
    for (const request of native.requests) {
      assert.ok(request.prompt[0].text.includes('NOTICE_ONLY_SENTINEL'))
      assert.ok(!request.prompt[0].text.includes('FILE_ONLY_SENTINEL'))
    }
    assert.ok(result.body.includes('FILE_ONLY_SENTINEL'))
    if (mixed) assert.ok(result.body.includes('NOTICE_ONLY_SENTINEL'))
  }
})

test('all unresolved messages arrive together with only their own material', async t => {
  const { worker } = await fixture(t), native = nativeContext()
  const generator = new BriefingGenerator(native.ctx, worker)
  t.after(() => generator.close())
  const time = new Date(Date.now() - 1000).toISOString()
  const notices = Array.from({ length: 24 }, (_, i) => ({
    source_id: 'n' + i, thread_id: 'shared', title: 'Notice ' + i, occurred_at: time,
    payload: { resource_type: 'announcement', body: '正文'.repeat(750) + 'END-' + i,
      prior_submission: 'NEVER_SEND_CONTEXT' },
  }))
  native.ctx.tools.execute = async () => ({ results: [{ result: { value: { structuredContent: {
    value: { source_items: notices, cursors: {}, next_offset: null },
  } } } }] })
  native.response(async request => {
    const prompt = request.prompt[0].text
    assert.ok(!prompt.includes('NEVER_SEND_CONTEXT'))
    const rows = JSON.parse(prompt.split('可阅读的原文数据：\n')[1])
    assert.equal(rows.length, 24)
    assert.ok(rows.every(row => row.body.endsWith('END-' + row.title.split(' ')[1])))
    return structured(request)
  })
  const result = await generator.generate(input())
  assert.equal(result.business.snapshot.items.length, 24)
  assert.equal(native.requests.length, 1)
})

test('a new plugin can contribute local facts and unresolved text through the existing source contract', async t => {
  const { worker } = await fixture(t), native = nativeContext()
  const generator = new BriefingGenerator(native.ctx, worker)
  t.after(() => generator.close())
  const time = new Date(Date.now() - 1000).toISOString()
  const config = { source: 'plugin:library:loans', accountId: 'reader', label: 'Library',
    tool: 'library_briefing_fetch', method: 'briefing_fetch', arguments: { source_id: 'loans' } }
  native.ctx.tools.execute = async () => ({ results: [{ result: { value: { source_id: 'loans', account_id: 'reader',
    next_offset: null, items: [
      { source_id: '1', title: 'Renewed', occurred_at: time, payload: { briefing_fact: 'RENEWED_LOCALLY' } },
      { source_id: '2', title: 'Library notice', occurred_at: time, payload: { body: 'Library changes to explain' } },
    ] } } }] })
  const result = await generator.generate({ ...input(), sources: [config] })
  assert.equal(native.requests.length, 1)
  const prompt = native.requests[0].prompt[0].text
  assert.ok(!prompt.includes('RENEWED_LOCALLY'))
  assert.ok(prompt.includes('Library changes to explain'))
  assert.ok(result.body.includes('RENEWED_LOCALLY'))
  assert.equal(result.business.snapshot.items.length, 2)
})
