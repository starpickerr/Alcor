import { LlmError } from '@deepseek-ai/dsh-llm'
import { EventSourceParserStream } from 'eventsource-parser/stream'
import { publicError } from './account-errors.js'

function invalidStream() {
  return new LlmError('ChatGPT 返回了不完整或无法识别的响应。', 'INVALID_RESPONSE')
}

function upstreamFailure(code, requestId) {
  const details = requestId ? { requestId } : undefined
  if (code === 'context_length_exceeded') {
    return new LlmError('对话超过此模型的上下文长度。', 'CONTEXT_WINDOW_EXCEEDED', details)
  }
  const error = publicError(code)
  return new LlmError(error.message, error.code ?? 'PROVIDER_ERROR', details)
}

function readBlock(item, namespace, names) {
  switch (item?.type) {
    case 'message': {
      if (item.role !== 'assistant' || !Array.isArray(item.content)) throw invalidStream()
      const text = item.content.map(part => {
        if (part.type === 'output_text' && typeof part.text === 'string') return part.text
        if (part.type === 'refusal' && typeof part.refusal === 'string') return part.refusal
        throw invalidStream()
      }).join('')
      return { type: 'text', text }
    }
    case 'reasoning': {
      if (!Array.isArray(item.summary)) throw invalidStream()
      const text = item.summary.map(part => {
        if (part.type !== 'summary_text' || typeof part.text !== 'string') throw invalidStream()
        return part.text
      }).join('\n')
      return { type: 'reasoning', text }
    }
    case 'function_call': {
      const name = names.get(item.name)
      if (item.namespace !== namespace || !name || typeof item.call_id !== 'string' || !item.call_id
        || typeof item.arguments !== 'string') throw invalidStream()
      return { type: 'tool-call', name, id: item.call_id, arguments: item.arguments }
    }
    default: throw invalidStream()
  }
}

function usageChunk(usage) {
  if (!Number.isSafeInteger(usage?.input_tokens) || usage.input_tokens < 0
    || !Number.isSafeInteger(usage.output_tokens) || usage.output_tokens < 0) return
  const cached = usage.input_tokens_details?.cached_tokens
  const cacheReadTokens = Number.isSafeInteger(cached) && cached >= 0 && cached <= usage.input_tokens ? cached : 0
  const total = usage.input_tokens + usage.output_tokens
  const reasoning = usage.output_tokens_details?.reasoning_tokens
  return { type: 'usage', usage: {
    inputTokens: usage.input_tokens - cacheReadTokens, outputTokens: usage.output_tokens, cacheReadTokens,
    ...(Number.isSafeInteger(total) && (usage.total_tokens == null || usage.total_tokens === total) && { totalTokens: total }),
    ...(Number.isSafeInteger(reasoning) && reasoning >= 0 && reasoning <= usage.output_tokens && { reasoningTokens: reasoning }),
  } }
}

export async function* responseChunks(response, namespace, names, signal) {
  if (!response.ok || !response.body) throw invalidStream()
  const blocks = new Map()
  function* start(index, block) {
    if (!Number.isSafeInteger(index) || index < 0) throw invalidStream()
    const known = blocks.get(index)
    if (known) {
      if (known.type !== block.type || known.id !== block.id || known.name !== block.name) throw invalidStream()
      return
    }
    blocks.set(index, { type: block.type, id: block.id, name: block.name, streamed: false })
    yield { type: 'block-start', index, blockType: block.type }
    if (block.type === 'tool-call') {
      yield { type: 'tool-call-delta', index, id: block.id, name: block.name, argumentsDelta: '' }
    }
  }
  function* delta(index, type, text) {
    const known = blocks.get(index)
    if (!known || known.type !== type || typeof text !== 'string') throw invalidStream()
    if (!text) return
    known.streamed = true
    if (type === 'tool-call') yield { type: 'tool-call-delta', index, id: known.id, argumentsDelta: text }
    else yield { type: `${type}-delta`, index, text }
  }
  const events = response.body.pipeThrough(new TextDecoderStream(), { signal })
    .pipeThrough(new EventSourceParserStream(), { signal })
  for await (const { data } of events) {
    signal?.throwIfAborted()
    if (data === '[DONE]') break
    let event
    try { event = JSON.parse(data) } catch { throw invalidStream() }
    const index = event?.output_index
    switch (event?.type) {
      case 'response.output_item.added':
        yield* start(index, readBlock(event.item, namespace, names))
        break
      case 'response.output_text.delta':
      case 'response.refusal.delta':
        yield* delta(index, 'text', event.delta)
        break
      case 'response.reasoning_summary_text.delta':
        yield* delta(index, 'reasoning', event.delta)
        break
      case 'response.function_call_arguments.delta':
        yield* delta(index, 'tool-call', event.delta)
        break
      case 'error':
      case 'response.failed':
        throw upstreamFailure(event.response?.error?.code ?? event.code, response.headers.get('x-request-id'))
      case 'response.incomplete':
        if (event.response?.incomplete_details?.reason !== 'max_output_tokens') {
          throw new LlmError('ChatGPT 响应未完成。', 'INCOMPLETE_RESPONSE')
        }
        yield { type: 'finish', reason: { kind: 'max-tokens' } }
        return
      case 'response.completed': {
        const output = event.response?.output
        if (!Array.isArray(output) || event.response.status !== 'completed'
          || [...blocks.keys()].some(index => index >= output.length)) throw invalidStream()
        const calls = new Set()
        const metadata = []
        let hasOutput = false
        for (const [index, item] of output.entries()) {
          const block = readBlock(item, namespace, names)
          if (block.type === 'tool-call') {
            if (calls.has(block.id)) throw invalidStream()
            calls.add(block.id)
          }
          if (block.type === 'tool-call' || (block.type === 'text' && block.text.trim())) hasOutput = true
          yield* start(index, block)
          if (!blocks.get(index).streamed) yield* delta(index, block.type, block.arguments ?? block.text)
          yield { type: 'block-end', index, block }
          // Stateless Responses carries encrypted reasoning and message phases across DSH tool rounds.
          metadata[index] = {
            ...(item.id && { id: item.id }),
            ...(block.type === 'reasoning' && item.encrypted_content && { encrypted_content: item.encrypted_content }),
            ...(block.type === 'text' && item.phase && { phase: item.phase }),
          }
        }
        if (!hasOutput) throw new LlmError('ChatGPT 返回了空响应。', 'EMPTY_RESPONSE')
        const usage = usageChunk(event.response.usage)
        if (usage) yield usage
        yield { type: 'finish', reason: { kind: calls.size ? 'tool-calls' : 'stop' },
          replayState: { response: null, blocks: [...blocks.keys()].map(index => metadata[index]) } }
        return
      }
    }
  }
  signal?.throwIfAborted()
  throw new LlmError('ChatGPT 连接在确认响应完成前已关闭。', 'STREAM_CLOSED')
}
