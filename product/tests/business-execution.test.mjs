import { discoverBriefingSources } from '../../packages/alcor-organizer/src/briefing-sources.js'
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm, writeFile, realpath } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'
import { HubWorker } from '../../packages/alcor-agent-hub/src/worker.js'
import { HubCoordinator } from '../../packages/alcor-agent-hub/src/hub.js'
import { ReminderDelivery } from '../../packages/alcor-organizer/src/reminders.js'
import { BriefingGenerator } from '../../packages/alcor-organizer/src/briefing.js'
import { assertObjectJsonSchema } from '@deepseek-ai/dsh-tools'
import { buildBriefing } from '../../packages/alcor-organizer/src/records.js'

class TestBriefingGenerator extends BriefingGenerator {
  constructor(ctx, worker) {
    const agents = new Map()
    super({ ...ctx, sessionController: {
      create: async () => { const sessionId = 'briefing-' + randomUUID(); agents.set(sessionId, { session: { id: sessionId }, cancel: () => {}, model: { current: () => ({ provider: 'synthetic', model: 'test' }), select: () => {} } }); return { sessionId } },
      rename: async () => {}, resolveAgent: async id => ({ agent: agents.get(id) }),
    } }, worker)
  }

  // Adapt the existing deterministic model fixtures; real scoped tools are covered by briefing-runs.
  async runAgent(state, request, authorize) {
    await authorize()
    const prompt = `任务\n${request.task}\n\n可阅读的原文数据：\n${JSON.stringify(request.entries)}`
    const activation = await this.ctx.subagents.startActivation({ signal: state.controller.signal,
      request: { prompt: [{ type: 'text', text: prompt }], outputSchema: request.schema } })
    const result = await activation.result
    state.controller.signal.throwIfAborted()
    if (result.stopReason !== 'completed' || !result.structured) throw new Error('模型未提交完整简报')
    const value = result.structured
    if (request.schema.properties.decisions && request.schema.properties.reports && !value.reports) {
      const ids = value.decisions.filter(row => row.selected).map(row => row.id)
      Object.assign(value, { body: '已核对两条来源，准备处理。', source_item_ids: ids, reports: ids.map(id => ({ id, content: '具体任务报告' })) })
    }
    return value
  }
}

async function fixture(context) {
  const directory = await mkdtemp(join(tmpdir(), 'stella-business-execution-'))
  const options = { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) }
  const business = new BusinessWorker(directory, options)
  const hub = new HubWorker(join(directory,'hub')); await hub.call('initialize')
  const worker = {call:(action,input={}) => action==='invoke' && input.domain==='agent_hub' ? hub.call(action,input) : business.call(action,input),close:async()=>{await hub.close();await business.close()}}
  context.after(async () => { await worker.close(); await rm(directory, { recursive: true, force: true }) })
  const invoke = (domain, method, args = {}) => worker.call('invoke', { domain, method, arguments: args })
  return { directory, options, worker, invoke }
}

async function waitFor(check) {
  const deadline = Date.now() + 8000
  while (Date.now() < deadline) {
    const value = await check()
    if (value) return value
    await new Promise(resolve => setTimeout(resolve, 20))
  }
  throw new Error('Business operation did not settle')
}

test('briefing settings reject stale writes', async context => {
  const { worker } = await fixture(context)
  const previous = await worker.call('source_settings', { sources: [{ source: 'alcor_tasks', accountId: 'local' }] })
  await worker.call('source_settings', { sources: [], expected_sources: previous })
  await assert.rejects(worker.call('source_settings', { sources: previous, expected_sources: previous }), /其他页面变化/)
  assert.deepEqual(await worker.call('source_settings'), [])

})

