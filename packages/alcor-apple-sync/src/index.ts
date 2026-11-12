import {modeConfig} from '../../alcor-plugin-info/src/host-policy.js'
import {publishApplication} from '../../alcor-plugin-info/src/assignments.js'
import { registerBundledSkills } from '../../alcor-organizer/src/skills.js'
import { Service } from '@deepseek-ai/cordis'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { dshHomePath } from '@deepseek-ai/dsh-home-paths'
import { createHash, randomUUID } from 'node:crypto'
import { join } from 'node:path'
import { isDeepStrictEqual } from 'node:util'
import { BusinessWorker, resolveBusinessPython } from '../../alcor-organizer/src/business.js'
import { frozenExportPlan, applePlanDetail } from '../../alcor-secretary/src/security-apple-export-gate.js'
import { confirmMacDeletion } from '../../alcor-secretary/src/mac-delete-confirmation.js'
import { createCategoryRebuildHost } from './category-rebuild-host.js'
import { remote, requestSchema, itemRestoreSchema, boundConflictSchema, rebuildPreviewSchema, rebuildCommitSchema } from './remote.js'
import { initializeProfile } from '../../alcor-organizer/src/profile-migration.js'

function assertCompatibleProfile(ctx) {
  const selected = ctx.get('profileContext')?.startedBundles
  if (!Array.isArray(selected)) throw new Error('无法确认当前 Profile 的插件，已停止 Alcor 启动。')
  if (selected.includes('@stella/apple-sync')) throw new Error('旧版 Stella 插件与 Alcor 不能同时启用，请先迁移 Profile。')
}

export default class AppleSync extends TypertRemoteService {
  static Config=modeConfig('alcor')
  static inject = ['credentials', 'typert', 'alcorOrganizer']
  private worker
  private active = true
  private rebuildPage = createCategoryRebuildHost(() => this.worker)
  private itemRestores = new Map()
  private confirmConflict = confirmMacDeletion
  private preparedExports = new Map()
  private runningExports = new Map()
  private preparedDeletes = new Map()
  private preparingDeletes = new Map()
  private runningDeletes = new Map()

  constructor(ctx) { assertCompatibleProfile(ctx); super(ctx, 'alcorAppleSync', { namespace: 'alcorAppleSync' }) }

  async [Service.init]() {
    assertCompatibleProfile(this.ctx)
    publishApplication(this.ctx)
    await initializeProfile(this.ctx)
    registerBundledSkills(this.ctx, 'alcor-apple-sync', new URL('../skills/', import.meta.url))
    const profile = this.ctx.get('profileContext')?.dir ?? dshHomePath('profiles', 'default')
    this.worker = new BusinessWorker(join(profile, 'alcor', 'apple-sync'), {
      worker: new URL('../python/worker.py', import.meta.url),
      python: await resolveBusinessPython(),
      onRequest: request => request?.route === 'item_restore' ? this.authorizeItemRestore(request)
        : ['item_restore_prepare', 'item_restore_owner'].includes(request?.route) ? this.itemRestoreOwner(request)
        : request?.route === 'bound_conflict_owner' ? this.boundConflictOwner(request)
        : request?.route === 'approved_export' ? this.authorizeExportDispatch(request)
        : request?.route === 'approved_delete' ? this.authorizeDeleteDispatch(request)
        : request?.route === 'approved_delete_prepare' ? this.approvedDeletePrepare(request)
        : request?.route === 'approved_delete_continue' ? this.continueDelete(request)
        : request?.route === 'approved_delete_owner' ? this.approvedDeleteOwner(request) : this.ctx.alcorOrganizer.applePeer(request),
      diagnostic: message => this.ctx.logger.warn(message),
    })
    this.ctx.effect(() => () => { this.active = false; this.preparedExports.clear(); this.runningExports.clear(); this.itemRestores.clear(); this.preparingDeletes.clear(); this.preparedDeletes.clear(); this.runningDeletes.clear(); this.rebuildPage.close(); this.worker.close() })
    await this.worker.call('initialize', { legacy_directory: join(profile, 'alcor', 'business') })
    this.ctx.typert.register({ package: '@alcor/apple-sync', face: 'host', schemas: [], invocations: remote.descriptors, model: { services: [], events: [], objects: [] } })
  }

