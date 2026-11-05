import { Service } from '@deepseek-ai/cordis'
import { z } from 'zod'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { defineDomain, domainTable } from '@deepseek-ai/dsh-storage-domain'
import { remote, schemas, jobSchema } from './remote.js'
import { callAutomation } from './page-tools.js'
import { stellaTarget } from './automation.js'
import { recordSchema, buildBriefing } from './records.js'
import { BusinessWorker, resolveBusinessPython } from './business.js'
import { HubCoordinator } from './hub.js'
import { dshHomePath } from '@deepseek-ai/dsh-home-paths'
import { join } from 'node:path'
import { ReminderDelivery } from './reminders.js'
import { BriefingGenerator } from './briefing.js'
import { discoverBriefingSources, briefingSourceOptions } from './briefing-sources.js'

const spec = defineDomain({ name: 'stella_organizer', version: 1, tables: {
  records: domainTable(recordSchema), jobs: domainTable(jobSchema),
  schedules: domainTable(z.object({ id: z.string() }).strict()),
} })

export default class Organizer extends TypertRemoteService {
  static inject = ['storageDomain', 'typert', 'schedule', 'subagents', 'sessionController', 'agentPresets', 'tools']
  private domain
  private businessWorker
  private hub
  private briefingGenerator
  private active = true
  private queue = Promise.resolve()

  constructor(ctx) { super(ctx, 'stellaOrganizer', { namespace: 'stellaOrganizer' }) }

  async [Service.init]() {
    this.domain = await this.ctx.storageDomain.open(spec)
    const profile = this.ctx.get('profileContext')
    this.businessWorker = new BusinessWorker(join(profile?.dir ?? dshHomePath('profiles', 'default'), 'stella', 'business'), { python: await resolveBusinessPython(), diagnostic: message => this.ctx.logger.warn(message) })
    try { await this.businessWorker.call('migrate', { records: [...this.domain.table('records').entries()].map(([, value]) => value) }) }
    catch (error) { await this.businessWorker.close(); await this.domain.close(); throw error }
    this.hub = new HubCoordinator(this.ctx, this.businessWorker)
    this.briefingGenerator = new BriefingGenerator(this.ctx, this.businessWorker)
    const reminders = new ReminderDelivery(this.businessWorker, { python: await resolveBusinessPython(), diagnostic: message => this.ctx.logger.warn(message) })
    const reminderTimer = setInterval(() => { void reminders.tick() }, 30000)
    void reminders.tick()
    for (const [id, job] of this.domain.table('jobs').entries()) {
      if (['starting', 'running'].includes(job.status)) await this.domain.table('jobs').put(id, { ...job, status: 'interrupted', diagnostic: '宿主曾停止；请核对原生会话结果，再决定是否重新发起。' })
    }
    this.ctx.typert.register({ package: '@stella/organizer', face: 'host', schemas: [], invocations: remote.descriptors, model: { services: [], events: [], objects: [] } })
    this.ctx.effect(() => async () => {
      this.active = false
      clearInterval(reminderTimer)
      await reminders.close()
      await this.briefingGenerator.close()
      await this.queue
      await this.hub.close()
      // A host stop preserves schedules; unloading Stella while the host is live revokes its wakeups.
      if (this.ctx.root.fiber.state === 2) {
        for (const [id] of this.domain.table('schedules').entries()) {
          try { await this.ctx.schedule.edit({ id, action: 'update', patch: { active: false } }) }
          catch { this.ctx.logger.warn('Stella 自动化暂停失败，请在 DSH 自动化页面核对。') }
        }
      }
      await this.domain.close()
      await this.businessWorker.close()
    })
  }

  private assertActive() { if (!this.active) throw new Error('功能已停用，请重新打开。') }
  private serialize(work) {
    const result = this.queue.then(() => { this.assertActive(); return work() })
    this.queue = result.catch(() => undefined)
    return result
  }
  private nativeSchedule(entry) {
    return { id: entry.id, title: entry.title, prompt: entry.prompt, status: entry.status, scheduledAt: entry.scheduledAt, updatedAt: entry.updatedAt ?? null, targetSessionId: entry.lastDelivery?.targetSessionId ?? entry.target.sessionId ?? null, deliveredAt: entry.lastDelivery?.deliveredAt ?? null }
  }

