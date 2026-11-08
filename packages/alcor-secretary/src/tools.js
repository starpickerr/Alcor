import { registerBundledSkills } from '../../alcor-organizer/src/skills.js'
import { secretaryPersona } from './persona.js'
import { createHash, randomUUID } from 'node:crypto'
import { readDateFile, writeBillCsv } from './page-files.js'
import { readBillFiles } from './bill-files.js'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { classifyCapability, requiresUnimplementedAppleApproval } from './security-policy.js'
import { createAutomationDeleteGate, isAutomationDeletion, automationDeleteUnavailable } from './security-automation-gate.js'
import { createLocalDeleteGate, isLocalDeletion } from './security-local-delete-gate.js'
import { restoreLocalItem } from './local-item-restore.js'
import { createAppleExportGate, isApplePlanOperation } from './security-apple-export-gate.js'

export const inject = ['tools', 'alcorSecretary', 'systemPrompt']
export function apply(ctx) {
  registerBundledSkills(ctx, 'alcor-secretary', new URL('../skills/', import.meta.url))
  const turnSources = new WeakMap()
  // Only host projection data can carry provenance across steps; empty messages
  // may inherit sources solely within the very same open turn.
  const turnSource = agent => {
    const saved = turnSources.get(agent)
    const boundary = agent?.session && ctx.get('agents')?.turnBoundaryOf(agent.session)
    return saved && saved.session === agent.session && saved.start === boundary?.openTurnStartSeq
      && saved.turn === boundary?.lastTurn ? saved.source : undefined
  }
  const personalTurn = agent => turnSource(agent) === 'user'
  const businessTurn = agent => ['user', 'schedule'].includes(turnSource(agent))
  ctx.on('agent/pre-step', ({ agent, messages }, next) => {
    if (messages.length) {
      const boundary = agent?.session && ctx.get('agents')?.turnBoundaryOf(agent.session)
      const user = messages.every(message => message.source.kind === 'user')
      const schedule = messages.every(message => message.source.kind === 'schedule')
      if (boundary?.openTurnStartSeq != null && Number.isSafeInteger(boundary.lastTurn) && boundary.lastTurn > 0) {
        const previous = turnSource(agent)
        const incoming = user ? 'user' : schedule ? 'schedule' : 'mixed'
        const source = previous && previous !== incoming ? 'mixed' : incoming
        turnSources.set(agent, { session: agent.session, start: boundary.openTurnStartSeq, turn: boundary.lastTurn, source })
      } else turnSources.delete(agent)
    }
    return next()
  })
  ctx.systemPrompt.variable('alcor_identity', () => secretaryPersona)
  ctx.tools.guard(execution => {
    if ((execution.name === 'schedule_edit' && execution.arguments?.action === 'delete') || isAutomationDeletion(execution)) return automationDeleteUnavailable
    if (requiresUnimplementedAppleApproval(execution)) return '此 Apple 操作需要具体目标审批，暂不能通过对话执行。'
    const capability = classifyCapability(execution.name, execution.arguments).kind
    if (capability === 'unclassified') return '此 Alcor 操作尚未完成安全分类，暂不能执行。'
    if (capability === 'internal') return '此内部操作仅由受信任的宿主调用。'
    if (execution.name === 'alcor_business') {
      const header = execution.agent?.session?.header
      if (!header || header.origin === 'subagent' || header.parentSession || !personalTurn(execution.agent)) return '本机业务数据仅供用户当前主会话使用；已配置的本机简报请使用专用入口。'
    }
    if (['alcor_import_bills', 'alcor_import_dates', 'alcor_export_bills'].includes(execution.name)) {
      const header = execution.agent?.session?.header
      if (!header || header.origin === 'subagent' || header.parentSession || !personalTurn(execution.agent)) return '业务文件操作仅供用户主动发起的 Alcor 主对话使用。'
    }
    if (['alcor_generate_briefing', 'alcor_review_tasks'].includes(execution.name)) {
      const header = execution.agent?.session?.header
      if (!header || header.origin === 'subagent' || header.parentSession || !businessTurn(execution.agent)) return '本机业务工具仅供用户主动对话或已安排的 Alcor 自动化使用。'
      if (execution.name === 'alcor_review_tasks' && !personalTurn(execution.agent)) return '定时任务尚无明确的事务读取授权，请使用已配置的本机简报来源。'
    }
    if (!execution.name.startsWith('mcp__')) return
    const agent = execution.agent
    const header = agent?.session?.header
    if (!header || header.origin === 'subagent' || header.parentSession || !personalTurn(agent)) return '私人 MCP 仅限用户主动发起的 Alcor 主会话。'
  })
  ctx.tools.register(defineTool({
    name: 'alcor_overview',
    description: 'Read Alcor’s introduction and installation status. This tool does not read personal sources.',
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
    async execute() { return ctx.alcorSecretary.overview() },
  }))
  ctx.inject(['alcorOrganizer'], ctx => {
    const automationGate = createAutomationDeleteGate()
    ctx.on('tools/pre-execute', (execution, next) => automationGate.preExecute(execution, next), { prepend: true })
    ctx.on('tools/result', execution => automationGate.release(execution))
    const localDeleteGate = createLocalDeleteGate({
      prepare: (request, authorize) => ctx.alcorOrganizer.prepareApprovedDelete(request, authorize),
      commit: (request, authorize) => ctx.alcorOrganizer.commitApprovedDelete(request, authorize),
      getApproval: () => ctx.get('approval'),
      trustedTurnOf: execution => {
        const boundary = ctx.get('agents')?.turnBoundaryOf(execution.agent.session)
        return boundary?.openTurnStartSeq == null ? undefined : boundary.lastTurn
      },
      actorStillAuthorized: execution => {
        const header = execution.agent?.session?.header
        return !!header && header.origin !== 'subagent' && !header.parentSession && personalTurn(execution.agent)
          && ctx.tools.executable(execution.name, execution.agent, execution.toolSet) !== undefined
          && ctx.tools.checkGuards(execution) === undefined
      },
    })
    ctx.tools.guard(localDeleteGate.guard)
    ctx.on('tools/pre-execute', (execution, next) => localDeleteGate.preExecute(execution, next), { prepend: true })
    ctx.on('tools/result', execution => localDeleteGate.release(execution))
    const appleExportGate = createAppleExportGate({
      prepare: (request, authorize) => ctx.alcorOrganizer.prepareApprovedExport(request, authorize),
      commit: (plan, authorize) => ctx.alcorOrganizer.commitApprovedExport(plan, authorize),
      getApproval: () => ctx.get('approval'),
      trustedTurnOf: execution => {
        const boundary = ctx.get('agents')?.turnBoundaryOf(execution.agent.session)
        return boundary?.openTurnStartSeq == null ? undefined : boundary.lastTurn
      },
      actorStillAuthorized: execution => {
        const header = execution.agent?.session?.header
        return !!header && header.origin !== 'subagent' && !header.parentSession && personalTurn(execution.agent)
          && ctx.tools.executable(execution.name, execution.agent, execution.toolSet) !== undefined
          && ctx.tools.checkGuards(execution) === undefined
      },
    })
    ctx.tools.guard(appleExportGate.guard)
    ctx.on('tools/pre-execute', (execution, next) => appleExportGate.preExecute(execution, next), { prepend: true })
    ctx.on('tools/result', execution => appleExportGate.release(execution))
    ctx.inject(['fs'], ctx => ctx.tools.register(defineTool({
      name: 'alcor_import_bills',
      description: 'Import user-provided WeChat XLSX and Alipay CSV bill files through DSH filesystem permissions into the same ledger used by the Bills page. When the user asks to import, call with their absolute file paths; imports directly without requiring a page visit. Use dry_run=true only if they ask for a preview. The parser handles encodings, exact amounts, timestamps, duplicates and skipped refunds/failed transactions; do not read file contents with shell or ask the user to paste rows. Return imported/duplicate/skipped counts and warnings; never claim all rows succeeded when some were skipped.',
      parameters: { paths: { type: 'array', items: { type: 'string' }, required: true }, dry_run: { type: 'boolean' } },
      output: { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
      async execute({ paths, dry_run = false }, execution) {
        const header = execution.agent?.session?.header
        if (!header || header.origin === 'subagent' || header.parentSession || !personalTurn(execution.agent)) throw new Error('账单导入需要用户主动发起。')
        const fs = execution.agent.ctx?.get('fs')
        if (!fs) throw new Error('当前对话没有可用的文件访问能力。')
        const files = await readBillFiles(fs, paths, execution.signal)
        execution.signal?.throwIfAborted()
        return ctx.alcorOrganizer.business({ domain: 'bills', method: 'import_files', arguments: { files, dry_run } })
      },
    })))

    ctx.inject(['fs'], ctx => {
      for (const [name, description, parameters, execute] of [
        ['alcor_import_dates', 'Import a user-provided UTF-8 CSV/ICS date file through DSH file permissions into the Calendar page. Supports the same all-day dates and simple yearly recurrence as the page; does not import arbitrary timed ICS events. No page visit required. CSV requires title,date; optional annual,description,uid. Files up to 1 MB and 500 records.', { path: { type: 'string', required: true }, timezone: { type: 'string' } }, async ({ path, timezone = Intl.DateTimeFormat().resolvedOptions().timeZone }, execution) => {
          const fs = execution.agent.ctx?.get('fs')
          if (!fs) throw new Error('当前对话无法读取文件。')
          const input = await readDateFile(fs, path, execution.signal)
          return ctx.alcorOrganizer.business({ domain: 'calendar', method: 'import_special_dates', arguments: { ...input, timezone }, requestId: execution.callId })
        }],
        ['alcor_export_bills', 'Export a requested date range of bills to a new CSV file using DSH filesystem permissions. start/end are YYYY-MM-DD inclusive; path must be an absolute .csv path. Never overwrite existing files. Uses the same ledger as the Bills page, keeps exact amounts and neutralizes spreadsheet formulas.', { path: { type: 'string', required: true }, start: { type: 'string', required: true }, end: { type: 'string', required: true } }, async ({ path, start, end }, execution) => {
          const result = await ctx.alcorOrganizer.business({ domain: 'bills', method: 'export_ledger_csv', arguments: { start, end } })
          return writeBillCsv(execution.agent.ctx, path, result.content, execution.signal, execution.agent.session)
        }],
      ]) ctx.tools.register(defineTool({ name, description, parameters, output: { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] }, async execute(args, execution) {
        const header = execution.agent?.session?.header
        if (!header || header.origin === 'subagent' || header.parentSession || !personalTurn(execution.agent)) throw new Error('业务文件操作需要用户主动发起。')
        return execute(args, execution)
      } }))
    })

    ctx.tools.register(defineTool({
      name: 'alcor_business',
      description: 'Read or change Alcor local business data through the same services as the pages. Domains: tasks (hierarchy, dependencies, completion, recurrence, checklist, categories, reminders); calendar (events, categories, recurrence, date imports); bills (ledger, reversal, statements, CSV/XLSX export); briefing (generation, revisions, item actions, rules; uses already authorized sources); automation (catalog/list/create/edit/run/history/deleteInactive, for the Automation page). Load the matching Alcor skill for workflows and argument examples. If a method is unfamiliar, call catalog; tasks/calendar/bills/briefing accept arguments.method to return only that method. Do not guess payload/patch fields. Use these tools to carry out user instructions directly; pages show the same saved objects. Do not ask the user to switch pages to do supported operations. For automation, query catalog for timing and edit examples. For an Apple conflict where the Alcor item is missing, restore_local_item accepts kind (calendar/reminders) and identity (the local item ID). It restores only the missing Alcor item from its already selected Apple source, without writing Apple or replacing existing local data. For an existing exact-bound item conflict, resolve_conflict accepts kind, alcor_id (calendar) or todo_id (reminders), and choice keep_apple (local update) or keep_alcor (specific approval before Apple update). Missing, moved, ambiguous and unknown targets are not overwritten. Report success only after receiving the result.',
      parameters: {
        domain: { type: 'string', enum: ['tasks', 'calendar', 'bills', 'briefing', 'apple', 'automation'], required: true },
        method: { type: 'string', required: true },
        arguments: { type: 'object', additionalProperties: true },
        requestId: { type: 'string' },
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
      async execute({ domain, method, arguments: args = {}, requestId }, execution) {
        if (domain === 'apple' && method === 'restore_local_item') return restoreLocalItem(execution, {
          trustedTurnOf: current => {
            const boundary = ctx.get('agents')?.turnBoundaryOf(current.agent.session)
            return boundary?.openTurnStartSeq == null ? undefined : boundary.lastTurn
          },
          actorStillAuthorized: current => {
            const header = current.agent?.session?.header
            return !!header && header.origin !== 'subagent' && !header.parentSession && personalTurn(current.agent)
              && ctx.tools.executable(current.name, current.agent, current.toolSet) !== undefined
              && ctx.tools.checkGuards(current) === undefined
          },
          restore: (request, authorize) => ctx.alcorOrganizer.restoreAppleItem(request, authorize),
        })
        if (isApplePlanOperation(execution)) return appleExportGate.execute(execution)
        if (isAutomationDeletion(execution)) return automationGate.execute(execution)
        if (isLocalDeletion(execution)) return localDeleteGate.execute(execution)
        if (domain === 'calendar' && method === 'list_events') {
          const header = execution.agent?.session?.header
          if (!header || header.origin === 'subagent' || header.parentSession || !personalTurn(execution.agent)) {
            throw new Error('本机日程查询仅供用户当前主会话使用；已配置的本机简报来源使用专用入口。')
          }
        }
        if (domain === 'bills' && method === 'import_files') {
          const header = execution.agent?.session?.header
          if (!header || header.origin === 'subagent' || header.parentSession || !personalTurn(execution.agent)) throw new Error('账单导入需要用户主动发起。')
        }
        if (domain === 'agent_hub') throw new Error('AgentHub 已独立为 DSH 开发者工具，请在工作台使用。')
        if (domain === 'automation') {
          const header = execution.agent?.session?.header
          if (!header || header.origin === 'subagent' || header.parentSession || !personalTurn(execution.agent)) throw new Error('自动化管理需要用户主动发起。')
        }
        if (domain === 'briefing' && ['configure_sources', 'configure_source_groups', 'discover_sources', 'source_options'].includes(method)) throw new Error('请在简报设置中管理来源开关，账号和范围在对应插件中管理。')
        if (domain === 'briefing' && ['generate', 'resume_generation', 'cancel_generation'].includes(method)) {
          const header = execution.agent?.session?.header
          if (!header || header.origin === 'subagent' || header.parentSession || !personalTurn(execution.agent)) throw new Error('外部来源简报仅供用户主动发起，请使用已授权的手动简报入口。')
          if (method === 'generate') {
            const hash = createHash('sha256').update(requestId ?? execution.callId ?? randomUUID()).digest('hex')
            args = { notes: '', ...args, parentSessionId: execution.agent?.session?.id, requestId: `${hash.slice(0, 8)}-${hash.slice(8, 12)}-5${hash.slice(13, 16)}-8${hash.slice(17, 20)}-${hash.slice(20, 32)}` }
          }
        }
        return { result: await ctx.alcorOrganizer.business({ domain, method, arguments: args, requestId: domain === 'briefing' && method === 'generate' ? args.requestId : requestId ?? execution.callId }) }
      },
    }))
    for (const [name, description, execute] of [
      ['alcor_generate_briefing', 'Start an independent briefing task using the configured sources and a fixed model. Return its run identity and execution session; starting does not mean the briefing is finished. User turns may use saved briefing sources. Scheduled turns use only selected local sources; external sources require separate manual authorization.', async (_args, execution) => {
        const now = new Date()
        const date = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
        // Retain the historical stella-daily-briefing seed so existing schedules keep their idempotency identity across the rename.
        const scheduled = !personalTurn(execution.agent)
        const hash = createHash('sha256').update(scheduled ? `stella-daily-briefing:${date}` : execution.callId || randomUUID()).digest('hex')
        const requestId = `${hash.slice(0, 8)}-${hash.slice(8, 12)}-5${hash.slice(13, 16)}-8${hash.slice(17, 20)}-${hash.slice(20, 32)}`
        const sources = scheduled ? await ctx.alcorOrganizer.localBriefingSources() : undefined
        const input = { date, notes: '', requestId, parentSessionId: execution.agent.session.id, ...(scheduled ? { sources } : {}) }
        if (!scheduled) return ctx.alcorOrganizer.startBriefing(input)
        const record = await ctx.alcorOrganizer.generateBriefing(input)
        return { id: record.id, date: record.date, body: record.body, saved: true }
      }],
      ['alcor_review_tasks', 'Read local unarchived tasks for a review requested in the current user conversation. Does not modify tasks or access MCP.', () => ctx.alcorOrganizer.taskReview()],
    ]) ctx.tools.register(defineTool({ name, description, parameters: {},
      output: { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] }, execute,
    }))
  })

}
