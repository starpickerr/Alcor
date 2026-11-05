import { readBriefingSourcePage } from './briefing-sources.js'

export class BriefingGenerator {
  constructor(ctx, worker) { this.ctx = ctx; this.worker = worker; this.active = new Set(); this.closed = false; this.progress = { phase: 'idle' } }

  status() { return this.progress }
  cancel() {
    for (const state of this.active) state.controller.abort(new Error('简报生成已取消。'))
    return this.status()
  }

  async collect(config, since, until, signal, authorize = async () => {}) {
    try {
      if (config.source === 'calendar_agenda') return this.worker.call('calendar_agenda', { run_at: until })
      if (['stella_tasks', 'stella_bills'].includes(config.source)) return this.worker.call('local_source', { source: config.source, run_at: until })
      const rows = []
      const errors = []
      const cursors = {}
      const paged = ['fetch', 'briefing_fetch'].includes(config.method)
      const saved = paged ? {} : await this.worker.call('source_cursors', { source: config.source, account_id: config.accountId })
      let offset = 0
      const seen = new Set()
      while (true) {
        if (signal.aborted) throw signal.reason
        await authorize()
        const value = await readBriefingSourcePage(this.ctx, config, { since, until, offset, cursors: saved, signal })
        const items = value.items ?? value.source_items
        rows.push(...items)
        errors.push(...(value.errors ?? []))
        Object.assign(cursors, value.cursors)
        if (!paged || value.next_offset === null || value.next_offset === undefined) {
          return { source: config.source, account_id: config.accountId, items: rows, cursors: Object.keys(cursors).length ? cursors : { until }, errors, window_start: since }
        }
        if (!Number.isSafeInteger(value.next_offset) || value.next_offset <= offset || seen.has(value.next_offset)) throw new Error('来源分页没有前进。')
        if (seen.size >= 1000) throw new Error('来源分页过多，请缩小范围后重试。')
        offset = value.next_offset; seen.add(offset)
      }
    } catch (error) {
      if (signal.aborted) throw signal.reason
      return { source: config.source, account_id: config.accountId, items: [], errors: [error.message], window_start: since }
    }
  }

  async generate({ record, requestId, parent, sources, signal, authorize = async () => {} }) {
    if (this.closed) throw new Error('简报功能已停用。')
    if (this.active.size) throw new Error('简报正在生成，请等待完成或取消。')
    const controller = new AbortController()
    const state = { controller }
    this.active.add(state)
    this.progress = { phase: 'collecting', date: record.date, requestId, message: '正在准备来源…' }
    const abort = () => controller.abort(signal.reason)
    signal?.addEventListener('abort', abort, { once: true })
    if (signal?.aborted) abort()
    try {
      const existing = (await this.worker.call('records')).find(item => item.id === requestId)
      if (existing) {
        if (existing.kind !== 'briefing' || existing.date !== record.date || existing.notes !== record.notes) throw new Error('同一操作标识不能用于不同简报。')
        this.progress = { ...this.progress, phase: 'completed', message: '简报已保存', briefingId: existing.business?.briefingId }
        return existing
      }
      const runAt = new Date().toISOString()
      const windows = await this.worker.call('source_windows', { date: record.date, run_at: runAt, sources })
      const collected = []
      for (const [index, config] of sources.entries()) {
        await authorize()
        this.progress = { ...this.progress, message: `正在采集 ${index + 1}/${sources.length}：${config.label || config.source}` }
        collected.push(await this.collect(config, windows[index], runAt, controller.signal, authorize))
      }
      const submissions = []
      while (true) {
        if (controller.signal.aborted) throw new Error('简报生成已取消。')
        await authorize()
        const result = await this.worker.call('generated_briefing', { record, request_id: requestId, sources: collected, submissions, run_at: runAt })
        if (result.record) { this.progress = { ...this.progress, phase: 'completed', message: '简报已保存', briefingId: result.record.business.briefingId }; return result.record }
        this.progress = { ...this.progress, phase: 'model', message: submissions.length ? '正在撰写简报…' : '正在按规则筛选信息…' }
        const request = result.model
        const activation = await this.ctx.subagents.startActivation({ provider: 'stella-spawn', label: '每日简报', delivery: 'caller', signal: controller.signal,
          request: { parent, agentPreset: 'stella', maxDepth: 1, outputSchema: request.schema,
            ...(parent.model?.current() ? { agentOptions: parent.model.current() } : {}),
            prompt: [{ type: 'text', text: `你负责简报筛选与写作。按规则逐项提交结构化结果，完整覆盖给定 ID，不能编造来源、日期或执行结果。\n规则：\n${request.rules}\n\n${request.task}\n\n可阅读的原文数据：\n${JSON.stringify(request.entries)}` }],
          } })
        state.activation = activation
        try {
          const generated = await activation.result
          if (controller.signal.aborted) throw new Error('简报生成已取消。')
          if (generated.stopReason !== 'completed' || !generated.structured) throw new Error('模型未提交完整简报，游标没有前移，请检查模型后重试。')
          submissions.push(generated.structured)
        } finally { await activation.dispose(); state.activation = undefined }
      }
    } catch (error) {
      this.progress = { ...this.progress, phase: controller.signal.aborted ? 'cancelled' : 'failed', message: controller.signal.aborted ? '简报生成已取消。' : error.message }
      throw error
    } finally { signal?.removeEventListener('abort', abort); this.active.delete(state) }
  }

  async close() {
    this.closed = true
    for (const state of this.active) state.controller.abort()
    await Promise.allSettled([...this.active].map(state => state.activation?.dispose()))
  }
}