  // The plan stays inside one explicit call. It cannot authorize a later background run.
  async restoreItem(request, authorize) {
    if (this.ctx.invocation) throw new Error('请使用本地恢复页面入口。')
    return this.runItemRestore(request, authorize)
  }

  private async runItemRestore(request, authorize) {
    const { operation_id, ...target } = request, input = itemRestoreSchema.parse(target)
    if (!this.active || typeof authorize !== 'function' || authorize() !== true
        || !/^[a-f0-9]{64}$/.test(operation_id ?? '')) throw new Error('本地恢复会话已失效。')
    if (this.itemRestores.has(operation_id)) throw new Error('此恢复正在处理。')
    let live = true
    const worker = this.worker, state = { ...input, operation_id, phase: 'prepare', capture: undefined, plan: undefined,
      authorize: () => live && this.active && this.worker === worker && authorize() === true }
    this.itemRestores.set(operation_id, state)
    try {
      const proposal = await worker.call('prepare_item_restore', { ...input, operation_id })
      const finish = proposal?.mode === 'receipt_finish', plan = finish ? proposal.plan : proposal
      if (!state.authorize() || plan?.version !== 1 || !/^[a-f0-9]{64}$/.test(plan.operation_id ?? '')
          || (!finish && (plan.operation_id !== operation_id || !state.capture))
          || plan.kind !== input.kind || plan.local_id !== input.identity
          || !plan.owner_expected || typeof plan.owner_expected !== 'object' || Array.isArray(plan.owner_expected)
          || !/^[a-f0-9]{64}$/.test(plan.plan_fingerprint ?? '')) throw new Error('无法验证缺失项目的本地恢复计划。')
      if (plan.operation_id !== operation_id) {
        if (this.itemRestores.has(plan.operation_id)) throw new Error('此恢复回执正在处理。')
        this.itemRestores.set(plan.operation_id, state)
      }
      state.operation_id = plan.operation_id; state.plan = structuredClone(plan); state.phase = finish ? 'receipt_finish' : 'run'
      try { return await worker.call('run_item_restore', { kind: input.kind,
        plan: finish ? { mode: 'receipt_finish', plan: structuredClone(state.plan) } : structuredClone(state.plan) }) }
      catch { return { status: 'needs_review', operation_id: plan.operation_id, message: '本地恢复结果待核对；不会重复写入，重新选择恢复时先核对已有回执。' } }
    } finally {
      live = false
      this.itemRestores.delete(operation_id)
      if (this.itemRestores.get(state.operation_id) === state) this.itemRestores.delete(state.operation_id)
    }
  }

  private authorizeItemRestore(request) {
    const state = this.itemRestores.get(request?.operation_id)
    if (!state || state.phase !== 'run' || request.route !== 'item_restore'
        || Object.keys(request).some(key => !['route', 'kind', 'operation_id', 'plan_fingerprint', 'local_id'].includes(key))
        || request.kind !== state.kind || request.plan_fingerprint !== state.plan.plan_fingerprint || request.local_id !== state.identity) return false
    try { return state.authorize() === true } catch { return false }
  }

  private itemRestoreOwner(request) {
    const state = this.itemRestores.get(request?.operation_id), capture = request.method === 'apple_capture_item_restore'
    const preparing = request.route === 'item_restore_prepare'
    const keys = ['route', 'kind', 'operation_id', 'local_id', 'method', 'args', 'kwargs', ...(preparing ? [] : ['plan_fingerprint'])]
    if (!state || Object.keys(request).some(key => !keys.includes(key)) || request.kind !== state.kind
        || request.local_id !== state.identity || !isDeepStrictEqual(request.kwargs, {}) || !Array.isArray(request.args)
        || (preparing ? state.phase !== 'prepare' || !capture : request.route !== 'item_restore_owner' || !['run', 'receipt_finish'].includes(state.phase)
          || request.plan_fingerprint !== state.plan.plan_fingerprint)
        || (state.phase === 'receipt_finish' && request.method !== 'apple_verify_item_restore')
        || (capture ? request.args.length !== 5 || request.args[0] !== state.identity
          || (state.capture !== undefined && !isDeepStrictEqual(request.args, state.capture))
          : !['apple_verify_item_restore', 'apple_restore_item'].includes(request.method)
            || !isDeepStrictEqual(request.args, [state.operation_id, state.plan.owner_expected]))
        || state.authorize() !== true) throw new Error('恢复调用不属于当前具体项目。')
    if (capture && state.capture === undefined) state.capture = structuredClone(request.args)
    return this.ctx.alcorOrganizer.itemRestorePeer({ domain: state.kind === 'reminders' ? 'tasks' : 'calendar',
      method: request.method, args: request.args, kwargs: {} }, state.authorize)
  }