test('Hub executes dependency joins, captures model/persona, and retries a failed step explicitly', async context => {
  const { worker, invoke } = await fixture(context)
  const requests = []
  let active = 0
  let maximum = 0
  let fail = true
  const coordinator = new HubCoordinator({ logger: { warn: assert.fail }, sessionController: { resolveAgent: async () => ({ agent: { model: { current: () => ({ provider: 'synthetic', model: 'default-model' }) } } }) }, subagents: {
    startActivation: async request => {
      requests.push(request)
      active++; maximum = Math.max(maximum, active)
      return { childId: 'child-' + requests.length, dispose: async () => {}, result: new Promise(resolve => setTimeout(() => {
        active--
        const failed = request.label === 'Fail once' && fail
        if (failed) fail = false
        resolve({ stopReason: failed ? 'failed' : 'completed', output: [{ type: 'text', text: 'Formal reply ' + request.label }] })
      }, 40)) }
    },
  } }, worker)
  context.after(() => coordinator.close())
  const task = await invoke('agent_hub', 'create_task', { payload: { title: 'Join', goal: 'Deliver report', participants: [
    { nativeAgentId: 'stella', displayName: 'Research', persona: 'Research persona', modelSelection: { mode: 'explicit', providerId: 'synthetic', modelId: 'research-model' } },
    { nativeAgentId: 'stella', displayName: 'Review' }, { nativeAgentId: 'stella', displayName: 'Fail once' },
  ] }, request_id: randomUUID() })
  const steps = [
    { stepId: 'a', title: 'Research A', participantId: task.participants[0].participantId, dependsOn: [] },
    { stepId: 'b', title: 'Research B', participantId: task.participants[1].participantId, dependsOn: [] },
    { stepId: 'c', title: 'Join after both', participantId: task.participants[2].participantId, dependsOn: ['a', 'b'] },
    { stepId: 'd', title: 'Final summary', participantId: task.participants[1].participantId, dependsOn: ['c'] },
  ]
  await invoke('agent_hub', 'set_workflow', { task_id: task.taskId, steps, expected_revision: task.revision, request_id: randomUUID() })
  await coordinator.start({ task_id: task.taskId, parentSessionId: 'parent' })
  await waitFor(() => !coordinator.tasks.has(task.taskId))
  let result = await invoke('agent_hub', 'get_task', { task_id: task.taskId })
  assert.equal(result.status, 'failed')
  assert.equal(maximum, 2)
  assert.equal(requests[2].request.prompt[0].text.includes('Formal reply Research'), true)
  assert.equal(requests[0].request.prompt[0].text.includes('Research persona'), true)
  assert.deepEqual(requests[0].request.agentOptions, { provider: 'synthetic', model: 'research-model' })
  assert.equal(result.steps[0].attempts[0].actualModel, 'research-model')
  await coordinator.start({ task_id: task.taskId, parentSessionId: 'parent', step_id: 'c', retry: true })
  await waitFor(() => !coordinator.tasks.has(task.taskId))
  result = await invoke('agent_hub', 'get_task', { task_id: task.taskId })
  assert.equal(result.status, 'succeeded')
  assert.equal(result.steps[2].attempts.length, 2)
  assert.equal(result.messages.filter(message => message.kind === 'reply').length, 4)
  assert.equal(result.steps[2].attempts[1].attemptId, 'child-4')
  assert.equal(requests.length, 5, 'Retry only the failed step, then continue its pending dependents')
  assert.ok(requests[4].request.prompt[0].text.includes('Formal reply Fail once'))
})

test('unreturned Hub attempts become unknown after restart and do not replay', async context => {
  const { directory, options, worker, invoke } = await fixture(context)
  let task = await invoke('agent_hub', 'create_task', { payload: { title: 'Interrupted', goal: 'Check once', participants: [{ nativeAgentId: 'stella' }] }, request_id: randomUUID() })
  task = await invoke('agent_hub', 'set_workflow', { task_id: task.taskId, steps: [{ stepId: 'one', title: 'One', participantId: task.participants[0].participantId, dependsOn: [] }], expected_revision: task.revision, request_id: randomUUID() })
  await invoke('agent_hub', 'claim_step', { task_id: task.taskId, step_id: 'one' })
  await worker.close()
  const restarted = new HubWorker(join(directory,'hub')); await restarted.call('initialize')
  context.after(() => restarted.close())
  const result = await restarted.call('invoke', { domain: 'agent_hub', method: 'get_task', arguments: { task_id: task.taskId } })
  assert.equal(result.steps[0].attempts[0].status, 'outcome_unknown')
  assert.equal(result.steps[0].attempts.length, 1)
  assert.equal(result.messages.at(-1).kind, 'status')
})

