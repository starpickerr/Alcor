import assert from 'node:assert/strict'
import { test } from 'node:test'
import { Context } from '@deepseek-ai/cordis'
import LlmRuntime, { BlockAssembler } from '@deepseek-ai/dsh-llm'
import { registerProvider } from '../../packages/alcor-chatgpt/src/provider.js'

const provider = 'alcor-chatgpt'
const model = 'test-plan-model'
const options = { provider, model, messages: [{ role: 'user', content: [{ type: 'text', text: '查询课程' }] }] }
const textItem = (text, phase = 'final_answer') => ({
  type: 'message', id: 'msg_1', role: 'assistant', phase, content: [{ type: 'output_text', text }],
})
const complete = (output, usage) => ({ type: 'response.completed', response: { status: 'completed', output, usage } })
const added = (output_index, item) => ({ type: 'response.output_item.added', output_index, item })
const originalTools = [{ name: 'school.lookup', description: '查课程', parameters: { type: 'object' } }]

function sse(...events) {
  const bytes = new TextEncoder().encode(events.map(event => ': keepalive\r\ndata: ' + JSON.stringify(event) + '\r\n\r\n').join(''))
  return new Response(new ReadableStream({
    start(controller) {
      for (let start = 0; start < bytes.length; start += 7) controller.enqueue(bytes.slice(start, start + 7))
      controller.close()
    },
  }))
}

function setup(respond, services = {}) {
  const calls = []
  const failures = []
  const models = [{ slug: model, display_name: 'Plan Model', context_window: 16000,
    input_modalities: ['text', 'image'], supported_reasoning_levels: [{ effort: 'high' }], default_reasoning_level: 'high' }]
  const listeners = new Map()
  let adapter
  const ctx = {
    on: (name, fn) => listeners.set(name, fn),
    get: name => services[name],
    llm: { registerAdapter(routes, value) { assert.deepEqual(routes, [provider]); adapter = value } },
  }
  registerProvider(ctx, {
    listModels: async () => models,
    respond: async (body, init) => { calls.push(body); return respond(body, init) },
    invalidateResponse: async (response, code) => failures.push({ response, code }),
  })
  return { adapter, calls, failures, models, changed: () => listeners.get('llm/models-updated')(provider) }
}

async function generate(adapter, request = options) {
  const call = await adapter.prepareCall(provider, model, request.signal)
  const assembly = new BlockAssembler()
  const chunks = []
  for await (const chunk of call.stream(request)) { assembly.push(chunk); chunks.push(chunk) }
  return { assembly, chunks }
}

test('native model catalog follows account visibility and invalidates prepared calls on account change', async () => {
  const { adapter, models, changed, calls } = setup(() => { throw new Error('unexpected dispatch') })
  assert.deepEqual(adapter.providerInfo(), { id: provider, name: 'ChatGPT' })
  assert.equal((await adapter.listModels())[0].name, 'Plan Model')
  const old = await adapter.prepareCall(provider, model)
  assert.deepEqual(old.model.context, { contextWindow: 16000 })
  assert.equal(old.model.reasoning.defaultEffort, 'high')
  models.length = 0
  changed()
  assert.deepEqual(await adapter.listModels(), [])
  await assert.rejects(Array.fromAsync(old.stream(options)), /登录状态已更改/)
  await assert.rejects(adapter.prepareCall(provider, model), /未提供此模型/)
  assert.equal(calls.length, 0)
})

test('fragmented SSE delivers Chinese text, terminal usage and the native ChatGPT conversation reply', async () => {
  const item = textItem('查到了')
  const { adapter, calls } = setup(() => sse(added(0, textItem('')),
    { type: 'response.output_text.delta', output_index: 0, delta: '查到' },
    { type: 'response.output_text.delta', output_index: 0, delta: '了' },
    complete([item], { input_tokens: 24, output_tokens: 8, total_tokens: 32,
      input_tokens_details: { cached_tokens: 10 }, output_tokens_details: { reasoning_tokens: 3 } })))
  const { assembly, chunks } = await generate(adapter, { ...options, system: '一次性指令', temperature: 0.8, maxTokens: 512,
    reasoningEffort: 'high', messages: [{ role: 'system', content: [{ type: 'text', text: 'DSH 提示' }] }, ...options.messages] })
  assert.deepEqual(chunks.filter(c => c.type === 'text-delta').map(c => c.text), ['查到', '了'])
  assert.deepEqual(assembly.blocks(), [{ type: 'text', text: '查到了' }])
  assert.deepEqual(assembly.finish, { kind: 'stop' })
  assert.deepEqual(assembly.usage, { inputTokens: 14, outputTokens: 8, cacheReadTokens: 10, totalTokens: 32, reasoningTokens: 3 })
  assert.deepEqual(calls[0], { model, stream: true, store: false, instructions: '一次性指令', reasoning: { effort: 'high' },
    input: [{ role: 'developer', content: [{ type: 'input_text', text: 'DSH 提示' }] },
      { role: 'user', content: [{ type: 'input_text', text: '查询课程' }] }] })
})

