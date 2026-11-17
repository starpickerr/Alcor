import { Logger } from '@deepseek-ai/cordis'
import { logCapacity, logCategory, logDebugCapacity, logSchemas, logSources } from './log-contract.js'

// Default Cordis logger names come from each native plugin's class name.
const sources = new Map(Object.entries({ secretary: 'Alcor', organizer: 'Alcor', tasks: '事务', calendar: '日程',
  briefing: '简报', bills: '账单', automation: '自动化', apple: 'Apple 同步', 'apple-sync': 'Apple 同步',
  'school-remote': 'eLearning', elearning: 'eLearning', 'mail-remote': '邮件', mail: '邮件', 'agent-hub': 'AgentHub' }))

export function logSource(message) {
  const name = message.name.replace(/^@alcor\//, '').replace(/^alcor-/, '')
  return sources.get(name) ?? null
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

// Only explicit native worker events attest an operation outcome; severity alone does not.
export function projectLog(row) {
  const event = row.message.match(/^\[(worker_operation_succeeded|worker_operation_failed|worker_timeout|worker_start_failed|worker_stopped|worker_write_failed|worker_protocol_error)\]\s*/)
  const outcome = event ? event[1] === 'worker_operation_succeeded' ? 'success' : 'failure' : null
  const operation = event ? row.message.slice(event[0].length).match(/^([a-z_]+(?:\.[a-z_]+)?)(?:\s|$)/)?.[1] ?? null : null
  const duration = event ? row.message.match(/ · 耗时 (\d+) ms · 请求 /)?.[1] : undefined
  const durationMs = duration !== undefined && Number.isSafeInteger(Number(duration)) ? Number(duration) : null
  const requestId = event ? row.message.match(/ · 请求 ([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})$/i)?.[1] ?? null : null
  return { ...row, outcome, operation, durationMs, requestId }
}

export class RuntimeLogs {
  constructor(ctx, table) {
    this.table = table
    const history = table.get('history')?.entries ?? []
    let excessDebug = Math.max(0, history.filter(row => row.level === 'debug').length - logDebugCapacity)
    this.entries = history.filter(row => {
      if (row.level !== 'debug' || excessDebug === 0) return true
      excessDebug--
      return false
    })
      .map(row => ({ ...row, source: row.source === '事务与简报' ? 'Alcor' : row.source, message: redactLog(row.message) }))
    this.serial = history.at(-1)?.id ?? 0
    this.startedAt = new Date().toISOString()
    this.storage = 'saved'
    this.revision = 0
    this.queue = Promise.resolve()
    this.active = true
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
          const oldestDebug = this.entries.findIndex(row => row.level === 'debug')
          this.entries.splice(Math.max(0, oldestDebug), 1)
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
      && (!category || logCategory(row) === category)
      && (!needle || `${row.message} ${row.source}`.toLocaleLowerCase().includes(needle))).reverse()
    const remaining = before ? matches.filter(row => row.id < before) : matches
    const entries = remaining.slice(0, 100)
    return { entries, sources: [...logSources], matched: matches.length,
      nextBefore: remaining.length > 100 ? entries.at(-1).id : null,
      retained: this.entries.length, capacity: logCapacity, startedAt: this.startedAt, storage: this.storage }
  }

  async close() {
    this.active = false
    clearTimeout(this.timer)
    await this.flush()
  }
}