test('external CLI runs use the chosen workspace, persist formal output, and support cancellation', async context => {
  const { directory, worker, invoke } = await fixture(context)
  const binary = join(directory, 'synthetic-codex')
  await writeFile(binary, '#!/usr/bin/env python3\nimport json,os,sys,time\nif "--version" in sys.argv:\n print("synthetic 1.0")\nelif "WAIT" in sys.argv[-1]:\n time.sleep(30)\nelse:\n print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"合成回复："+os.getcwd()}}),flush=True)\n', { mode: 0o700 })
  const provider = await invoke('agent_hub', 'upsert_provider', { provider: { providerLocalId: 'codex-cli', binaryPath: binary } })
  assert.equal((await invoke('agent_hub', 'probe_provider', { provider_config_id: provider.providerConfigId })).status, 'ready')
  const coordinator = new HubCoordinator({ logger: { warn: assert.fail } }, worker)
  context.after(() => coordinator.close())
  async function create(goal) {
    let task = await invoke('agent_hub', 'create_task', { payload: { title: goal, goal, participants: [{ providerConfigId: provider.providerConfigId }] }, request_id: randomUUID() })
    return invoke('agent_hub', 'set_workflow', { task_id: task.taskId, steps: [{ stepId: 'run', title: goal, participantId: task.participants[0].participantId, dependsOn: [] }], expected_revision: task.revision, request_id: randomUUID() })
  }
  const task = await create('Deliver')
  await coordinator.start({ task_id: task.taskId, workspacePath: directory })
  await waitFor(() => !coordinator.tasks.has(task.taskId))
  const result = await invoke('agent_hub', 'get_task', { task_id: task.taskId })
  assert.equal(result.status, 'succeeded')
  assert.equal(result.messages.find(message => message.kind === 'reply').body, '合成回复：' + await realpath(directory))
  assert.equal(result.steps[0].attempts[0].artifactRefs.length, 1)
  const waiting = await create('WAIT')
  await coordinator.start({ task_id: waiting.taskId, workspacePath: directory })
  await waitFor(async () => (await invoke('agent_hub', 'list_external_runs')).some(run => run.status === 'running'))
  await coordinator.cancel(waiting.taskId)
  await waitFor(() => !coordinator.tasks.has(waiting.taskId))
  const cancelled = await invoke('agent_hub', 'get_task', { task_id: waiting.taskId })
  assert.equal(cancelled.steps[0].attempts[0].status, 'cancelled')
})

test('desktop reminder outbox retries failures, suppresses acknowledged and cancelled deliveries', async context => {
  const { worker, invoke } = await fixture(context)
  const past = new Date(Date.now() - 5000).toISOString()
  const todo = await invoke('tasks', 'create_todo', { payload: { title: 'Reminder', remind_at: past, reminder_delivery: { primary: { channel_type: 'desktop' } } } })
  let sends = 0
  let fail = true
  const delivery = new ReminderDelivery(worker, { send: async title => { assert.equal(title, 'Reminder'); sends++; if (fail) throw new Error('Synthetic refusal') } })
  await delivery.tick()
  fail = false
  await delivery.tick()
  await delivery.tick()
  assert.equal(sends, 2)
  await invoke('tasks', 'update_todo', { todo_id: todo.id, payload: { remind_at: new Date(Date.now() + 86400000).toISOString() } })
  await delivery.tick()
  assert.equal(sends, 2)
  await delivery.close()
})

