import { createHash } from 'node:crypto'
import {
  LlmAdapter, LlmError, offloadedImageText, requestImageHandleText, resolveImageAttachmentAccess,
} from '@deepseek-ai/dsh-llm'
import { responseChunks } from './response-stream.js'

const provider = 'alcor-chatgpt'
const namespace = 'dsh'

function wireName(name) {
  if (/^[A-Za-z0-9_-]{1,64}$/.test(name) && !name.startsWith('dshx_')) return name
  const readable = name.replace(/[^A-Za-z0-9_-]+/g, '_').slice(0, 34)
  return 'dshx_' + readable + '_' + createHash('sha256').update(name).digest('base64url').slice(0, 24)
}

function modelInfo(model) {
  const info = { provider, id: model.slug, name: model.display_name || model.slug }
  if (typeof model.description === 'string') info.description = model.description
  if (Array.isArray(model.input_modalities)) {
    info.inputModalities = [...new Set(model.input_modalities.filter(value => value === 'text' || value === 'image'))]
  }
  if (Number.isSafeInteger(model.context_window) && model.context_window > 0) {
    info.context = { contextWindow: model.context_window }
  }
  if (Array.isArray(model.supported_reasoning_levels)) {
    const levels = model.supported_reasoning_levels.filter(level => typeof level?.effort === 'string' && level.effort)
    if (levels.length) {
      info.reasoning = { efforts: levels.map(level => ({ id: level.effort, name: level.effort })) }
      if (levels.some(level => level.effort === model.default_reasoning_level)) {
        info.reasoning.defaultEffort = model.default_reasoning_level
      }
    }
  }
  return info
}

function assistantItems(message, model) {
  const replay = message.source?.provider === provider && message.source.model === model
    ? message.source.replayState?.blocks : undefined
  return message.content.flatMap((block, index) => {
    const meta = replay?.[index]
    if (block.type === 'reasoning') {
      if (typeof meta?.encrypted_content !== 'string') return []
      return [{ type: 'reasoning', id: meta.id, encrypted_content: meta.encrypted_content,
        summary: block.text ? [{ type: 'summary_text', text: block.text }] : [] }]
    }
    if (block.type === 'tool-call') {
      return [{ type: 'function_call', namespace, name: wireName(block.name),
        arguments: block.arguments, call_id: block.id, ...(meta?.id && { id: meta.id }) }]
    }
    if (block.type === 'text') {
      const phase = meta?.phase
      return [{ role: 'assistant', content: block.text, ...(phase === 'commentary' || phase === 'final_answer' ? { phase } : {}) }]
    }
    throw new LlmError('ChatGPT 无法读取这条历史助手消息的内容。', 'UNSUPPORTED_CONTENT')
  })
}

async function inputParts(ctx, blocks, signal) {
  const parts = []
  for (const block of blocks) {
    if (block.type === 'text') {
      parts.push({ type: 'input_text', text: block.text })
    } else if (block.type === 'image') {
      const store = ctx.get('attachments')
      const ref = block.attachment
      const access = store && resolveImageAttachmentAccess(store, path => ctx.get('fs')?.processPathFromHostPath(path), ref)
      if (block.offloaded) {
        parts.push({ type: 'input_text', text: offloadedImageText(ref, access) })
        continue
      }
      if (!store) throw new LlmError('DSH 附件服务未连接，无法读取本轮图片。', 'UNSUPPORTED_CONTENT')
      const file = await store.readImageRequest(ref, { width: ref.width, height: ref.height, maxBytes: ref.bytes }, signal)
      parts.push({ type: 'input_text', text: requestImageHandleText(ref, file, access) })
      parts.push({ type: 'input_image', image_url: `data:${file.mediaType};base64,${Buffer.from(file.data).toString('base64')}` })
    } else {
      throw new LlmError('ChatGPT 无法读取这条消息的内容。', 'UNSUPPORTED_CONTENT')
    }
  }
  return parts
}

async function requestBody(ctx, options) {
  const input = []
  for (const message of options.messages) {
    options.signal?.throwIfAborted()
    if (message.role === 'assistant') {
      input.push(...assistantItems(message, options.model))
      continue
    }
    const parts = await inputParts(ctx, message.content, options.signal)
    if (message.role === 'tool') {
      if (message.isError) parts.unshift({ type: 'input_text', text: 'Tool execution failed.\n' })
      input.push({ type: 'function_call_output', call_id: message.toolCallId, output: parts.length ? parts : '' })
    } else if (parts.length) {
      input.push({ role: message.role === 'system' ? 'developer' : message.role, content: parts })
    }
  }
  const tools = (options.tools ?? []).map(tool => ({
    type: 'function', name: wireName(tool.name), description: tool.description, parameters: tool.parameters, strict: false,
  }))
  return {
    model: options.model, store: false, stream: true, input,
    ...(options.system && { instructions: options.system }),
    ...(options.reasoningEffort && { reasoning: { effort: options.reasoningEffort } }),
    ...(tools.length && { tools: [{ type: 'namespace', name: namespace, description: 'Tools available in this DSH conversation.', tools }] }),
  }
}

export function registerProvider(ctx, account) {
  let catalog
  let generation = new AbortController()
  ctx.on('llm/models-updated', id => {
    if (id === provider) {
      catalog = undefined
      generation.abort(new LlmError('ChatGPT 登录状态已更改，请重试。', 'AUTH'))
      generation = new AbortController()
    }
  })
  async function load(signal) {
    const started = generation
    const models = (await account.listModels({ signal })).map(modelInfo)
    if (generation !== started) throw new LlmError('ChatGPT 登录状态已更改，请重新选择模型。', 'AUTH')
    catalog = new Map(models.map(info => [info.id, info]))
    return models
  }
  class ChatGPTAdapter extends LlmAdapter {
    providerInfo() { return { id: provider, name: 'ChatGPT' } }
    listModels() { return load() }
    async resolveModel(_provider, model, signal) {
      if (!catalog) await load(signal)
      const info = catalog?.get(model)
      if (!info) throw new LlmError('当前 ChatGPT 账号未提供此模型，请检查登录状态并重新选择。', 'UNKNOWN_MODEL')
      return info
    }
    async prepareCall(id, model, signal) {
      const prepared = generation
      const info = await this.resolveModel(id, model, signal)
      return { model: info, stream: options => this.stream(options, prepared) }
    }
    async *stream(options, prepared = generation) {
      // Credential lookup is asynchronous; a change during lookup must also cancel this prepared call.
      const signal = options.signal ? AbortSignal.any([options.signal, prepared.signal]) : prepared.signal
      signal.throwIfAborted()
      const body = await requestBody(ctx, { ...options, signal })
      signal.throwIfAborted()
      const names = new Map((options.tools ?? []).map(tool => [wireName(tool.name), tool.name]))
      let response
      try {
        response = await account.respond(body, { signal })
      } catch (error) {
        if (!Number.isInteger(error?.status) || error.status < 100 || error.status > 599) throw error
        throw new LlmError(error.message, error.code ?? `HTTP_${error.status}`, {
          status: error.status, ...(error.requestId && { requestId: error.requestId }),
        })
      }
      try {
        yield* responseChunks(response, namespace, names, signal)
      } catch (error) {
        if (typeof error?.code === 'string') await account.invalidateResponse(response, error.code)
        throw error
      }
    }
  }
  return ctx.llm.registerAdapter([provider], new ChatGPTAdapter())
}