  // Owner is the existing data-access boundary, not proof of a human approval.
  @Remote
  restoreLocalItem(request) {
    const input = itemRestoreSchema.parse(request), invocation = this.ctx.invocation
    if (invocation?.peer?.principal?.kind !== 'owner') throw new Error('本地恢复仅供所有者页面使用。')
    const peer = invocation.peer, signal = AbortSignal.any([invocation.signal, peer.signal])
    return this.runItemRestore({ ...input, operation_id: createHash('sha256').update(randomUUID()).digest('hex') },
      () => !signal.aborted && peer.principal.kind === 'owner')
  }

  // Owner identifies the data-access caller; only the separate prompt approves an Apple write.
  @Remote
  async resolveItemConflict(request) {
    const input = boundConflictSchema.parse(request), invocation = this.ctx.invocation
    if (invocation?.peer?.principal?.kind !== 'owner') throw new Error('冲突处理仅供所有者页面使用。')
    const peer = invocation.peer, signal = AbortSignal.any([invocation.signal, peer.signal])
    const operation_id = createHash('sha256').update(randomUUID()).digest('hex')
    let live = true, phase = 'prepare', prepared
    const authorize = () => live && this.active && !signal.aborted && peer.principal.kind === 'owner' && phase !== 'confirming'
    try {
      prepared = await this.prepareExport({ method: 'resolve_conflict', arguments: input, operation_id }, authorize)
      if (input.choice === 'keep_alcor' && prepared.mode !== 'receipt_finish') {
        phase = 'confirming'
        if (await this.confirmConflict({ version: 1, operation_id, kind: 'apple-update', detail: applePlanDetail(prepared) }, signal) !== true)
          throw new Error('Apple 更新已取消或未获确认，未执行。')
      }
      phase = 'approved'
      return await this.commitExport(prepared, authorize)
    } finally {
      live = false
      this.preparedExports.delete(prepared?.mode === 'receipt_finish' ? prepared.plan.operation_id : operation_id)
    }
  }

  // Host-only methods: these are intentionally absent from Remote descriptors.
  async prepareApprovedExport(request, authorize) {
    if (this.ctx.invocation) throw new Error('请使用具体页面处理入口。')
    return this.prepareExport(request, authorize)
  }

  private async prepareExport(request, authorize) {
    if (!this.active || typeof authorize !== 'function' || authorize() !== true) throw new Error('操作需要当前有效会话。')
    const proposal = frozenExportPlan(await this.worker.call('prepare_approved_export', request), request)
    const plan = proposal.mode === 'receipt_finish' ? proposal.plan : proposal
    if (authorize() !== true) throw new Error('操作会话已失效。')
    const now = Date.now()
    for (const [id, state] of this.preparedExports) if (state.expires <= now) this.preparedExports.delete(id)
    if (this.preparedExports.has(plan.operation_id) || this.runningExports.has(plan.operation_id)) throw new Error('相同操作正在处理。')
    this.preparedExports.set(plan.operation_id, { serialized: JSON.stringify(proposal), authorize, expires: now + 300000 })
    return proposal
  }

  async commitApprovedExport(proposal, authorize) {
    if (this.ctx.invocation) throw new Error('请使用具体页面处理入口。')
    return this.commitExport(proposal, authorize)
  }

  private async commitExport(proposal, authorize) {
    const finish = proposal.mode === 'receipt_finish', plan = finish ? proposal.plan : proposal
    const state = this.preparedExports.get(plan.operation_id)
    this.preparedExports.delete(plan.operation_id)
    if (!this.active || !state || state.authorize !== authorize || state.expires <= Date.now()
        || state.serialized !== JSON.stringify(proposal) || authorize() !== true) throw new Error('操作范围无效或已消费。')
    let live = true
    const worker = this.worker
    this.runningExports.set(plan.operation_id, { ...state, plan, finish, fingerprint: plan.plan_fingerprint, initial: true,
      authorize: () => live && this.active && this.worker === worker && authorize() === true,
      items: new Set(plan.items.map(item => item.local_id)) })
    try { return await worker.call('run_approved_export', { plan: proposal }) }
    catch { return { status: 'needs_review', operation_id: plan.operation_id, message: '操作结果未确认，不会自动重发；请核对已有回执和两侧状态。' } }
    finally { live = false; this.runningExports.delete(plan.operation_id) }
  }