test('model briefings collect MCP pages, validate complete decisions, retry a failed source and commit one revision', async context => {
  const { worker, invoke } = await fixture(context)
  const date = new Date().toISOString().slice(0, 10)
  const stamp = new Date(Date.now() - 1000).toISOString()
  let modelCalls = 0
  const pages = []
  let mailOffline = true
  const generator = new TestBriefingGenerator({ tools: { execute: async ({ calls }) => {
    const args = JSON.parse(calls[0].arguments)
    pages.push(args)
    if (calls[0].name.includes('__email__')) return {results:[{result:mailOffline ? {isError:true,error:{message:'Synthetic source unavailable'}} : {value:{items:[],cursors:{}}}}]}
    return { results: [{ result: { isError: false, value: { structuredContent: { value: { source_items: [{ source_id: 'source-' + args.offset, title: 'Candidate ' + args.offset, snippet: 'Due tomorrow', occurred_at: stamp }], next_offset: args.offset === 0 ? 1 : null } } } } }] }
  } }, subagents: { startActivation: async ({ request }) => {
    assertObjectJsonSchema(request.outputSchema)
    modelCalls++
    const prompt = request.prompt[0].text
    const original = JSON.parse(prompt.split('可阅读的原文数据：\n')[1])
    let structured
    if (request.outputSchema.properties.decisions) structured = { decisions: original.map(item => ({ id: item.id, selected: true, reason: 'Needs action', section: 'action', score: 90, summary: item.snippet })) }
    else {
      const expected = JSON.parse(prompt.slice(prompt.indexOf('{"date":'), prompt.indexOf('\n\n可阅读的原文数据：'))).items.map(item => item.id)
      structured = { body: '已核对两条来源，准备处理。', source_item_ids: expected, reports: expected.map(id => ({ id, content: '具体任务报告' })) }
    }
    return { dispose: async () => {}, result: Promise.resolve({ stopReason: 'completed', structured }) }
  } } }, worker)
  context.after(() => generator.close())
  const record = buildBriefing([], date, '', stamp)
  const requestId = randomUUID()
  const sources = [
    { source: 'elearning', accountId: 'synthetic', tool: 'mcp__elearning__fetch', method: 'fetch', arguments: {} },
    { source: 'email', accountId: 'mail-test', tool: 'mcp__email__fetch_messages', method: 'fetch_messages', arguments: { account_id: 'mail-test' } },
  ]
  await worker.call('source_settings', { sources })
  assert.deepEqual(await worker.call('source_settings'), sources)
  await assert.rejects(generator.generate({record,requestId,parent:{},sources}),/Synthetic source unavailable/)
  assert.equal((await invoke('briefing','list_briefings')).length,0)
  assert.equal(await invoke('briefing','get_cursor',{source:'elearning',account_id:'synthetic',cursor_key:'until'}),null)
  mailOffline = false
  await generator.resume(requestId,async()=>{})
  const result = await generator.wait(requestId)
  assert.equal(result.body, '已核对两条来源，准备处理。')
  assert.equal(modelCalls, 1)
  const partial = new TestBriefingGenerator({ tools: { execute: async ({ calls }) => {
    const args = JSON.parse(calls[0].arguments)
    return { results: [{ result: { isError: false, value: { structuredContent: { value: { source_items: [], next_offset: args.offset === 0 ? 1 : null, errors: args.offset === 0 ? ['First page failed'] : [] } } } } }] }
  } } }, worker)
  assert.deepEqual((await partial.collect(sources[0], pages[0].since, pages[0].until, new AbortController().signal)).errors, ['First page failed'])
  let receivedCursors
  const incremental = new TestBriefingGenerator({ tools: { execute: async ({ calls }) => {
    receivedCursors = JSON.parse(calls[0].arguments).cursors
    return { results: [{ result: { isError: false, value: { structuredContent: { value: { items: [], cursors: {} } } } } }] }
  } } }, worker)
  await incremental.collect({ ...sources[0], method: 'fetch_source', tool: 'mcp__elearning__fetch_source' }, pages[0].since, pages[0].until, new AbortController().signal)
  assert.deepEqual(receivedCursors, {}, 'A different source method has a different cursor scope')
  assert.deepEqual(pages.filter(page => 'offset' in page).map(page => page.offset), [0, 1])
  const saved = await invoke('briefing', 'get_briefing', { briefing_id: result.business.briefingId })
  assert.equal(saved.briefing.status, 'completed')
  assert.equal(saved.mail.length, 2)
  assert.equal(saved.items.length, 2)
  assert.equal(saved.briefing.error_message, '')
  assert.equal((await invoke('briefing', 'get_cursor', { source: 'elearning', account_id: 'synthetic', cursor_key: 'until' })).cursor_value, pages[0].until)
  assert.equal((await invoke('briefing', 'get_cursor', { source: 'email', account_id: 'mail-test', cursor_key: 'until' })).cursor_value, pages[0].until)
  const same = await generator.generate({ record, requestId, parent: {}, sources })
  assert.equal(same.id, result.id)
  assert.equal(modelCalls, 1)
  const inputStamp = new Date().toISOString()
  const invalid = await worker.call('generated_briefing', { record, request_id: randomUUID(), sources: [{ source: 'test', items: [{ source_id: 'bad', title: 'Bad', occurred_at: inputStamp }], cursors: {} }], submissions: [] })
  assert(invalid.model)
  await assert.rejects(worker.call('generated_briefing', { record, request_id: randomUUID(), sources: [{ source: 'test', items: [{ source_id: 'bad', title: 'Bad', occurred_at: inputStamp }], cursors: {} }], submissions: [{ decisions: [] }] }), /不能遗漏/)
})


