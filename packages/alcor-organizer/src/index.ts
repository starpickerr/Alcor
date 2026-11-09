import { Service } from '@deepseek-ai/cordis'
import { createDeletePageHost } from '../../alcor-secretary/src/delete-page-host.js'
import { billDeleteTitle } from '../../alcor-secretary/src/security-local-delete-gate.js'
import { z } from 'zod'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { defineDomain, domainTable } from '@deepseek-ai/dsh-storage-domain'
import { remote, schemas, jobSchema } from './remote.js'
import { callAutomation } from './page-tools.js'
import { alcorTarget, automationDeleteUnavailable } from './automation.js'
import { recordSchema, buildBriefing } from './records.js'
import { BusinessWorker, resolveBusinessPython } from './business.js'
import { dshHomePath } from '@deepseek-ai/dsh-home-paths'
import { join } from 'node:path'
import { createHash, randomUUID } from 'node:crypto'
import { ReminderDelivery } from './reminders.js'
import { BriefingGenerator } from './briefing.js'
import { discoverBriefingSources, briefingSourceOptions, effectiveBriefingSources, localBriefingSources } from './briefing-sources.js'
import { initializeProfile } from './profile-migration.js'

const spec = defineDomain({ name: 'alcor_organizer', version: 1, tables: {
  records: domainTable(recordSchema), jobs: domainTable(jobSchema),
  schedules: domainTable(z.object({ id: z.string() }).strict()),
} })

function assertCompatibleProfile(ctx) {
  const selected = ctx.get('profileContext')?.startedBundles
  if (!Array.isArray(selected)) throw new Error('无法确认当前 Profile 的插件，已停止 Alcor 启动。')
  if (selected.includes('@stella/secretary')) throw new Error('旧版 Stella 插件与 Alcor 不能同时启用，请先迁移 Profile。')
}

export default class Organizer extends TypertRemoteService {
  readonly agentHubDetached = true
  static inject = ['credentials', 'storageDomain', 'typert', 'schedule', 'subagents', 'sessionController', 'agentPresets', 'tools']
  private domain
  private businessWorker
  private briefingGenerator
  private active = true
  private queue = Promise.resolve()
  private exportAuthorizers = new WeakMap()
  private deleteAuthorizers = new WeakMap()
  private pageDeletes = createDeletePageHost({
    prepare: (request, authorize) => this.prepareApprovedDelete(request, authorize),
    commit: (prepared, authorize) => this.commitApprovedDelete(prepared, authorize),
  })

  constructor(ctx) { assertCompatibleProfile(ctx); super(ctx, 'alcorOrganizer', { namespace: 'alcorOrganizer' }) }