  private boundConflictOwner(request) {
    const state = this.runningExports.get(request?.operation_id), plan = state?.plan, item = plan?.items?.[0]
    const [operation, suppliedItem, choice, native] = request?.args ?? []
    const nativeKey = plan?.kind === 'calendar' ? 'event_id' : 'reminder_id', groupKey = plan?.kind === 'calendar' ? 'calendar_id' : 'list_id'
    if (!state || plan.mode !== 'conflict' || request.route !== 'bound_conflict_owner'
        || Object.keys(request).some(key => !['route', 'kind', 'operation_id', 'plan_fingerprint', 'local_id', 'method', 'args', 'kwargs'].includes(key))
        || request.kind !== plan.kind || request.plan_fingerprint !== plan.plan_fingerprint || request.local_id !== item.local_id
        || !['apple_commit_bound_conflict', 'apple_verify_bound_conflict'].includes(request.method)
        || (state.finish && request.method !== 'apple_verify_bound_conflict')
        || !Array.isArray(request.args) || request.args.length !== 4 || !isDeepStrictEqual(request.kwargs, {})
        || operation !== plan.operation_id || !isDeepStrictEqual(suppliedItem, item) || choice !== plan.arguments.choice
        || (choice === 'keep_apple' ? !isDeepStrictEqual(native, item.native_snapshot) : !native || typeof native !== 'object'
          || native[nativeKey] !== item.native_snapshot[nativeKey] || native[groupKey] !== item.group_id || native.source_id !== item.source_id)
        || state.expires <= Date.now() || state.authorize() !== true) throw new Error('冲突写入不属于当前具体操作。')
    return this.ctx.alcorOrganizer.boundConflictPeer({ domain: plan.kind === 'calendar' ? 'calendar' : 'tasks', method: request.method,
      args: request.args, kwargs: {} }, state.authorize)
  }

  private authorizeExportDispatch(request) {
    const state = this.runningExports.get(request.operation_id)
    if (!state || state.finish || Object.keys(request).some(key => !['route', 'operation_id', 'plan_fingerprint', 'local_id'].includes(key))
        || request.plan_fingerprint !== state.fingerprint || state.expires <= Date.now()) return false
    try { if (state.authorize() !== true) return false } catch { return false }
    if (request.local_id === null) {
      if (!state.initial) return false
      state.initial = false
      return true
    }
    if (state.initial || !state.items.has(request.local_id)) return false
    state.items.delete(request.local_id)
    return true
  }

  // A Host-only approval callback remains live through this exact worker run.
  async prepareApprovedDelete(request, authorize) {
    if (this.ctx.invocation || typeof authorize !== 'function' || authorize() !== true) throw new Error('删除需要真实会话的具体单次审批。')
    if (this.preparingDeletes.has(request.operation_id) || this.preparedDeletes.has(request.operation_id)
        || this.runningDeletes.has(request.operation_id)) throw new Error('相同删除操作正在处理。')
    this.preparingDeletes.set(request.operation_id, { request: structuredClone(request), authorize })
    let plan
    try { plan = await this.worker.call('prepare_approved_delete', request) }
    finally { this.preparingDeletes.delete(request.operation_id) }
    if (authorize() !== true || plan?.version !== 1 || plan.operation_id !== request.operation_id
        || plan.domain !== request.domain || (request.category_id ? plan.local_plan?.id !== request.category_id
          : !isDeepStrictEqual(plan.local_plan, request.local_plan))
        || !/^[a-f0-9]{64}$/.test(plan.plan_fingerprint ?? '')) throw new Error('无法验证 Apple 删除计划。')
    const now = Date.now()
    for (const [id, state] of this.preparedDeletes) if (state.expires <= now) this.preparedDeletes.delete(id)
    if (this.preparedDeletes.has(plan.operation_id) || this.runningDeletes.has(plan.operation_id)) throw new Error('相同删除操作正在处理。')
    this.preparedDeletes.set(plan.operation_id, { serialized: JSON.stringify(plan), authorize, expires: now + 300000 })
    return plan
  }