  @Remote
  async business(request) {
    const input = schemas.business[0].parse(request)
    if (input.domain === 'automation') return callAutomation(this, input.method, input.arguments)
    if (input.domain === 'agent_hub' && input.method === 'start_job') return this.startJob(input.arguments)
    if (input.domain === 'agent_hub' && input.method === 'list_jobs') return (await this.read({})).jobs
    if (input.domain === 'agent_hub' && input.method === 'cancel_job') return this.cancelJob(input.arguments)
    if (input.domain === 'apple') {
      const apple = this.ctx.get('stellaAppleSync')
      if (!apple) throw new Error('请先安装并启用 Apple 同步插件。')
      return apple.invoke({ method: input.method, arguments: input.arguments })
    }
    if (input.domain === 'briefing' && input.method === 'generate') return this.generateBriefing({ ...input.arguments, ...(input.requestId ? { requestId: input.requestId } : {}) })
    if (input.domain === 'briefing' && input.method === 'configure_sources') {
      const sources = schemas.briefingSources.parse(input.arguments.sources)
      const expected = input.arguments.expectedSources === undefined ? undefined : schemas.briefingSources.parse(input.arguments.expectedSources)
      return this.serialize(() => this.businessWorker.call('source_settings', { sources, ...(expected === undefined ? {} : { expected_sources: expected }) }))
    }
    if (input.domain === 'briefing' && input.method === 'source_options') return briefingSourceOptions(this.ctx, schemas.briefingSources.parse([input.arguments.source])[0])
    if (input.domain === 'briefing' && input.method === 'generation_status') return this.briefingGenerator.status()
    if (input.domain === 'briefing' && input.method === 'cancel_generation') return this.briefingGenerator.cancel()
    if (input.domain === 'briefing' && input.method === 'discover_sources') return discoverBriefingSources(this.ctx)
    if (input.domain === 'briefing' && input.method === 'list_sources') return this.businessWorker.call('source_settings')
    if (input.domain === 'briefing' && input.method === 'available_sources') return this.ctx.tools.definitions().map(tool => tool.name).filter(name => /^(mcp__[A-Za-z0-9_-]+__(fetch_messages|fetch_source|fetch)|[A-Za-z0-9_-]{1,64}_briefing_fetch)$/.test(name))
    if (input.domain === 'agent_hub' && input.method === 'run_workflow') return this.serialize(() => this.hub.start(input.arguments))
    if (input.domain === 'agent_hub' && input.method === 'cancel_task') return this.serialize(() => this.hub.cancel(input.arguments.task_id))
    return this.serialize(() => this.businessWorker.call('invoke', { domain: input.domain, method: input.method, arguments: input.arguments, request_id: input.requestId }))
  }

  private async businessRecords() {
    const current = await this.businessWorker.call('records')
    return [...current, ...[...this.domain.table('records').entries()].map(([, value]) => value).filter(item => item.kind === 'briefing')]
  }

  applePeer(request) {
    return this.serialize(() => this.businessWorker.call('apple_peer', request))
  }

  @Remote
  async read(request) {
    schemas.read[0].parse(request); this.assertActive()
    const owned = new Set([...this.domain.table('schedules').keys()])
    const schedules = (await this.ctx.schedule.catalog()).filter(entry => owned.has(entry.id)).map(entry => this.nativeSchedule(entry))
    const tasks = await this.businessWorker.call('invoke', { domain: 'agent_hub', method: 'list_tasks' })
    const jobs = await Promise.all(tasks.map(async task => this.hubJob(await this.businessWorker.call('invoke', { domain: 'agent_hub', method: 'get_task', arguments: { task_id: task.taskId } }))))
    return { records: await this.businessRecords(), jobs: [...[...this.domain.table('jobs').entries()].map(([, value]) => value), ...jobs], schedules }
  }

  @Remote
  async write(request) {
    const input = schemas.write[0].parse(request)
    if (input.record.kind === 'briefing') throw new Error('简报通过生成入口保存，不能伪造来源。')
    return this.serialize(() => this.businessWorker.call('write', { record: input.record, expected_revision: input.expectedRevision }))
  }