test('native parallel tool calls round-trip ids, inputs, approval denial and encrypted reasoning', async () => {
  const reasoning = { type: 'reasoning', id: 'rs_1', summary: [], encrypted_content: 'synthetic-opaque-content' }
  const { adapter, calls } = setup(body => {
    if (body.input.some(item => item.type === 'function_call_output')) return sse(complete([textItem('已处理')]))
    const tool = (id, args) => ({ type: 'function_call', id: 'fc_' + id, namespace: 'dsh',
      name: body.tools[0].tools[0].name, call_id: id, arguments: args })
    return sse(added(0, reasoning), added(1, { ...textItem('', 'commentary'), content: [] }),
      added(2, tool('call_a', '')), added(3, tool('call_b', '')),
      { type: 'response.function_call_arguments.delta', output_index: 3, delta: '{"b":2}' },
      { type: 'response.function_call_arguments.delta', output_index: 2, delta: '{"a":1}' },
      complete([reasoning, textItem('正在查', 'commentary'), tool('call_a', '{"a":1}'), tool('call_b', '{"b":2}')]))
  })
  const { assembly } = await generate(adapter, { ...options, tools: originalTools })
  assert.deepEqual(assembly.finish, { kind: 'tool-calls' })
  assert.deepEqual(assembly.blocks().slice(2), [
    { type: 'tool-call', name: 'school.lookup', id: 'call_a', arguments: '{"a":1}' },
    { type: 'tool-call', name: 'school.lookup', id: 'call_b', arguments: '{"b":2}' },
  ])
  const assistant = { role: 'assistant', content: assembly.blocks(), source: { provider, model, replayState: assembly.replayState } }
  const followup = await generate(adapter, { ...options, tools: originalTools, messages: [...options.messages, assistant,
    { role: 'tool', toolCallId: 'call_a', content: [{ type: 'text', text: '数学' }] },
    { role: 'tool', toolCallId: 'call_b', isError: true, content: [{ type: 'text', text: '用户拒绝操作' }] }] })
  assert.deepEqual(followup.assembly.blocks(), [{ type: 'text', text: '已处理' }])
  const [{ tools }] = calls[0].tools
  assert.equal(tools[0].strict, false)
  assert.deepEqual(tools[0].parameters, originalTools[0].parameters)
  assert.match(tools[0].name, /^[\w-]{1,64}$/)
  assert.deepEqual(calls[1].input.slice(1), [reasoning, { role: 'assistant', phase: 'commentary', content: '正在查' },
    { type: 'function_call', id: 'fc_call_a', namespace: 'dsh', name: tools[0].name, call_id: 'call_a', arguments: '{"a":1}' },
    { type: 'function_call', id: 'fc_call_b', namespace: 'dsh', name: tools[0].name, call_id: 'call_b', arguments: '{"b":2}' },
    { type: 'function_call_output', call_id: 'call_a', output: [{ type: 'input_text', text: '数学' }] },
    { type: 'function_call_output', call_id: 'call_b', output: [{ type: 'input_text', text: 'Tool execution failed.\n' },
      { type: 'input_text', text: '用户拒绝操作' }] }])
})

test('rejects unknown tools, unexpected stream closure, and quota failure after text deltas', async () => {
  const secret = 'synthetic-private-error'
  for (const [events, pattern] of [
    [[complete([{ type: 'function_call', namespace: 'dsh', name: 'unadvertised', arguments: '{}', call_id: 'call_x' }])], /无法识别/],
    [[added(0, textItem('')), { type: 'response.output_text.delta', output_index: 0, delta: '未完成' }], /关闭/],
    [[added(0, textItem('')), { type: 'response.output_text.delta', output_index: 0, delta: '未完成' },
      { type: 'response.failed', response: { error: { code: 'subscription_sharing_usage_limit_exceeded', message: secret } } }], /额度|用量/],
  ]) {
    const { adapter } = setup(() => sse(...events))
    await assert.rejects(generate(adapter, { ...options, tools: originalTools }), err => {
      assert.match(err.message, pattern); assert.ok(!err.message.includes(secret)); return true
    })
  }
})