  async commitApprovedDelete(plan, authorize) {
    const state = this.preparedDeletes.get(plan.operation_id)
    this.preparedDeletes.delete(plan.operation_id)
    if (this.ctx.invocation || !state || state.authorize !== authorize || state.expires <= Date.now()
        || state.serialized !== JSON.stringify(plan) || authorize() !== true) throw new Error('删除审批无效或已消费。')
    this.runningDeletes.set(plan.operation_id, { ...state, domain: plan.domain, mode: plan.mode,
      snapshot: plan.local_plan.snapshot, fingerprint: plan.plan_fingerprint, localId: plan.local_plan.id })
    try { return await this.worker.call('run_approved_delete', { plan }) }
    catch { return { status: 'needs_review', operation_id: plan.operation_id, pending: true, message: '删除结果未确认，不会自动重发；请先核对 Apple 和本地状态。' } }
    finally { this.runningDeletes.delete(plan.operation_id) }
  }

  private authorizeDeleteDispatch(request) {
    const state = this.runningDeletes.get(request.operation_id)
    if (!state || state.mode === 'receipt_finish' || Object.keys(request).some(key => !['route', 'operation_id', 'plan_fingerprint', 'local_id'].includes(key))
        || request.route !== 'approved_delete' || request.plan_fingerprint !== state.fingerprint
        || request.local_id !== state.localId || state.expires <= Date.now()) return false
    // A retry may ask again only within this live run. The owner/manager must prove
    // that the same operation has not been sent; an unknown receipt is never replayed.
    try { return state.authorize() === true } catch { return false }
  }

  private approvedDeleteOwner(request) {
    const state = this.runningDeletes.get(request.operation_id)
    const unlink = state?.mode === 'local-import-unlink'
    const allowed = state?.mode === 'receipt_finish' ? ['apple_finish_approved_delete'] : unlink ? ['commit_import_category_delete'] : ['apple_begin_approved_delete', 'apple_finish_approved_delete', 'apple_reset_approved_delete']
    const argKeys = unlink ? ['operation_id', 'category_id', 'snapshot'] : request.action === 'apple_finish_approved_delete' ? ['operation_id'] : ['operation_id', 'document', 'binding']
    if (!state || Object.keys(request).some(key => !['route', 'action', 'operation_id', 'plan_fingerprint', 'local_id', 'args'].includes(key))
        || request.route !== 'approved_delete_owner' || !allowed.includes(request.action)
        || !request.args || typeof request.args !== 'object' || Array.isArray(request.args)
        || request.args.operation_id !== request.operation_id
        || Object.keys(request.args).some(key => !argKeys.includes(key))
        || (unlink && (request.args.category_id !== state.localId || request.args.snapshot !== state.snapshot))
        || request.plan_fingerprint !== state.fingerprint || request.local_id !== state.localId
        || state.expires <= Date.now()) throw new Error('Apple 删除回执不属于当前已批准操作。')
    return this.ctx.alcorOrganizer.approvedDeletePeer({ action: request.action, input: { ...request.args, domain: state.domain } }, state.authorize)
  }

  private approvedDeletePrepare(request) {
    const state = this.preparingDeletes.get(request?.args?.operation_id), frozen = state?.request
    const expected = request.action === 'prepare_import_category_delete'
      ? { domain: frozen?.domain, category_id: frozen?.category_id, operation_id: frozen?.operation_id }
      : { domain: frozen?.domain, method: 'delete_category', arguments: { [frozen?.domain === 'tasks' ? 'category_id' : 'id']: frozen?.category_id }, operation_id: frozen?.operation_id }
    if (!state || !frozen.category_id || request.route !== 'approved_delete_prepare'
        || Object.keys(request).some(key => !['route', 'action', 'args'].includes(key))
        || !['prepare_import_category_delete', 'prepare_apple_delete'].includes(request.action)
        || !isDeepStrictEqual(request.args, expected) || state.authorize() !== true) throw new Error('删除预览不属于当前具体对象。')
    return this.ctx.alcorOrganizer.approvedDeletePreparePeer({ action: request.action, input: request.args }, state.authorize)
  }