  @Remote
  async archive(request) {
    const input = schemas.archive[0].parse(request)
    if (!this.domain.table('records').get(input.id) || this.domain.table('records').get(input.id).kind !== 'briefing') {
      return this.serialize(() => this.businessWorker.call('archive', { record_id: input.id, expected_revision: input.expectedRevision, archived: input.archived }))
    }
    return this.serialize(() => this.domain.table('records').update(input.id, current => {
      if (current.revision !== input.expectedRevision) throw new Error('记录已变化，请刷新后重试。')
      return { ...current, archived: input.archived, revision: current.revision + 1, updatedAt: new Date().toISOString() }
    }))
  }

  @Remote
  async briefing(request) {
    const input = schemas.briefing[0].parse(request)
    return this.serialize(async () => {
      const table = this.domain.table('records')
      const existing = table.get(input.requestId)
      if (existing) {
        if (existing.kind !== 'briefing') throw new Error('操作标识已被占用。')
        if (existing.date !== input.date || existing.notes !== input.notes) throw new Error('同一操作标识不能用于不同简报。')
        return existing
      }
      const now = new Date().toISOString()
      const record = buildBriefing(await this.businessRecords(), input.date, input.notes, now)
      return this.businessWorker.call('local_briefing', { record, request_id: input.requestId })
    })
  }

  @Remote
  async generateBriefing(request) {
    const input = schemas.generateBriefing[0].parse(request)
    const found = await this.ctx.sessionController.resolveAgent(input.parentSessionId)
    if ('error' in found) throw new Error('请先开始一个 Stella 对话并选择模型。')
    this.assertActive()
    const record = buildBriefing([], input.date, input.notes, new Date().toISOString())
    const configured = await this.businessWorker.call('source_settings')
    const sources = input.sources ?? configured
    const authorize = async () => {
      this.assertActive()
      const allowed = await this.businessWorker.call('source_settings')
      for (const source of sources) if (!['calendar_agenda', 'stella_tasks', 'stella_bills'].includes(source.source) && !allowed.some(saved => saved.source === source.source && saved.accountId === source.accountId && saved.tool === source.tool && saved.method === source.method && JSON.stringify(saved.arguments) === JSON.stringify(source.arguments))) throw new Error('来源未获简报授权或授权已撤销，请在简报的信息来源中重新选择。')
    }
    await authorize()
    return this.briefingGenerator.generate({ record, requestId: input.requestId, parent: found.agent, sources, authorize })
  }

  @Remote
  async startJob(request) {
    const input = schemas.startJob[0].parse(request)
    return this.serialize(async () => {
      const found = await this.ctx.sessionController.resolveAgent(input.parentSessionId)
      if ('error' in found || this.ctx.agentPresets.presetForSession(found.agent.session) !== 'stella') throw new Error('请先开始一个 Stella 对话，再发起协作。')
      const invoke = (method, args) => this.businessWorker.call('invoke', { domain: 'agent_hub', method, arguments: args })
      let task = await invoke('create_task', { payload: { title: input.title, goal: input.prompt, participants: [{ nativeAgentId: 'stella' }] }, request_id: input.id })
      if (!task.steps.length) task = await invoke('set_workflow', { task_id: task.taskId, steps: [{ stepId: 'work', title: input.title, participantId: task.participants[0].participantId, dependsOn: [] }], expected_revision: task.revision, request_id: input.id + ':workflow' })
      if (!task.steps.some(step => step.attempts.length)) await this.hub.start({ task_id: task.taskId, parentSessionId: input.parentSessionId })
      return this.hubJob(task)
    })
  }

  private hubJob(task) {
    const attempts = task.steps.flatMap(step => step.attempts)
    const latest = attempts.at(-1)
    return jobSchema.parse({ id: task.taskId, title: task.title, prompt: task.goal, parentSessionId: '', createdAt: new Date(task.createdAt).toISOString(),
      status: ({ pending: 'starting', queued: 'starting', running: 'running', succeeded: 'completed', outcome_unknown: 'interrupted', failed: 'failed', cancelled: 'cancelled' })[task.status] ?? 'starting',
      ...(latest?.attemptId ? { childId: latest.attemptId } : {}),
      output: task.messages.filter(message => message.kind === 'reply').map(message => message.body).join('\n\n').slice(0, 100000),
      diagnostic: task.messages.filter(message => message.kind === 'status').map(message => message.body).join('\n').slice(0, 4096),
    })
  }