test('briefing runs expose progress, reject concurrent runs and cancel without committing', async context => {
  const { worker, invoke } = await fixture(context)
  const generator = new TestBriefingGenerator({ subagents: { startActivation: async ({ signal }) => ({
    dispose: async () => {}, result: new Promise(resolve => signal.addEventListener('abort', () => resolve({ stopReason: 'cancelled' }), { once: true })),
  }) } }, worker)
  context.after(() => generator.close())
  const record = buildBriefing([], '2026-11-05', 'Synthetic input', new Date().toISOString())
  const pending = generator.generate({ record, requestId: randomUUID(), parent: {}, sources: [] })
  const rejected = assert.rejects(pending, /取消/)
  await waitFor(async () => (await generator.status()).phase === 'model')
  await assert.rejects(generator.generate({ record, requestId: randomUUID(), parent: {}, sources: [] }), /正在生成/)
  await generator.cancel()
  await rejected
  assert.equal((await generator.status()).phase, 'cancelled')
  assert.equal((await invoke('briefing', 'list_briefings')).length, 0)
})

test('selected briefing sources never silently include unrelated local tasks or bills', async context => {
  const { worker, invoke } = await fixture(context)
  await invoke('tasks', 'create_todo', { payload: { title: 'Private task outside selected sources' } })
  const record = buildBriefing([], '2026-11-05', 'Only this note', new Date().toISOString())
  const result = await worker.call('generated_briefing', { record, request_id: randomUUID(), sources: [] })
  assert.equal(result.model.entries.length, 1)
  assert.equal(result.model.entries[0].title, '补充材料')
})


test('briefing rejects changed source accounts and stops after authorization revocation', async context => {
  const { worker, invoke } = await fixture(context)
  const generator = new TestBriefingGenerator({ tools: { execute: async () => ({ results: [{ result: { value: { structuredContent: { value: { items: [{ source: 'email', account_id: 'different-account' }] } } } } }] }) }, subagents: { startActivation: async () => { throw new Error('must not reach model') } } }, worker)
  context.after(() => generator.close())
  const collected = await generator.collect({ source: 'email', accountId: 'selected', method: 'fetch_messages', arguments: {} }, '2026-11-04T00:00:00Z', '2026-11-05T00:00:00Z', new AbortController().signal)
  assert.deepEqual(collected.items, [])
  assert.match(collected.errors[0], /账号已变化/)
  const record = buildBriefing([], '2026-11-05', 'Synthetic note', new Date().toISOString())
  await assert.rejects(generator.generate({ record, requestId: randomUUID(), parent: {}, sources: [], authorize: async () => { throw new Error('授权已撤销') } }), /授权已撤销/)
  assert.equal((await invoke('briefing', 'list_briefings')).length, 0)
})


