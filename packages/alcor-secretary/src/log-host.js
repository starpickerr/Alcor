import { checkMessages as mailErrors } from '../../../../dsh-email/plugin/src/check-status.js'
import { checkMessages as schoolErrors } from '../../alcor-plugin-info/src/elearning-status.js'
import { reportHostFailure } from '../../alcor-organizer/src/worker-diagnostic.js'
import { recordAgentFailure, recordGenericToolFailure } from './chat-diagnostics.js'
import { Logger } from '@deepseek-ai/cordis'
import { logCapacity, logCategory, logDebugCapacity, logSchemas, logSources } from './log-contract.js'

// Default Cordis logger names come from each native plugin's class name.
const sources = new Map(Object.entries({ secretary: 'Alcor', organizer: 'Alcor', tasks: '事务', calendar: '日程',
  briefing: '简报', bills: '账单', automation: '自动化', apple: 'Apple 同步', 'apple-sync': 'Apple 同步',
  'school-remote': 'eLearning', elearning: 'eLearning', 'mail-remote': '邮件', 'mail-connection': '邮件', mail: '邮件', 'agent-hub': 'AgentHub' }))
const legacySources = new Map([
  ['事务与简报', 'Alcor'],
  ['Alcor：AgentHub', 'AgentHub'],
  ...logSources.map(source => [source.startsWith('Alcor：') ? source.slice('Alcor：'.length) : `Alcor：${source}`, source]),
])