  private async continueDelete(request) {
    if (!this.active || this.ctx.invocation || request?.route !== 'approved_delete_continue') throw new Error('续办入口不可用。')
    if (request.phase === 'end') {
      const state = this.runningDeletes.get(request.operation_id)
      if (Object.keys(request).some(key => !['route', 'phase', 'operation_id', 'plan_fingerprint', 'local_id'].includes(key))
          || !state?.continuation || request.plan_fingerprint !== state.fingerprint || request.local_id !== state.localId) return false
      this.runningDeletes.delete(request.operation_id)
      return true
    }
    const finish = request.phase === 'finish_begin'
    const keys = finish ? ['route', 'phase', 'domain', 'operation_id', 'plan'] : ['route', 'phase', 'domain', 'operation_id']
    if ((!finish && request.phase !== 'begin') || Object.keys(request).some(key => !keys.includes(key))
        || !['tasks', 'calendar'].includes(request.domain) || !/^[a-f0-9]{64}$/.test(request.operation_id ?? '')) throw new Error('未知的已确认操作。')
    const now = Date.now()
    for (const [id, state] of this.runningDeletes) if (state.expires <= now) this.runningDeletes.delete(id)
    if (this.runningDeletes.has(request.operation_id) || this.preparingDeletes.has(request.operation_id)) throw new Error('该删除操作正在执行。')
    // This is a persisted, explicitly confirmed operation, not a replayed SDK grant.
    const plan = finish ? structuredClone(request.plan)
      : await this.ctx.alcorOrganizer.prepareConfirmedDelete({ domain: request.domain, operation_id: request.operation_id })
    if (!this.active || plan?.version !== 1 || plan.domain !== request.domain || plan.operation_id !== request.operation_id
        || plan.kind !== (request.domain === 'tasks' ? 'reminders' : 'calendar') || !plan.local_plan?.id
        || !/^[a-f0-9]{64}$/.test(plan.plan_fingerprint ?? '') || !plan.native) throw new Error('无法验证待续办的具体删除计划。')
    if (finish && await this.ctx.alcorOrganizer.verifyConfirmedDeleteFinish({ domain: request.domain, operation_id: request.operation_id, plan }) !== true) throw new Error('无法验证已完成的具体删除回执。')
    if (!this.active || this.runningDeletes.has(plan.operation_id)) throw new Error('该删除操作正在执行或已停止。')
    const frozen = JSON.parse(JSON.stringify(plan)), worker = this.worker
    this.runningDeletes.set(plan.operation_id, { continuation: true, mode: finish ? 'receipt_finish' : undefined, serialized: JSON.stringify(frozen), domain: plan.domain,
      fingerprint: plan.plan_fingerprint, localId: plan.local_plan.id, snapshot: plan.local_plan.snapshot,
      expires: now + 300000, authorize: () => this.active && this.worker === worker })
    return frozen
  }

  @Remote
  previewCategoryRebuild(request) {
    return this.rebuildPage.preview(this.ctx.invocation, rebuildPreviewSchema.parse(request))
  }

  @Remote
  rebuildCategory(request) {
    return this.rebuildPage.commit(this.ctx.invocation, rebuildCommitSchema.parse(request))
  }

  @Remote
  cancelCategoryRebuild(request) { return this.rebuildPage.cancel(this.ctx.invocation, rebuildCommitSchema.parse(request)) }

  @Remote
  invoke(request) {
    const input = requestSchema.parse(request), args = input.arguments
    if ((['one_time', 'transfer_batch'].includes(input.method) && args.direction === 'export')
        || (['resolve_conflict', 'resolve_category_conflict'].includes(input.method) && ['keep_alcor', 'keep_apple'].includes(args.choice))
        || (['sync', 'start_sync'].includes(input.method) && ['confirm_deletions', 'confirm_local_deletions'].some(key => args[key] !== undefined && args[key] !== false))) {
      throw new Error('此操作缺少真实会话的具体单次审批；页面或 confirm 参数不能授予权限。')
    }
    const result = this.worker.call('invoke', input)
    return input.method === 'catalog' ? result.then(rows => [...rows.map(row => row.name === 'resolve_conflict' ? { ...row, description: '用户当前对话处理双方仍存在且精确绑定的单条冲突；keep_apple 仅本地条件更新，keep_alcor 须本次具体外写审批。页面使用 resolveItemConflict，普通 invoke 不开放；未知结果只核已有回执。' } : row), { name: 'restore_local_item',
      parameters: '(kind: calendar | reminders, identity: local item ID)',
      description: '仅在本地项目缺失、Apple 原项目仍存在且来源已关联时恢复 Alcor，不向 Apple 写入。由用户当前 alcor_business 调用；所有者页面使用 restoreLocalItem 专用入口。' }]) : result
  }
}