test('native cancellation closes an idle subscription stream and prevents a pre-cancelled request', async () => {
  let cancel
  const upstreamCancelled = new Promise(resolve => { cancel = resolve })
  const controller = new AbortController()
  const { adapter, calls } = setup((_body, { signal }) => {
    assert.ok(signal instanceof AbortSignal && !signal.aborted)
    return new Response(new ReadableStream({
      start(stream) {
        const event = added(0, textItem(''))
        stream.enqueue(new TextEncoder().encode('data: ' + JSON.stringify(event) + '\n\n'))
      },
      cancel,
    }))
  })
  const call = await adapter.prepareCall(provider, model)
  const stream = call.stream({ ...options, signal: controller.signal })
  assert.equal((await stream.next()).value.type, 'block-start')
  const pending = stream.next()
  controller.abort()
  await assert.rejects(pending, { name: 'AbortError' })
  await upstreamCancelled
  await assert.rejects(generate(adapter, { ...options, signal: controller.signal }), { name: 'AbortError' })
  assert.equal(calls.length, 1)
})

test('DSH dispatch resolves only this route, projects native tools and removes the adapter on unload', async t => {
  const ctx = new Context()
  t.after(() => ctx.fiber.dispose())
  await ctx.plugin(LlmRuntime)
  let body
  const plugin = ctx.plugin({
    inject: ['llm'],
    apply(pluginCtx) {
      registerProvider(pluginCtx, {
        listModels: async () => [{ slug: model, display_name: '本账号模型' }],
        respond: async request => { body = request; return sse(complete([textItem('好')])) },
      })
    },
  })
  await plugin
  assert.deepEqual(ctx.llm.listProviders(), [{ id: provider, name: 'ChatGPT' }])
  const [listed] = await ctx.llm.listModels(provider)
  assert.equal(listed.name, '本账号模型')
  const assembly = new BlockAssembler()
  for await (const chunk of ctx.llm.stream({ ...options, tools: originalTools })) assembly.push(chunk)
  assert.deepEqual(assembly.blocks(), [{ type: 'text', text: '好' }])
  assert.equal(body.tools[0].type, 'namespace')
  await plugin.dispose()
  assert.deepEqual(ctx.llm.listProviders(), [])
})

test('reads user and tool images through the DSH attachment service and honors offload decisions', async () => {
  let reads = 0
  const ref = { attachmentId: 'sha256:synthetic', name: '图.png', bytes: 4, width: 1, height: 1, mediaType: 'image/png' }
  const store = { imageHostPath: () => undefined, async readImageRequest(input, target) {
    assert.deepEqual(input, ref)
    assert.deepEqual(target, { width: 1, height: 1, maxBytes: 4 })
    reads++
    return { ...target, data: Uint8Array.of(1, 2, 3, 4), mediaType: 'image/png' }
  } }
  const { adapter, calls } = setup(() => sse(complete([textItem('图片')])), { attachments: store })
  const content = [{ type: 'image', attachment: ref }, { type: 'image', attachment: ref, offloaded: true }]
  await generate(adapter, { ...options, messages: [{ role: 'user', content }, { role: 'tool', toolCallId: 'c', content }] })
  assert.equal(reads, 2)
  const image = { type: 'input_image', image_url: 'data:image/png;base64,AQIDBA==' }
  assert.deepEqual(calls[0].input[0].content[1], image)
  assert.deepEqual(calls[0].input[1].output[1], image)
  assert.match(calls[0].input[0].content[2].text, /omitted/)
})

test('an account change during asynchronous credential lookup cancels the old conversation before transport', async () => {
  let dispatched = false
  const { adapter, changed } = setup(async (_body, { signal }) => {
    changed()
    await Promise.resolve()
    signal.throwIfAborted()
    dispatched = true
  })
  await assert.rejects(generate(adapter), /登录状态已更改/)
  assert.equal(dispatched, false)
})

test('a rejected permission in a successful HTTP stream reaches the matching account and preserves its request ID', async () => {
  const upstream = sse({ type: 'error', code: 'chatpass_v2_scope_not_authorized' })
  upstream.headers.set('x-request-id', 'req_fixture')
  const { adapter, failures } = setup(() => upstream)
  await assert.rejects(generate(adapter), err => {
    assert.equal(err.code, 'chatpass_v2_scope_not_authorized')
    assert.equal(err.failure.requestId, 'req_fixture')
    return true
  })
  assert.deepEqual(failures, [{ response: upstream, code: 'chatpass_v2_scope_not_authorized' }])
})
