import { readFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
import { createHash } from 'node:crypto'
import { isDeepStrictEqual } from 'node:util'
import { Context } from '@deepseek-ai/cordis'
import * as Jsonl from '@deepseek-ai/dsh-session-persistence-jsonl'
import { Session, SESSION_FORMAT_VERSION } from '@deepseek-ai/dsh-session'
import { createUserMessage, createAssistantMessage } from '@deepseek-ai/dsh-llm'

const timestamp = value => {
  const date = typeof value === 'number' ? Math.round(value * 1000) : Date.parse(value)
  if (!Number.isSafeInteger(date) || date < 0) throw new Error('Invalid historical timestamp')
  return date
}

export function legacySession(record, cwd) {
  const id = 'legacy-stella-' + createHash('sha256').update(record.session_id).digest('hex').slice(0, 32)
  const session = Session.create(id, undefined, { version: SESSION_FORMAT_VERSION, id, createdAt: timestamp(record.created_at), cwd, isSeeded: false, agentPreset: 'stella' })
  const times = []
  const append = (type, data, time, surface = false) => {
    session.append(type, data, ...(surface ? [{surfaceOp:'append'}] : []))
    times.push(time)
  }
  append('session/title', { title: record.title?.trim() || '旧版 Stella 对话', messageSeqs: [], source: { kind: 'user' } }, timestamp(record.created_at))
  let turn = 0, step = 0, toolRecords = 0, open = false, lastTime = timestamp(record.created_at)
  for (const row of record.messages) {
    if (!['user', 'assistant'].includes(row.role)) throw new Error('Unsupported historical message role')
    const blocks = JSON.parse(row.content)
    if (!Array.isArray(blocks)) throw new Error('Unsupported historical message content')
    const toolOnly = blocks.length > 0 && blocks.every(block => block.type === 'tool_result')
    const content = blocks.map(block => {
      if (block.type === 'text' && typeof block.text === 'string') return block.text
      if (!['tool_use', 'tool_result'].includes(block.type)) throw new Error('Unsupported historical block; keep the snapshot for recovery')
      toolRecords++
      return '【旧版工具历史记录，不执行】\n' + JSON.stringify(block, null, 2)
    }).join('\n')
    const role = toolOnly ? 'assistant' : row.role
    const time = timestamp(row.created_at)
    if (role === 'user' || !open) {
      if (open) append('turn/end', {turn, reason:{kind:'completed'}}, lastTime)
      turn++; step = 0; open = true
      append('turn/start', {turn}, time)
    }
    if (role === 'user') {
      append('user/message', createUserMessage({ content: [{ type: 'text', text: content }], source: { kind: 'user' } }), time, true)
    } else {
      step++
      append('step/start', {turn,step}, time)
      append('assistant/message', { turn, step, message: createAssistantMessage({ content: [{ type: 'text', text: content }], source: { provider: 'legacy-stella', model: 'historical-transcript' } }), stream: [] }, time, true)
      append('step/end', {turn,step}, time)
    }
    lastTime = time
  }
  if (open) append('turn/end', {turn,reason:{kind:'completed'}},lastTime)
  return { header: session.header, events: session.log.slice().map((event, index) => ({ ...event, time: times[index] })), toolRecords }
}

const stableEvent = event => {
  const data = {...event.data}
  if (event.type === 'user/message') delete data.id
  if (event.type === 'assistant/message') {data.message = {...data.message}; delete data.message.id}
  return {type:event.type,seq:event.seq,time:event.time,surfaceOp:event.surfaceOp,data}
}

export async function importHistory(records, root, cwd) {
  const ctx = new Context()
  let imported = 0, messages = 0, toolRecords = 0
  try {
    await ctx.plugin(Jsonl, Jsonl.Config({ root }))
    for (const record of records) {
      const data = legacySession(record, cwd)
      const existing = await ctx.sessionPersistence.stat(data.header.id)
      if (existing) {
        const handle = await ctx.sessionPersistence.open(data.header.id, 'read')
        try {
          const { events } = await handle.read()
          // A retry must verify the whole imported transcript before skipping it.
          if (existing.header.cwd !== data.header.cwd || existing.header.createdAt !== data.header.createdAt || !isDeepStrictEqual(events.map(stableEvent),data.events.map(stableEvent))) throw new Error('Historical session collision')
        } finally { await handle.close() }
      } else {
        const handle = await ctx.sessionPersistence.create(data.header)
        try { await handle.append(data.events); await handle.flush() } finally { await handle.close() }
        imported++
      }
      const verify = await ctx.sessionPersistence.open(data.header.id, 'read')
      try {
        const { events } = await verify.read()
        if (events.filter(e => ['user/message', 'assistant/message'].includes(e.type)).length !== record.messages.length) throw new Error('Historical message count differs')
      } finally { await verify.close() }
      messages += record.messages.length
      toolRecords += data.toolRecords
    }
  } finally { await ctx.fiber.dispose() }
  return { sessions: records.length, imported, messages, toolRecords }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [file, root, cwd] = process.argv.slice(2)
  if (!file || !root || !cwd) throw new Error('Usage: migrate-legacy-history.mjs <offline-history.json> <session-root> <workspace>')
  console.log(JSON.stringify(await importHistory(JSON.parse(await readFile(file, 'utf8')), root, cwd)))
}