test('briefing source discovery only enumerates supported metadata and revocation stops pagination', async context => {
  const calls = []
  const catalog = await discoverBriefingSources({ get: () => undefined, tools: {
    definitions: () => ['mcp__mail__list_sources', 'mcp__mail__fetch_messages', 'mcp__unrelated__list_sources'].map(name => ({ name })),
    execute: async ({ calls: batch }) => { calls.push(batch[0].name); return { results: [{ result: { value: { structuredContent: { value: [{ source: 'email', account_id: 'source-mailbox', mailbox_id: 'connection-mailbox', name: 'Synthetic mailbox' }] } } } }] } },
  } })
  assert.deepEqual(calls, ['mcp__mail__list_sources'])
  const mail = catalog.sources.find(item => item.source === 'email')
  assert.equal(mail.accountId, 'source-mailbox')
  assert.deepEqual(mail.arguments, { account_id: 'connection-mailbox' })
  const { worker } = await fixture(context)
  let pages = 0
  const generator = new TestBriefingGenerator({ tools: { execute: async () => {
    pages++
    return { results: [{ result: { value: { structuredContent: { value: { items: [], next_offset: 1 } } } } }] }
  } } }, worker)
  const result = await generator.collect({ source: 'elearning', accountId: 'test', method: 'fetch', arguments: {} }, '2026-11-04T00:00:00Z', '2026-11-05T00:00:00Z', new AbortController().signal,
    async () => { if (pages) throw new Error('Source authorization revoked') })
  assert.equal(pages, 1)
  assert.deepEqual(result.errors, ['Source authorization revoked'])
})

test('briefing body batches expose only their own thread members and constrain output ids', async context => {
  const { worker } = await fixture(context)
  const date = new Date().toISOString().slice(0,10)
  const stamp = new Date(Date.now()-1000).toISOString()
  let bodyBatches=0
  const generator=new TestBriefingGenerator({tools:{execute:async()=>({results:[{result:{isError:false,value:{structuredContent:{value:{
    source_items:Array.from({length:26},(_,i)=>({source_id:'batch-'+i,thread_id:i<2?'merged-thread':'thread-'+i,title:'Synthetic '+i,snippet:'Needs attention',occurred_at:stamp,payload:{body:'Full original '+i}})),
    next_offset:null,errors:[],
  }}}}}]})},subagents:{startActivation:async({request})=>{
    assertObjectJsonSchema(request.outputSchema)
    const prompt=request.prompt[0].text
    const original=JSON.parse(prompt.split('可阅读的原文数据：\n')[1])
    let structured
    if(request.outputSchema.properties.decisions) structured={decisions:original.map(row=>({id:row.id,selected:true,reason:'Keep',section:'fyi',score:80,summary:'Synthetic summary'}))}
    else {
      bodyBatches++
      const task=JSON.parse(prompt.slice(prompt.indexOf('{"date":'),prompt.indexOf('\n\n可阅读的原文数据：')))
      const expected=task.items.map(row=>row.id)
      const members=task.items.flatMap(row=>row.members.map(member=>member.id))
      assert.deepEqual(original.map(row=>row.id).sort(),[...new Set(members)].sort())
      assert.deepEqual(task.expected_ids,expected)
      assert.deepEqual(request.outputSchema.properties.source_item_ids.items.enum,expected)
      assert.deepEqual(request.outputSchema.properties.reports.items.properties.id.enum,expected)
      structured={body:'Synthetic batch report',source_item_ids:expected,reports:expected.map(id=>({id,content:'Validated report'}))}
    }
    return {dispose:async()=>{},result:Promise.resolve({stopReason:'completed',structured})}
  }}},worker)
  context.after(()=>generator.close())
  const sources=[{source:'elearning',accountId:'synthetic',tool:'mcp__elearning__fetch',method:'fetch',arguments:{}}]
  const result=await generator.generate({record:buildBriefing([],date,'',stamp),requestId:randomUUID(),parent:{},sources})
  assert.equal(result.kind,'briefing')
  assert.equal(bodyBatches,2)
})