  async [Service.init]() {
    assertCompatibleProfile(this.ctx)
    await initializeProfile(this.ctx)
    this.domain = await this.ctx.storageDomain.open(spec)
    const profile = this.ctx.get('profileContext')
    this.businessWorker = new BusinessWorker(join(profile?.dir ?? dshHomePath('profiles', 'default'), 'alcor', 'business'), { python: await resolveBusinessPython(), diagnostic: message => this.ctx.logger.warn(message) })
    try { await this.businessWorker.call('migrate', { records: [...this.domain.table('records').entries()].map(([, value]) => value) }) }
    catch (error) { await this.businessWorker.close(); await this.domain.close(); throw error }
    this.ctx.inject(['agentHub'], ctx => { void ctx.agentHub.importLegacyJobs([...this.domain.table('jobs').entries()].map(([,value])=>value)).catch(error=>this.ctx.logger.warn('旧协作记录导入失败：'+error.message)) })
    this.briefingGenerator = new BriefingGenerator(this.ctx, this.businessWorker)
    const reminders = new ReminderDelivery(this.businessWorker, { python: await resolveBusinessPython(), diagnostic: message => this.ctx.logger.warn(message) })
    const reminderTimer = setInterval(() => { void reminders.tick() }, 30000)
    void reminders.tick()
    this.ctx.typert.register({ package: '@alcor/organizer', face: 'host', schemas: [], invocations: remote.descriptors, model: { services: [], events: [], objects: [] } })
    this.ctx.effect(() => async () => {
      this.active = false
      this.pageDeletes.close()
      clearInterval(reminderTimer)
      await reminders.close()
      await this.briefingGenerator.close()
      await this.queue
      // A host stop preserves schedules; unloading Alcor while the host is live revokes its wakeups.
      if (this.ctx.root.fiber.state === 2) {
        for (const [id] of this.domain.table('schedules').entries()) {
          try { await this.ctx.schedule.edit({ id, action: 'update', patch: { active: false } }) }
          catch { this.ctx.logger.warn('Alcor 自动化暂停失败，请在 DSH 自动化页面核对。') }
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
    if ((input.domain === 'tasks' && ['delete_todo', 'delete_category', 'delete_external_todo'].includes(input.method))
        || (input.domain === 'bills' && input.method === 'delete_ledger')
        || (input.domain === 'calendar' && (['delete_category', 'exclude_occurrence'].includes(input.method)
          || (input.method === 'delete_event' && !(input.arguments.scope === 'future' && typeof input.arguments.occurrence_on === 'string'))))) {
      throw new Error('缺少可验证的本次人工确认通道，不可恢复删除暂不可用。')
    }
    if (input.domain === 'automation') return callAutomation(this, input.method, input.arguments)
    if (input.domain === 'apple') {
      const apple = this.ctx.get('alcorAppleSync')
      if (!apple) throw new Error('请先安装并启用 Apple 同步插件。')
      return apple.invoke({ method: input.method, arguments: input.arguments })
    }
    if (input.domain === 'calendar' && input.method === 'list_events' && input.arguments.start && input.arguments.end) {
      // Preserve the existing refresh-before-read behavior until the outbox authorization boundary is connected.
      await this.refreshCalendarRange(input.arguments.start, input.arguments.end)
      return this.queryCalendarRange(input)
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

  private async refreshCalendarRange(start, end) {
    const apple = this.ctx.get('alcorAppleSync')
    if (!apple) return
    try { return await apple.invoke({ method: 'sync_range', arguments: { kind: 'calendar', start, end } }) }
    catch (error) { return { status: 'error', reason: error instanceof Error ? error.message : 'Apple 日程加载失败' } }
  }

  private queryCalendarRange(input) {
    return this.serialize(() => this.businessWorker.call('invoke', { domain: input.domain, method: input.method, arguments: input.arguments, request_id: input.requestId }))
  }

  @Remote
  async listInboundRecoveries(request) {
    const input = schemas.listInboundRecoveries[0].parse(request)
    const invocation = this.ctx.invocation
    if (invocation?.peer.principal.kind !== 'owner') throw new Error('恢复记录仅供所有者页面查看。')
    const signal = AbortSignal.any([invocation.signal, invocation.peer.signal])
    return this.serialize(() => {
      signal.throwIfAborted()
      return this.businessWorker.call('list_inbound_recoveries', { domain: input.domain })
    })
  }

  @Remote
  async restoreInboundRecovery(request) {
    const input = schemas.restoreInboundRecovery[0].parse(request)
    const invocation = this.ctx.invocation
    if (invocation?.peer.principal.kind !== 'owner') throw new Error('恢复操作仅供所有者页面使用。')
    const signal = AbortSignal.any([invocation.signal, invocation.peer.signal])
    return this.serialize(() => {
      signal.throwIfAborted()
      return this.businessWorker.call('restore_inbound_recovery', { domain: input.domain, recovery_id: input.recoveryId })
    })
  }

  // Host-only path: never expose an approval callback or prepared snapshot over Typert.
  approvedBillDelete(request, authorize) {
    if (this.ctx.invocation || typeof authorize !== 'function') throw new Error('删除需要真实会话的具体单次审批。')
    return this.serialize(() => {
      if (authorize() !== true) throw new Error('删除授权已失效。')
      return this.businessWorker.call('commit_bill_delete', { entry_id: request.id, operation_id: request.operation_id, revision: request.revision, snapshot: request.snapshot })
    })
  }

  // Host-only: prepared snapshots and authorization callbacks never cross Typert.
  prepareLocalDelete(request, authorize) {
    if (typeof authorize !== 'function') throw new Error('无法验证删除授权。')
    return this.serialize(() => {
      if (authorize() !== true) throw new Error('删除授权已失效。')
      return this.businessWorker.call('local_delete_prepare', request)
    })
  }

  commitLocalDelete(request, authorize) {
    if (this.ctx.invocation || typeof authorize !== 'function') throw new Error('删除需要真实会话的具体单次审批。')
    return this.serialize(() => {
      if (authorize() !== true) throw new Error('删除授权已失效。')
      return this.businessWorker.call('local_delete_commit', request)
    })
  }

  async prepareApprovedDelete(request, authorize) {
    if (this.ctx.invocation || typeof authorize !== 'function') throw new Error('删除需要真实会话的具体单次审批。')
    const { domain, method, arguments: args, operation_id } = request
    if (domain === 'bills' && method === 'delete_ledger') return this.serialize(async () => {
      if (authorize() !== true) throw new Error('删除授权已失效。')
      const receipt = await this.businessWorker.call('invoke', { domain, method: 'get_operation', arguments: { operationId: operation_id } })
      if (receipt) {
        const verified = await this.businessWorker.call('verify_bill_delete_replay', { entry_id: args.id, operation_id, ...(args.expectedRevision === undefined ? {} : { expected_revision: args.expectedRevision }) })
        if (authorize() !== true) throw new Error('删除授权已失效。')
        return { kind: 'receipt', request, plan: verified }
      }
      const row = await this.businessWorker.call('invoke', { domain, method: 'get_ledger', arguments: { id: args.id } })
      if (authorize() !== true || !row || row.id !== args.id || !Number.isSafeInteger(row.revision) || row.revision < 1
          || (args.expectedRevision !== undefined && args.expectedRevision !== row.revision)) throw new Error('账单已变化，请刷新后重新确认。')
      const snapshot = createHash('sha256').update(JSON.stringify(row)).digest('hex')
      return { kind: 'bill', request, plan: { id: row.id, snapshot, billSnapshot: row,
        targets: [{ id: row.id, title: billDeleteTitle(row) }] } }
    })
    if (!['tasks', 'calendar'].includes(domain)) throw new Error('未知的删除范围。')
    const task = domain === 'tasks', category = method === 'delete_category', external = method === 'delete_external_todo'
    const id = category && task ? args.category_id : external ? args.source_key : task ? args.todo_id : method === 'exclude_occurrence' ? args.series_id : args.id
    const prepared = await this.serialize(async () => {
      if (authorize() !== true) throw new Error('删除授权已失效。')
      const receipt = await this.businessWorker.call('prepare_delete_receipt', request)
      if (receipt) return { kind: 'receipt', request, plan: receipt }
      const row = external ? null : await this.businessWorker.call('invoke', { domain,
        method: category ? 'get_category' : task ? 'get_todo' : 'get_event',
        arguments: { [task ? category ? 'category_id' : 'todo_id' : 'id']: id } })
      if (authorize() !== true) throw new Error('删除授权已失效。')
      if (external || (row?.apple && !row.apple.planned)) {
        // The manager must distinguish import unlink from native category deletion first.
        if (category) return { kind: 'apple-category', request }
        const plan = await this.businessWorker.call('prepare_apple_delete', request)
        return { kind: plan.replayed ? 'receipt' : 'apple', request, plan }
      }
      if (!task && !category && args.occurrence_on) {
        const plan = await this.businessWorker.call('prepare_local_occurrence_delete', {
          event_id: id, occurrence_on: args.occurrence_on, scope: method === 'exclude_occurrence' ? 'this' : args.scope ?? 'this', operation_id,
        })
        return { kind: plan.replay ? 'receipt' : 'local-occurrence', request, plan }
      }
      const plan = await this.businessWorker.call('local_delete_prepare', {
        domain, target_id: id, operation_id, preserve_children: args.preserve_children ?? false, category,
      })
      return { kind: plan.replay ? 'receipt' : 'local', request, plan }
    })
    if (authorize() !== true) throw new Error('删除授权已失效。')
    if (!['apple', 'apple-category'].includes(prepared.kind)) return prepared
    const apple = this.ctx.get('alcorAppleSync')
    if (!apple) throw new Error('请先安装并启用 Apple 同步插件。')
    let live = this.deleteAuthorizers.get(authorize)
    if (!live) { live = () => this.active && authorize() === true; this.deleteAuthorizers.set(authorize, live) }
    // Do not hold the owner queue while the Apple worker calls back into that owner.
    const applePlan = await apple.prepareApprovedDelete({ domain, operation_id,
      ...(category ? { category_id: id } : { local_plan: prepared.plan }) }, live)
    if (authorize() !== true) throw new Error('删除授权已失效。')
    return { kind: applePlan.local_plan?.replayed ? 'receipt' : 'apple', request, plan: applePlan.local_plan, applePlan }
  }

  async commitApprovedDelete(prepared, authorize) {
    if (this.ctx.invocation || typeof authorize !== 'function' || authorize() !== true) throw new Error('删除需要真实会话的具体单次审批。')
    const { request, plan } = prepared, { domain, method, arguments: args, operation_id } = request
    if (prepared.kind === 'bill') return this.approvedBillDelete({ id: plan.id, operation_id, revision: plan.billSnapshot.revision, snapshot: plan.billSnapshot }, authorize)
    if (prepared.kind === 'local') return this.commitLocalDelete({ domain, target_id: plan.id, operation_id,
      snapshot: plan.snapshot, preserve_children: args.preserve_children ?? false, category: method === 'delete_category' }, authorize)
    if (prepared.kind === 'local-occurrence') return this.serialize(() => {
      if (authorize() !== true) throw new Error('删除授权已失效。')
      return this.businessWorker.call('commit_local_occurrence_delete', { event_id: plan.id, occurrence_on: plan.occurrence_on,
        scope: plan.scope, operation_id, snapshot: plan.snapshot })
    })
    if (prepared.kind !== 'apple') throw new Error('未知的已批准删除。')
    const apple = this.ctx.get('alcorAppleSync'), live = this.deleteAuthorizers.get(authorize)
    this.deleteAuthorizers.delete(authorize)
    if (!apple || !live || live() !== true) throw new Error('Apple 删除授权已失效。')
    if (prepared.applePlan.mode === 'local-import-unlink') return apple.commitApprovedDelete(prepared.applePlan, live)
    await this.serialize(() => {
      if (live() !== true) throw new Error('Apple 删除授权已失效。')
      return this.businessWorker.call('commit_apple_delete', { ...request, snapshot: plan.snapshot, native_snapshot: prepared.applePlan.native,
        confirmed_intent: { version: 1, plan_fingerprint: prepared.applePlan.plan_fingerprint } })
    })
    if (live() !== true) return { status: 'pending', operation_id, pending: true, message: '删除已排队，但会话授权已失效，尚未继续发送到 Apple。' }
    return apple.commitApprovedDelete(prepared.applePlan, live)
  }

  approvedDeletePeer(request, authorize) {
    if (this.ctx.invocation || typeof authorize !== 'function'
        || !['apple_begin_approved_delete', 'apple_finish_approved_delete', 'apple_reset_approved_delete', 'commit_import_category_delete'].includes(request.action)) throw new Error('未知的已批准删除回执。')
    return this.serialize(() => {
      if (authorize() !== true) throw new Error('删除授权已失效。')
      return this.businessWorker.call(request.action, request.input)
    })
  }

  approvedDeletePreparePeer(request, authorize) {
    if (this.ctx.invocation || typeof authorize !== 'function'
        || !['prepare_import_category_delete', 'prepare_apple_delete'].includes(request.action)) throw new Error('未知的删除预览。')
    return this.serialize(() => {
      if (authorize() !== true) throw new Error('删除会话已失效。')
      return this.businessWorker.call(request.action, request.input)
    })
  }

  prepareConfirmedDelete(request) {
    if (this.ctx.invocation || !this.active) throw new Error('已确认操作的续办入口不可用。')
    return this.serialize(() => {
      this.assertActive()
      return this.businessWorker.call('prepare_confirmed_delete', request)
    })
  }

  verifyConfirmedDeleteFinish(request) {
    if (this.ctx.invocation || !this.active) throw new Error('已确认操作的回执入口不可用。')
    return this.serialize(() => {
      this.assertActive()
      return this.businessWorker.call('verify_confirmed_delete_finish', request)
    })
  }

  async prepareApprovedExport(request, authorize) {
    this.assertActive()
    const apple = this.ctx.get('alcorAppleSync')
    if (!apple || this.ctx.invocation) throw new Error('Apple 导出审批入口不可用。')
    let live = this.exportAuthorizers.get(authorize)
    if (!live) { live = () => this.active && authorize() === true; this.exportAuthorizers.set(authorize, live) }
    return apple.prepareApprovedExport(request, live)
  }

  async commitApprovedExport(plan, authorize) {
    this.assertActive()
    const apple = this.ctx.get('alcorAppleSync')
    if (!apple || this.ctx.invocation) throw new Error('Apple 导出审批入口不可用。')
    const live = this.exportAuthorizers.get(authorize)
    this.exportAuthorizers.delete(authorize)
    if (!live) throw new Error('导出审批无效或已消费。')
    return apple.commitApprovedExport(plan, live)
  }

  @Remote
  async preparePageDelete(request) {
    return this.pageDeletes.preview(this.ctx.invocation, schemas.preparePageDelete[0].parse(request))
  }

  @Remote
  async commitPageDelete(request) {
    return this.pageDeletes.commit(this.ctx.invocation, schemas.commitPageDelete[0].parse(request))
  }

  @Remote
  async cancelPageDelete(request) {
    return this.pageDeletes.cancel(this.ctx.invocation, schemas.cancelPageDelete[0].parse(request))
  }

  private async businessRecords() {
    const current = await this.businessWorker.call('records')
    const days = new Map()
    for (const [,record] of this.domain.table('records').entries()) if (record.kind === 'briefing') days.set(record.date,record)
    for (const record of current) if (record.kind === 'briefing') days.set(record.date,record)
    return [...current.filter(record => record.kind !== 'briefing'), ...days.values()]
  }

  // Only the live explicit restore call may access these conditional owner methods.
  restoreAppleItem(request, authorize) {
    const apple = this.ctx.get('alcorAppleSync')
    if (this.ctx.invocation || !this.active || !apple || typeof authorize !== 'function') throw new Error('本地恢复入口不可用。')
    return apple.restoreItem(request, () => this.active && authorize() === true)
  }

  itemRestorePeer(request, authorize) {
    if (this.ctx.invocation || !this.active || typeof authorize !== 'function'
        || !['apple_capture_item_restore', 'apple_verify_item_restore', 'apple_restore_item'].includes(request.method)
        || !['tasks', 'calendar'].includes(request.domain)) throw new Error('未知的本地恢复调用。')
    return this.serialize(() => {
      if (!this.active || authorize() !== true) throw new Error('本地恢复会话已失效。')
      return this.businessWorker.call('item_restore_peer', request)
    })
  }

  boundConflictPeer(request, authorize) {
    if (this.ctx.invocation || !this.active || typeof authorize !== 'function'
        || !['apple_commit_bound_conflict', 'apple_verify_bound_conflict'].includes(request.method)
        || !['tasks', 'calendar'].includes(request.domain)) throw new Error('未知的具体冲突调用。')
    return this.serialize(() => {
      if (!this.active || authorize() !== true) throw new Error('冲突处理会话已失效。')
      return this.businessWorker.call('bound_conflict_peer', request)
    })
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

  private async effectiveSources(localOnly = false) {
    const [preferences, available, groups] = await Promise.all([this.businessWorker.call('source_settings'), localOnly ? Promise.resolve(localBriefingSources()) : discoverBriefingSources(this.ctx).then(catalog => catalog.sources), this.businessWorker.call('source_group_settings')])
    return effectiveBriefingSources(preferences, available, groups)
  }

  async localBriefingSources() {
    return this.effectiveSources(true)
  }

  private briefingAuthorization(sources) {
    const localOnly = sources.every(source => source.accountId === 'local' && localBriefingSources().some(local => local.source === source.source) && !source.tool && !source.method)
    return async () => {
      this.assertActive()
      const allowed = await this.effectiveSources(localOnly)
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
      parent, workspaceId: input.workspaceId, sources, trigger:input.trigger, authorize: this.briefingAuthorization(sources) })
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
    if (input.action === 'deleteInactive' || (input.action === 'edit' && input.request.action === 'delete')) {
      throw new Error(automationDeleteUnavailable)
    }
    if (input.action === 'catalog') {
      const owned = new Set([...this.domain.table('schedules').keys()])
      return (await this.ctx.schedule.catalog()).filter(entry => input.scope === 'all' || owned.has(entry.id))
        .map(entry => ({ ...entry, sourceLabel: owned.has(entry.id) ? 'Alcor' : 'DSH' }))
    }
    if (input.action === 'history') {
      if (input.scope === 'alcor' && !this.domain.table('schedules').get(input.request.id)) throw new Error('只能读取 Alcor 自动化。')
      return this.ctx.schedule.history(input.request)
    }
    return this.serialize(async () => {
      if (input.action === 'create') {
        if (input.scope === 'all') return this.ctx.schedule.create(input.request)
        const entry = await this.ctx.schedule.create({ ...input.request, target: alcorTarget(input.request.target, undefined) })
        try {
          this.assertActive()
          await this.domain.table('schedules').put(entry.id, { id: entry.id })
        } catch (error) {
          await this.ctx.schedule.edit({ id: entry.id, action: 'update', patch: { active: false } })
          throw error
        }
        return entry
      }
      const owned = this.domain.table('schedules').get(input.request.id)
      if (input.scope === 'alcor' && !owned) throw new Error('只能管理 Alcor 自动化。')
      if (input.action === 'run') return this.ctx.schedule.run(input.request)
      let change = input.request
      if (owned && change.action === 'update' && change.patch.target) {
        const current = await this.ctx.schedule.view({ id: change.id })
        if ('code' in current) return current
        change = { ...change, patch: { ...change.patch, target: alcorTarget(change.patch.target, current) } }
      }
      const result = await this.ctx.schedule.edit(change)
      return result
    })
  }

  // Public view/history do not reveal pendingDelivery; no host outcome proves deletion is safe.
  approvedAutomationDelete(_request, _authorize, _signal, _actorSessionId?) {
    throw new Error(automationDeleteUnavailable)
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
    return this.nativeSchedule(await this.automation({ scope: 'alcor', action: 'create', request: { title: input.title, prompt: input.prompt, timing } }))
  }

  @Remote
  async editSchedule(request) {
    const input = schemas.editSchedule[0].parse(request)
    const result = await this.automation({ scope: 'alcor', action: input.action === 'run' ? 'run' : 'edit', request: input.action === 'run' ? { id: input.id } : input.action === 'delete'
      ? { id: input.id, action: 'delete', expectedUpdatedAt: input.expectedUpdatedAt }
      : { id: input.id, action: 'update', patch: { active: input.action === 'resume' }, expectedUpdatedAt: input.expectedUpdatedAt } })
    if ('code' in result) throw new Error('任务已变化或不可执行，请刷新后重试。')
    return true
  }
}