  @Remote
  async cancelJob(request) {
    const { id } = schemas.cancelJob[0].parse(request)
    this.assertActive()
    if (id.startsWith('hub-')) return this.hubJob(await this.hub.cancel(id))
    const current = this.domain.table('jobs').get(id)
    if (!current) throw new Error('协作任务不存在。')
    return current
  }

  @Remote
  async automation(request) {
    const input = schemas.automation[0].parse(request)
    this.assertActive()
    if (input.action === 'catalog') {
      const owned = new Set([...this.domain.table('schedules').keys()])
      return (await this.ctx.schedule.catalog()).filter(entry => input.scope === 'all' || owned.has(entry.id))
        .map(entry => ({ ...entry, sourceLabel: owned.has(entry.id) ? 'Stella' : 'DSH' }))
    }
    if (input.action === 'history') {
      if (input.scope === 'stella' && !this.domain.table('schedules').get(input.request.id)) throw new Error('只能读取 Stella 自动化。')
      return this.ctx.schedule.history(input.request)
    }
    return this.serialize(async () => {
      if (input.action === 'create') {
        if (input.scope === 'all') return this.ctx.schedule.create(input.request)
        const entry = await this.ctx.schedule.create({ ...input.request, target: stellaTarget(input.request.target, undefined) })
        try {
          this.assertActive()
          await this.domain.table('schedules').put(entry.id, { id: entry.id })
        } catch (error) {
          await this.ctx.schedule.edit({ id: entry.id, action: 'update', patch: { active: false } })
          throw error
        }
        return entry
      }
      if (input.action === 'deleteInactive') {
        if (input.scope === 'all') return this.ctx.schedule.deleteInactive()
        let deleted = 0
        for (const entry of await this.ctx.schedule.catalog()) {
          if (entry.status !== 'ended' || !this.domain.table('schedules').get(entry.id)) continue
          const result = await this.ctx.schedule.edit({ id: entry.id, action: 'delete', expectedUpdatedAt: entry.updatedAt ?? null })
          if ('deleted' in result) { await this.domain.table('schedules').delete(entry.id); deleted++ }
        }
        return { deleted }
      }
      const owned = this.domain.table('schedules').get(input.request.id)
      if (input.scope === 'stella' && !owned) throw new Error('只能管理 Stella 自动化。')
      if (input.action === 'run') return this.ctx.schedule.run(input.request)
      let change = input.request
      if (owned && change.action === 'update' && change.patch.target) {
        const current = await this.ctx.schedule.view({ id: change.id })
        if ('code' in current) return current
        change = { ...change, patch: { ...change.patch, target: stellaTarget(change.patch.target, current) } }
      }
      const result = await this.ctx.schedule.edit(change)
      if (owned && 'deleted' in result) await this.domain.table('schedules').delete(change.id)
      return result
    })
  }

  async taskReview() {
    this.assertActive()
    const records = await this.businessRecords()
    const tasks = records.filter(item => item.kind === 'task' && !item.archived)
    return { open: tasks.filter(item => item.status !== 'done').map(item => ({ title: item.title, due: item.due, details: item.details })), completed: tasks.filter(item => item.status === 'done').length }
  }

  @Remote
  async createSchedule(request) {
    const input = schemas.createSchedule[0].parse(request)
    const timing = input.timing.kind === 'daily' ? { kind: 'daily', daily: { time: input.timing.time, time_zone: input.timing.timeZone } } : input.timing
    return this.nativeSchedule(await this.automation({ scope: 'stella', action: 'create', request: { title: input.title, prompt: input.prompt, timing } }))
  }

  @Remote
  async editSchedule(request) {
    const input = schemas.editSchedule[0].parse(request)
    const result = await this.automation({ scope: 'stella', action: input.action === 'run' ? 'run' : 'edit', request: input.action === 'run' ? { id: input.id } : input.action === 'delete'
      ? { id: input.id, action: 'delete', expectedUpdatedAt: input.expectedUpdatedAt }
      : { id: input.id, action: 'update', patch: { active: input.action === 'resume' }, expectedUpdatedAt: input.expectedUpdatedAt } })
    if ('code' in result) throw new Error('任务已变化或不可执行，请刷新后重试。')
    return true
  }
}
