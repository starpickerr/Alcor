import { registerBundledSkills } from '../../stella-organizer/src/skills.js'
import { secretaryPersona } from './persona.js'
import { createHash, randomUUID } from 'node:crypto'
import { readDateFile, writeBillCsv } from './page-files.js'
import { readBillFiles } from './bill-files.js'
import { defineTool } from '@deepseek-ai/dsh-tools'

export const inject = ['tools', 'stellaSecretary', 'systemPrompt']
export function apply(ctx) {
  registerBundledSkills(ctx, 'stella-secretary', new URL('../skills/', import.meta.url))
  const personalTurns = new WeakMap()
  const businessTurns = new WeakMap()
  ctx.on('agent/pre-step', ({ agent, messages }, next) => {
    if (messages.length) {
      personalTurns.set(agent, messages.every(message => message.source.kind === 'user'))
      businessTurns.set(agent, messages.every(message => ['user', 'schedule'].includes(message.source.kind)))
    }
    return next()
  })
  ctx.systemPrompt.variable('stella_identity', () => secretaryPersona)
  ctx.tools.guard(execution => {
    if (['stella_import_bills', 'stella_import_dates', 'stella_export_bills'].includes(execution.name)) {
      const header = execution.agent?.session?.header
      if (!header || header.origin === 'subagent' || header.parentSession || personalTurns.get(execution.agent) !== true) return '业务文件操作仅供用户主动发起的 Stella 主对话使用。'
    }
    if (['stella_generate_briefing', 'stella_review_tasks'].includes(execution.name)) {
      const header = execution.agent?.session?.header
      if (!header || header.origin === 'subagent' || header.parentSession || !businessTurns.get(execution.agent)) return '本机业务工具仅供用户主动对话或已安排的 Stella 自动化使用。'
    }
    if (!execution.name.startsWith('mcp__')) return
    const agent = execution.agent
    const header = agent?.session?.header
    if (!header || header.origin === 'subagent' || header.parentSession || personalTurns.get(agent) !== true) return '私人 MCP 仅限用户主动发起的 Stella 主会话。'
  })
  ctx.tools.register(defineTool({
    name: 'stella_overview',
    description: 'Read Stella’s introduction and installation status. This tool does not read personal sources.',
    parameters: {},
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: {
          displayName: { type: 'string', required: true },
          installedAt: { type: 'string', required: true },
          message: { type: 'string', required: true },
        },
      },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute() { return ctx.stellaSecretary.overview() },
  }))
  ctx.inject(['stellaOrganizer'], ctx => {
    ctx.inject(['fs'], ctx => ctx.tools.register(defineTool({
      name: 'stella_import_bills',
      description: 'Import user-provided WeChat XLSX and Alipay CSV bill files through DSH filesystem permissions into the same ledger used by the Bills page. When the user asks to import, call with their absolute file paths; imports directly without requiring a page visit. Use dry_run=true only if they ask for a preview. The parser handles encodings, exact amounts, timestamps, duplicates and skipped refunds/failed transactions; do not read file contents with shell or ask the user to paste rows. Return imported/duplicate/skipped counts and warnings; never claim all rows succeeded when some were skipped.',
      parameters: { paths: { type: 'array', items: { type: 'string' }, required: true }, dry_run: { type: 'boolean' } },
      output: { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
      async execute({ paths, dry_run = false }, execution) {
        const header = execution.agent?.session?.header
        if (!header || header.origin === 'subagent' || header.parentSession || personalTurns.get(execution.agent) !== true) throw new Error('账单导入需要用户主动发起。')
        const fs = execution.agent.ctx?.get('fs')
        if (!fs) throw new Error('当前对话没有可用的文件访问能力。')
        const files = await readBillFiles(fs, paths, execution.signal)
        execution.signal?.throwIfAborted()
        return ctx.stellaOrganizer.business({ domain: 'bills', method: 'import_files', arguments: { files, dry_run } })
      },
    })))

    ctx.inject(['fs'], ctx => {
      for (const [name, description, parameters, execute] of [
        ['stella_import_dates', 'Import a user-provided UTF-8 CSV/ICS date file through DSH file permissions into the Calendar page. Supports the same all-day dates and simple yearly recurrence as the page; does not import arbitrary timed ICS events. No page visit required. CSV requires title,date; optional annual,description,uid. Files up to 1 MB and 500 records.', { path: { type: 'string', required: true }, timezone: { type: 'string' } }, async ({ path, timezone = Intl.DateTimeFormat().resolvedOptions().timeZone }, execution) => {
          const fs = execution.agent.ctx?.get('fs')
          if (!fs) throw new Error('当前对话无法读取文件。')
          const input = await readDateFile(fs, path, execution.signal)
          return ctx.stellaOrganizer.business({ domain: 'calendar', method: 'import_special_dates', arguments: { ...input, timezone }, requestId: execution.callId })
        }],
        ['stella_export_bills', 'Export a requested date range of bills to a new CSV file using DSH filesystem permissions. start/end are YYYY-MM-DD inclusive; path must be an absolute .csv path. Never overwrite existing files. Uses the same ledger as the Bills page, keeps exact amounts and neutralizes spreadsheet formulas.', { path: { type: 'string', required: true }, start: { type: 'string', required: true }, end: { type: 'string', required: true } }, async ({ path, start, end }, execution) => {
          const result = await ctx.stellaOrganizer.business({ domain: 'bills', method: 'export_ledger_csv', arguments: { start, end } })
          return writeBillCsv(execution.agent.ctx, path, result.content, execution.signal, execution.agent.session)
        }],
      ]) ctx.tools.register(defineTool({ name, description, parameters, output: { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] }, async execute(args, execution) {
        const header = execution.agent?.session?.header
        if (!header || header.origin === 'subagent' || header.parentSession || personalTurns.get(execution.agent) !== true) throw new Error('业务文件操作需要用户主动发起。')
        return execute(args, execution)
      } }))
    })

    ctx.tools.register(defineTool({
      name: 'stella_business',
      description: 'Read or change Stella local business data through the same services as the pages. Domains: tasks (hierarchy, dependencies, completion, recurrence, checklist, categories, reminders); calendar (events, categories, recurrence, date imports); bills (ledger, reversal, statements, CSV/XLSX export); briefing (generation, revisions, item actions, rules; uses already authorized sources); agent_hub (start_job/list_jobs/cancel_job for the collaboration page, plus workflow); automation (catalog/list/create/edit/run/history/deleteInactive, for the Automation page). Load the matching Stella skill for workflows and argument examples. If a method is unfamiliar, call catalog; tasks/calendar/bills/briefing/agent_hub accept arguments.method to return only that method. Do not guess payload/patch fields. Use these tools to carry out user instructions directly; pages show the same saved objects. Do not ask the user to switch pages to do supported operations. For collaboration, starting a job is not completion: read list_jobs for its status/output. For automation, query catalog for timing and edit examples. Report success only after receiving the result.',
      parameters: {
        domain: { type: 'string', enum: ['tasks', 'calendar', 'bills', 'briefing', 'agent_hub', 'apple', 'automation'], required: true },
        method: { type: 'string', required: true },
        arguments: { type: 'object', additionalProperties: true },
        requestId: { type: 'string' },
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
      async execute({ domain, method, arguments: args = {}, requestId }, execution) {
        if (domain === 'bills' && method === 'import_files') {
          const header = execution.agent?.session?.header
          if (!header || header.origin === 'subagent' || header.parentSession || personalTurns.get(execution.agent) !== true) throw new Error('账单导入需要用户主动发起。')
        }
        if (domain === 'automation' || domain === 'agent_hub' && ['start_job', 'cancel_job', 'run_workflow'].includes(method)) {
          const header = execution.agent?.session?.header
          if (!header || header.origin === 'subagent' || header.parentSession || personalTurns.get(execution.agent) !== true) throw new Error('自动化管理和协作执行需要用户主动发起。')
        }
        if (domain === 'agent_hub' && method === 'run_workflow') args = { ...args, parentSessionId: execution.agent.session.id }
        if (domain === 'agent_hub' && method === 'start_job') {
          const hash = createHash('sha256').update(requestId ?? execution.callId ?? randomUUID()).digest('hex')
          args = { ...args, parentSessionId: execution.agent.session.id, id: `${hash.slice(0, 8)}-${hash.slice(8, 12)}-5${hash.slice(13, 16)}-8${hash.slice(17, 20)}-${hash.slice(20, 32)}` }
        }
        if (domain === 'briefing' && ['configure_sources', 'configure_source_groups', 'discover_sources', 'source_options'].includes(method)) throw new Error('请在简报设置中管理来源开关，账号和范围在对应插件中管理。')
        if (domain === 'briefing' && ['generate', 'resume_generation', 'cancel_generation'].includes(method)) {
          const header = execution.agent?.session?.header
          if (!header || header.origin === 'subagent' || header.parentSession || personalTurns.get(execution.agent) !== true) throw new Error('外部来源简报仅供用户主动发起，请使用已授权的手动简报入口。')
          if (method === 'generate') {
            const hash = createHash('sha256').update(requestId ?? execution.callId ?? randomUUID()).digest('hex')
            args = { notes: '', ...args, parentSessionId: execution.agent?.session?.id, requestId: `${hash.slice(0, 8)}-${hash.slice(8, 12)}-5${hash.slice(13, 16)}-8${hash.slice(17, 20)}-${hash.slice(20, 32)}` }
          }
        }
        return { result: await ctx.stellaOrganizer.business({ domain, method, arguments: args, requestId: domain === 'briefing' && method === 'generate' ? args.requestId : requestId ?? execution.callId }) }
      },
    }))
    for (const [name, description, execute] of [
      ['stella_generate_briefing', 'Start an independent briefing task using the configured sources and a fixed model. Return its run identity and execution session; starting does not mean the briefing is finished. User turns may use saved briefing sources. Scheduled turns use only selected local sources; external sources require separate manual authorization.', async (_args, execution) => {
        const now = new Date()
        const date = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
        // Schedule delivery can repeat after a crash; one saved daily briefing must keep one identity.
        const scheduled = personalTurns.get(execution.agent) !== true
        const hash = createHash('sha256').update(scheduled ? `stella-daily-briefing:${date}` : execution.callId || randomUUID()).digest('hex')
        const requestId = `${hash.slice(0, 8)}-${hash.slice(8, 12)}-5${hash.slice(13, 16)}-8${hash.slice(17, 20)}-${hash.slice(20, 32)}`
        const sources = scheduled ? (await ctx.stellaOrganizer.business({ domain: 'briefing', method: 'list_sources', arguments: {} })).filter(source => ['calendar_agenda', 'stella_tasks', 'stella_bills'].includes(source.source)) : undefined
        const input = { date, notes: '', requestId, parentSessionId: execution.agent.session.id, ...(scheduled ? { sources } : {}) }
        if (!scheduled) return ctx.stellaOrganizer.startBriefing(input)
        const record = await ctx.stellaOrganizer.generateBriefing(input)
        return { id: record.id, date: record.date, body: record.body, saved: true }
      }],
      ['stella_review_tasks', 'Read local unarchived tasks for a user-requested or scheduled task review. Does not modify tasks or access MCP.', () => ctx.stellaOrganizer.taskReview()],
    ]) ctx.tools.register(defineTool({ name, description, parameters: {},
      output: { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] }, execute,
    }))
  })

}