export function logSource(message) {
  const name = message.name.replace(/^@alcor\//, '').replace(/^alcor-/, '')
  const business = sources.get(name)
  if (business) return legacySources.get(business) ?? business
  return redactLog(message.name).slice(0, 100) || 'DSH'
}

export function redactLog(text) {
  return text.replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, '')
    .replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi, '$1[已隐藏]@')
    .replace(/\bBearer\s+[^\s"',;]+/gi, 'Bearer [已隐藏]')
    .replace(/(["']?(?:password|passwd|pwd|token|access_token|refresh_token|api[_-]?key|authorization|cookie|secret)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)/gi, '$1[已隐藏]')
    .replace(/\bsk-[A-Za-z0-9_-]+/g, '[已隐藏]')
    .replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi, '[邮箱已隐藏]')
    .replace(/([?&][^=\s]+)=([^&#\s]*)/g, '$1=[已隐藏]')
    .replace(/(?:\/Users\/|\/home\/)[^/\s]+|[A-Z]:\\Users\\[^\\\s]+/gi, '[用户目录]')
}

// MCP errors may contain mail bodies, credentials or server responses. Resolve only known codes.
export function recordSourceFailure(ctx, execution, result) {
  const match = /^mcp__(email|elearning)__([a-z_]{1,80})$/.exec(execution.name)
  if (!match) return recordGenericToolFailure(ctx, execution, result)
  const value = result.value?.structuredContent?.value
  const mailCheck = execution.name === 'mcp__email__test_connection'
  if (!result.isError && !(mailCheck && (value?.connected === false || value?.folder_accessible === false))) return
  const messages = match[1] === 'email' ? mailErrors : schoolErrors
  let code = value?.error_code
  if (!code && typeof result.error?.message === 'string') {
    try { code = JSON.parse(result.error.message)?.error_code } catch { /* Unstructured remote response. */ }
  }
  if (mailCheck && value?.connected === true && value?.folder_accessible === false) code = 'folder_denied'
  if (typeof code !== 'string' || !Object.hasOwn(messages, code)) code = 'query_failed'
  reportHostFailure(ctx, match[1] === 'email' ? 'mail' : 'elearning', match[1] + '.' + match[2],
    { code }, messages[code], execution.callId)
}

const reservedPerIncidentLevel = 100

// Keep recent incidents findable among routine requests without increasing stored history.
function evictionIndex(entries) {
  const debug = entries.findIndex(row => row.level === 'debug')
  if (debug >= 0) return debug
  if (entries[0].level === 'info') return 0
  const totals = { error: 0, warn: 0 }
  for (const { level } of entries) if (Object.hasOwn(totals, level)) totals[level]++
  return entries.findIndex(({ level }) => level === 'info' || totals[level] > reservedPerIncidentLevel)
}

// Only explicit native worker events attest an operation outcome; severity alone does not.
export function projectLog(row) {
  const [header, ...details] = row.message.split('\n')
  const event = header.match(/^\[(worker_operation_succeeded|business_operation_failed|worker_operation_failed|worker_timeout|worker_start_failed|worker_stopped|worker_write_failed|worker_protocol_error)\]\s*/)
  const outcome = event ? event[1] === 'worker_operation_succeeded' ? 'success' : 'failure' : null
  const operation = event ? header.slice(event[0].length).match(/^([a-z_]+(?:\.[a-zA-Z_]+)?)(?:\s|$)/)?.[1] ?? null : null
  const duration = event ? header.match(/ · 耗时 (\d+) ms · 请求 /)?.[1] : undefined
  const durationMs = duration !== undefined && Number.isSafeInteger(Number(duration)) ? Number(duration) : null
  const requestId = event ? header.match(/ · 请求 ([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})$/i)?.[1] ?? null : null
  const code = outcome === 'failure' ? header.match(/ · ([A-Za-z_][\w-]{0,63})(?: · |$)/)?.[1] ?? event[1] : null
  const diagnostic = code ? { code, reason: details.find(line => line.startsWith('原因：'))?.slice(3) ?? null,
    businessCode: details.find(line => line.startsWith('业务错误：'))?.slice(5) ?? null,
    systemCode: details.find(line => line.startsWith('系统错误：'))?.slice(5) ?? null,
    locations: details.filter(line => line.startsWith('位置：')).slice(0, 8).map(line => line.slice(3)) } : undefined
  return { ...row, outcome, operation, durationMs, requestId, ...(diagnostic ? { diagnostic } : {}) }
}

export class RuntimeLogs {
  constructor(ctx, table) {
    this.table = table
    const history = (table.get('history')?.entries ?? []).map(row => {
      // Old builds promoted expected Apple existence probes to errors before their caller handled them.
      const expectedMissing = row.level === 'error'
        && /^\[worker_operation_failed\] (?:calendar.get_event|tasks.get_todo) · KeyError · /.test(row.message)
        && /^原因：(?:event not found|todo not found)$/m.test(row.message)
        && /^位置：worker.py:\d+ in apple_peer$/m.test(row.message)
      return expectedMissing ? { ...row, level: 'debug' } : row
    })
    let excessDebug = Math.max(0, history.filter(row => row.level === 'debug').length - logDebugCapacity)
    this.entries = history.filter(row => {
      if (row.level !== 'debug' || excessDebug === 0) return true
      excessDebug--
      return false
    })
      .map(row => ({ ...row, source: legacySources.get(row.source) ?? row.source, message: redactLog(row.message) }))
    this.serial = history.at(-1)?.id ?? 0
    this.startedAt = new Date().toISOString()
    this.storage = 'saved'
    this.revision = 0
    this.queue = Promise.resolve()
    this.active = true
    ctx.on('tools/result', (execution, result) => {
      try { if (this.active) recordSourceFailure(ctx, execution, result) } catch { /* Keep the original tool result intact. */ }
    })
    ctx.on('agent/error', event => {
      try { if (this.active) recordAgentFailure(ctx, this.entries, event) } catch { /* Keep the original agent failure intact. */ }
    })
    ctx.logger.exporter({ colors: false, levels: { default: 3 }, export: message => {
      // A broken diagnostic sink must never interrupt a business operation.
      try {
        const source = logSource(message)
        if (!this.active || !source) return
        const formatted = Logger.format({ colors: false, maxLength: 4000 }, message)
        this.entries.push({ id: ++this.serial, time: new Date(message.ts).toISOString(),
          level: message.type, source, message: redactLog(formatted).slice(0, 4000) })
        const debugOverflow = message.type === 'debug' && this.entries.filter(row => row.level === 'debug').length > logDebugCapacity
        if (debugOverflow || this.entries.length > logCapacity) {
          this.entries.splice(evictionIndex(this.entries), 1)
        }
        this.revision++
        this.storage = 'pending'
        this.timer ??= setTimeout(() => { this.timer = undefined; void this.flush() }, 1000)
        this.timer.unref?.()
      } catch { /* Formatting an unsupported log value is best effort. */ }
    } })
  }

  flush() {
    this.queue = this.queue.then(async () => {
      const revision = this.revision
      try {
        await this.table.put('history', { entries: this.entries.map(row => ({ ...row })) })
        this.storage = revision === this.revision ? 'saved' : 'pending'
      } catch { this.storage = 'failed' }
    })
    return this.queue
  }

  list(request) {
    const { query, level, outcome, category, source, before, since } = logSchemas[0].parse(request)
    const needle = query.toLocaleLowerCase()
    const matches = this.entries.map(projectLog).filter(row => (!since || row.time >= since)
      && (level === 'all' || (level === 'normal' ? row.level !== 'debug' : row.level === level))
      && (outcome === 'all' || row.outcome === outcome) && (!source || row.source === source)
      && (!category || (category === 'error' ? row.level === 'error' : logCategory(row) === category))
      && (!needle || `${row.message} ${row.source}`.toLocaleLowerCase().includes(needle))).reverse()
    const remaining = before ? matches.filter(row => row.id < before) : matches
    const entries = remaining.slice(0, 100)
    return { entries, sources: [...new Set([...logSources, ...this.entries.map(row => row.source)])], matched: matches.length,
      nextBefore: remaining.length > 100 ? entries.at(-1).id : null,
      retained: this.entries.length, capacity: logCapacity, startedAt: this.startedAt, storage: this.storage }
  }

  async close() {
    this.active = false
    clearTimeout(this.timer)
    await this.flush()
  }
}