test('briefing updates bind cursors to selection, reuse unchanged results and only model changed content', async context => {
  const {worker,invoke}=await fixture(context)
  const day=new Date().toISOString().slice(0,10)
  const stamp=new Date(Date.now()-1000).toISOString()
  const scope={source:'elearning',accountId:'school',tool:'mcp__elearning__fetch',method:'fetch',arguments:{course_ids:['one']}}
  await worker.call('source_settings',{sources:[scope]})
  let changed=false,empty=false,failure=false,modelCalls=0
  const seen=[]
  const generator=new TestBriefingGenerator({tools:{execute:async({calls:[call]})=>{
    const args=JSON.parse(call.arguments);seen.push(args)
    if(failure)throw new Error('Synthetic source failure')
    const data=empty?[]:[{source_id:'a',title:'A',snippet:'A',occurred_at:stamp,payload:{course_id:args.course_ids[0],body:'A'}},
      {source_id:'b',title:'B',snippet:'B',occurred_at:stamp,payload:{course_id:args.course_ids[0],body:changed?'Changed B':'B'}}]
    return {results:[{result:{isError:false,value:{structuredContent:{value:{source_items:data,next_offset:null,errors:[],cursors:{elearning_state:changed?'v2':'v1'}}}}}}]}
  }},subagents:{startActivation:async({request})=>{
    modelCalls++
    const text=request.prompt[0].text
    const originals=JSON.parse(text.split('可阅读的原文数据：\n')[1])
    let structured
    if(request.outputSchema.properties.decisions)structured={decisions:originals.map(row=>({id:row.id,selected:true,reason:'Keep',section:'fyi',score:80,summary:row.snippet}))}
    else {const ids=request.outputSchema.properties.source_item_ids.items.enum||[];structured={body:'Synthetic report',source_item_ids:ids,reports:ids.map(id=>({id,content:'Report '+id}))}}
    return {dispose:async()=>{},result:Promise.resolve({stopReason:'completed',structured})}
  }}},worker)
  context.after(()=>generator.close())
  const generate=()=>generator.generate({record:buildBriefing([],day,'',new Date().toISOString()),requestId:randomUUID(),parent:{},sources:[scope]})
  await generate()
  assert.equal(modelCalls,1)
  const first=await worker.call('source_cursors',{source:'elearning',account_id:'school',configuration:scope})
  assert.equal(first.elearning_state,'v1');assert(first.until)
  empty=true
  await generate()
  assert.equal(modelCalls,1,'No model call for an unchanged refresh')
  assert.equal(seen.at(-1).since,first.until)
  assert.equal(seen.at(-1).cursors.elearning_state,'v1')
  const current=(await invoke('briefing','get_briefing',{briefing_id:(await invoke('briefing','list_briefings'))[0].id}))
  assert.equal(current.items.length,2,'Unchanged cards remain in the current day report')
  changed=true;empty=false
  await generate()
  assert.equal(modelCalls,3,'Only the changed item requires selection and writing')
  const beforeFailure=await worker.call('source_cursors',{source:'elearning',account_id:'school',configuration:scope})
  failure=true
  await assert.rejects(generate(),/Synthetic source failure/)
  assert.deepEqual(await worker.call('source_cursors',{source:'elearning',account_id:'school',configuration:scope}),beforeFailure)
  failure=false;scope.arguments.course_ids=['two']
  assert.deepEqual(await worker.call('source_cursors',{source:'elearning',account_id:'school',configuration:scope}),{},'Do not reuse another course selection cursor')
})

test('first source window ignores legacy history and empty selection has no implicit local sources', async context => {
  const {worker,invoke}=await fixture(context)
  assert.deepEqual(await worker.call('source_settings'),[])
  const scope={source:'elearning',accountId:'school',tool:'mcp__elearning__fetch',method:'fetch',arguments:{}}
  const old=await invoke('briefing','start_briefing',{briefing_date:'2020-01-01'})
  await invoke('briefing','complete_briefing',{briefing_id:old.id,payload:{status:'completed',run_at:'2020-01-01T20:00:00Z',window_start:'1990-01-01T00:00:00Z',window_end:'2020-01-01T20:00:00Z',source_windows:{'elearning:school':{source:'elearning',account_id:'school',start:'1990-01-01T00:00:00Z',end:'2020-01-01T20:00:00Z'}}}})
  const [start]=await worker.call('source_windows',{date:'2026-11-06',run_at:'2026-11-06T08:00:00Z',sources:[scope],time_zone:'Asia/Shanghai'})
  assert.equal(Date.parse(start),Date.parse('2026-11-04T16:00:00Z'),'Previous local midnight')
})


