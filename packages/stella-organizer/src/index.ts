import { Service } from '@deepseek-ai/cordis'
import { z } from 'zod'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { defineDomain, domainTable } from '@deepseek-ai/dsh-storage-domain'
import { remote, schemas, jobSchema } from './remote.js'
import { callAutomation } from './page-tools.js'
import { stellaTarget } from './automation.js'
import { recordSchema, buildBriefing } from './records.js'
import { BusinessWorker, resolveBusinessPython } from './business.js'
import { dshHomePath } from '@deepseek-ai/dsh-home-paths'
import { join } from 'node:path'
import { ReminderDelivery } from './reminders.js'
import { BriefingGenerator } from './briefing.js'
import { discoverBriefingSources, briefingSourceOptions, effectiveBriefingSources } from './briefing-sources.js'

const spec = defineDomain({ name: 'stella_organizer', version: 1, tables: {
  records: domainTable(recordSchema), jobs: domainTable(jobSchema),
  schedules: domainTable(z.object({ id: z.string() }).strict()),
} })

export default class Organizer extends TypertRemoteService {
  readonly agentHubDetached = true
  static inject = ['storageDomain', 'typert', 'schedule', 'subagents', 'sessionController', 'agentPresets', 'tools']
  private domain
  private businessWorker
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
    this.ctx.inject(['agentHub'], ctx => { void ctx.agentHub.importLegacyJobs([...this.domain.table('jobs').entries()].map(([,value])=>value)).catch(error=>this.ctx.logger.warn('旧协作记录导入失败：'+error.message)) })
    this.briefingGenerator = new BriefingGenerator(this.ctx, this.businessWorker)
    const reminders = new ReminderDelivery(this.businessWorker, { python: await resolveBusinessPython(), diagnostic: message => this.ctx.logger.warn(message) })
    const reminderTimer = setInterval(() => { void reminders.tick() }, 30000)
    void reminders.tick()
    this.ctx.typert.register({ package: '@stella/organizer', face: 'host', schemas: [], invocations: remote.descriptors, model: { services: [], events: [], objects: [] } })
    this.ctx.effect(() => async () => {
      this.active = false
      clearInterval(reminderTimer)
      await reminders.close()
      await this.briefingGenerator.close()
      await this.queue
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
    if (input.domain === 'apple') {
      const apple = this.ctx.get('stellaAppleSync')
      if (!apple) throw new Error('请先安装并启用 Apple 同步插件。')
      return apple.invoke({ method: input.method, arguments: input.arguments })
    }
    if (input.domain === 'calendar' && input.method === 'list_events' && input.arguments.start && input.arguments.end) {
      const apple = this.ctx.get('stellaAppleSync')
      let sync
      if (apple) {
        try { sync = await apple.invoke({ method: 'sync_range', arguments: { kind: 'calendar', start: input.arguments.start, end: input.arguments.end } }) }
        catch (error) { sync = { status: 'error', reason: error instanceof Error ? error.message : 'Apple 日程加载失败' } }
      }
      const result = await this.serialize(() => this.businessWorker.call('invoke', { domain: input.domain, method: input.method, arguments: input.arguments, request_id: input.requestId }))
      return result
    }
    if (input.domain === 'briefing' && input.method === 'generate') return this.startBriefing({ ...input.arguments, ...(input.requestId ? { requestId: input.requestId } : {}) })
    if (input.domain === 'briefing' && input.method === 'configure_sources') {
      const sources = schemas.briefingSources.parse(input.arguments.sources)
      const expected = input.arguments.expectedSources === undefined ? undefined : schemas.briefingSources.parse(input.arguments.expectedSources)
      return this.serialize(() => this.businessWorker.call('source_settings', { sources, ...(expected === undefined ? {} : { expected_sources: expected }) }))
    }
    if (input.domain === 'briefing' && input.method === 'source_groups') return this.businessWorker.call('source_group_settings')
    if (input.domain === 'briefing' && input.method === 'configure_source_groups') {
      const groups = schemas.sourceGroups.parse(input.arguments.groups)
      const expected = schemas.sourceGroups.parse(input.arguments.expectedGroups)
      return this.serialize(() => this.businessWorker.call('source_group_settings', { groups, expected_groups: expected }))
    }
    if (input.domain === 'briefing' && input.method === 'source_options') return briefingSourceOptions(this.ctx, schemas.briefingSources.parse([input.arguments.source])[0])
    if (input.domain === 'briefing' && input.method === 'generation_status') return this.briefingGenerator.status()
    if (input.domain === 'briefing' && input.method === 'cancel_generation') return this.briefingGenerator.cancel(input.arguments.requestId)
    if (input.domain === 'briefing' && input.method === 'resume_generation') return this.resumeBriefing(input.arguments)
    if (input.domain === 'briefing' && input.method === 'discover_sources') return discoverBriefingSources(this.ctx)
    if (input.domain === 'briefing' && input.method === 'source_preferences') return this.businessWorker.call('source_settings')
    if (input.domain === 'briefing' && input.method === 'list_sources') return this.effectiveSources()
    if (input.domain === 'briefing' && input.method === 'available_sources') return this.ctx.tools.definitions().map(tool => tool.name).filter(name => /^(mcp__[A-Za-z0-9_-]+__(fetch_messages|fetch_source|fetch)|[A-Za-z0-9_-]{1,64}_briefing_fetch)$/.test(name))
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
    return { records: await this.businessRecords(), jobs: [], schedules }
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
      const record = buildBriefing([], input.date, input.notes, now)
      return this.businessWorker.call('local_briefing', { record, request_id: input.requestId })
    })
  }

  private async effectiveSources() {
    const [preferences, catalog, groups] = await Promise.all([this.businessWorker.call('source_settings'), discoverBriefingSources(this.ctx), this.businessWorker.call('source_group_settings')])
    return effectiveBriefingSources(preferences, catalog.sources, groups)
  }

  private briefingAuthorization(sources) {
    return async () => {
      this.assertActive()
      const allowed = await this.effectiveSources()
      for (const source of sources) if (!allowed.some(saved => saved.source === source.source && saved.accountId === source.accountId && saved.tool === source.tool && saved.method === source.method && JSON.stringify(saved.arguments) === JSON.stringify(source.arguments))) throw new Error('来源已关闭、账号或范围已变化，请检查简报来源开关与插件设置。')
    }
  }

  @Remote
  async startBriefing(request) {
    const input = schemas.startBriefing[0].parse(request)
    this.assertActive()
    let parent
    if (input.parentSessionId) {
      const found = await this.ctx.sessionController.resolveAgent(input.parentSessionId)
      if ('error' in found) throw new Error('发起会话无法读取，请刷新后重试。')
      parent = found.agent
    }
    const sources = input.sources ?? await this.effectiveSources()
    return this.briefingGenerator.start({ record: buildBriefing([], input.date, input.notes, new Date().toISOString()), requestId: input.requestId,
      parent, workspaceId: input.workspaceId, sources, authorize: this.briefingAuthorization(sources) })
  }

  @Remote
  async generateBriefing(request) {
    const input = schemas.generateBriefing[0].parse(request)
    await this.startBriefing(input)
    return this.briefingGenerator.wait(input.requestId)
  }

  @Remote
  async resumeBriefing(request) {
    const { requestId } = schemas.resumeBriefing[0].parse(request)
    const run = await this.businessWorker.call('briefing_run', { method: 'get', request_id: requestId })
    return this.briefingGenerator.resume(requestId, this.briefingAuthorization(run.sources))
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