test('incremental reports isolate days and selections, apply changed rules only to new information and keep cursors on model failure', async context => {
  const {worker,invoke}=await fixture(context)
  const scope={source:'elearning',accountId:'school',tool:'mcp__elearning__fetch',method:'fetch',arguments:{course_ids:['one']}}
  const makeSource=(items,stamp)=>({source:'elearning',account_id:'school',items,cursors:{elearning_state:stamp,until:stamp},window_start:'2026-11-05T12:00:00Z'})
  const item={source_id:'one',title:'One',snippet:'One',occurred_at:'2026-11-06T07:00:00Z',payload:{course_id:'one',body:'One'}}
  let calls=0
  async function finish(date,stamp,source,configuration=scope) {
    const input={record:buildBriefing([],date,'',stamp),request_id:randomUUID(),sources:[source],configurations:[configuration],run_at:stamp,submissions:[]}
    while(true) {
      const result=await worker.call('generated_briefing',input)
      if(result.record)return result.record
      calls++
      const request=result.model
      const decisions=request.entries.map(row=>({id:row.id,selected:true,reason:'Keep',section:'fyi',score:80,summary:row.title}))
      const ids=request.schema.properties.decisions ? decisions.map(row=>row.id) : request.schema.properties.source_item_ids.items.enum
      input.submissions.push(request.schema.properties.decisions && !request.schema.properties.reports ? {decisions} :
        { ...(request.schema.properties.decisions ? {decisions} : {}), body:'Report',source_item_ids:ids,reports:ids.map(id=>({id,content:'Report '+id}))})
    }
  }
  await finish('2026-11-06','2026-11-06T08:00:00Z',makeSource([item],'2026-11-06T08:00:00Z'))
  assert.equal(calls,1)
  await invoke('briefing','create_rule',{payload:{text:'Keep concrete deadlines',enabled:true,source:'*'}})
  await finish('2026-11-06','2026-11-06T09:00:00Z',makeSource([],'2026-11-06T09:00:00Z'))
  assert.equal(calls,1,'New rules do not reanalyze existing candidates')
  const before=await worker.call('source_cursors',{source:'elearning',account_id:'school',configuration:scope})
  const changed={...item,snippet:'Changed',payload:{...item.payload,body:'Changed'}}
  const pending=await worker.call('generated_briefing',{record:buildBriefing([],'2026-11-06','','2026-11-06T10:00:00Z'),request_id:randomUUID(),sources:[makeSource([changed],'2026-11-06T10:00:00Z')],configurations:[scope],run_at:'2026-11-06T10:00:00Z'})
  assert(pending.model)
  assert.deepEqual(await worker.call('source_cursors',{source:'elearning',account_id:'school',configuration:scope}),before,'Pending or cancelled model work does not advance cursors')
  const other={...scope,arguments:{course_ids:['two']}}
  const switched=await finish('2026-11-06','2026-11-06T11:00:00Z',makeSource([],'2026-11-06T11:00:00Z'),other)
  assert.equal(switched.business.snapshot.items.length,0,'No candidates restored from another course scope')
  assert.notEqual(switched.body,'Report','Old narrative must not survive a changed course selection')
  const nextDay=await finish('2026-11-07','2026-11-07T08:00:00Z',makeSource([],'2026-11-07T08:00:00Z'),other)
  assert.equal(nextDay.business.snapshot.items.length,0,'Previous day candidates never enter the next day model')
  assert.equal(calls,1,'Empty windows do not call a model')
  assert.deepEqual(await worker.call('source_cursors',{source:'elearning',account_id:'another-school',configuration:{...scope,accountId:'another-school'}}),{})
})


test('briefing source switches persist separately from source scopes and reject stale writes', async t => {
  const { worker, directory, options } = await fixture(t)
  await worker.call('source_settings', { sources: [{ source: 'alcor_tasks', accountId: 'local' }] })
  assert.deepEqual(await worker.call('source_group_settings'), {})
  await worker.call('source_group_settings', { groups: { email: false }, expected_groups: {} })
  await assert.rejects(worker.call('source_group_settings', { groups: { email: true }, expected_groups: {} }), /其他页面变化/)
  assert.deepEqual(await worker.call('source_settings'), [{ source: 'alcor_tasks', accountId: 'local' }])
  await worker.close()
  const restarted = new BusinessWorker(directory, options); t.after(() => restarted.close())
  assert.deepEqual(await restarted.call('source_group_settings'), { email: false })
})
