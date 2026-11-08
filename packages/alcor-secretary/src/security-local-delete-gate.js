import { isDeepStrictEqual } from 'node:util'
import { createHash } from 'node:crypto'
import { symbols } from '@deepseek-ai/cordis'
import { billOperationId } from './security-bill-delete.js'

const providerIdentity = provider => provider?.[symbols.original] ?? provider
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const identity = value => typeof value === 'string' && value.length > 0
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
export const billDeleteTitle = row => `${row.name}；${row.entry_type} ${row.amount_minor} ${row.currency} 最小货币单位；${row.occurred_at}；版本 ${row.revision}`
const label = value => JSON.stringify(String(value).replace(/[\u0000-\u001f\u007f-\u009f]/g, ' '))
const policyOf = (approval, session) => approval.overrideOf(session) ?? approval.config?.policy ?? 'ask'
const freeze = value => {
  if (value && typeof value === 'object') { for (const child of Object.values(value)) freeze(child); Object.freeze(value) }
  return value
}
export const isLocalDeletion = execution => execution?.name === 'alcor_business'
  && ((execution.arguments?.domain === 'tasks' && ['delete_todo', 'delete_category', 'delete_external_todo'].includes(execution.arguments?.method))
    || (execution.arguments?.domain === 'calendar' && ['delete_event', 'delete_category', 'exclude_occurrence'].includes(execution.arguments?.method))
    || (execution.arguments?.domain === 'bills' && execution.arguments?.method === 'delete_ledger'))

function deletionRequest(execution, turn) {
  const input = execution.arguments, { domain, method, arguments: args = {} } = input
  const task = domain === 'tasks', bill = domain === 'bills', category = method === 'delete_category', external = method === 'delete_external_todo', exclusion = method === 'exclude_occurrence'
  const keys = bill ? ['id', 'expectedRevision'] : category ? [task ? 'category_id' : 'id'] : external ? ['source_key', 'confirm', 'preserve_children'] : task ? ['todo_id', 'preserve_children'] : exclusion ? ['series_id', 'occurrence_on'] : ['id', 'scope', 'occurrence_on']
  const id = category && task ? args?.category_id : external ? args?.source_key : task ? args?.todo_id : exclusion ? args?.series_id : args?.id
  if (!record(args) || Object.keys(input).some(key => !['domain', 'method', 'arguments', 'requestId'].includes(key))
      || Object.keys(args).some(key => !keys.includes(key)) || !identity(id) || id.length > 200
      || (bill && args.expectedRevision !== undefined && (!Number.isSafeInteger(args.expectedRevision) || args.expectedRevision < 1))
      || (task && args.preserve_children !== undefined && typeof args.preserve_children !== 'boolean')
      || (external && args.preserve_children !== undefined && args.preserve_children !== true)
      || (external && args.confirm !== undefined && typeof args.confirm !== 'boolean')
      || (!task && args.scope !== undefined && !['this', 'future'].includes(args.scope))
      || (args.occurrence_on !== undefined && !/^\d{4}-\d{2}-\d{2}$/.test(args.occurrence_on))
      || (exclusion && !args.occurrence_on)) throw new Error('无法确认具体删除对象和范围。')
  const values = structuredClone(args)
  delete values.confirm
  return { domain, method, arguments: values, operation_id: billOperationId(execution, turn) }
}

export function frozenDeletion(proposal, request) {
  const result = structuredClone(proposal)
  if (!isDeepStrictEqual(result, JSON.parse(JSON.stringify(result)))) throw new Error('删除计划必须为完整 JSON 数据。')
  if (!record(result) || !['local', 'local-occurrence', 'apple', 'bill', 'receipt'].includes(result.kind)
      || !isDeepStrictEqual(result.request, request) || !record(result.plan)) throw new Error('无法验证删除计划。')
  const plan = result.plan, args = request.arguments
  const requestedId = request.domain === 'tasks' ? request.method === 'delete_category' ? args.category_id : args.todo_id
    : request.method === 'exclude_occurrence' ? args.series_id : args.id
  if ((requestedId !== undefined && plan.id !== undefined && plan.id !== requestedId)
      || (plan.operation_id !== undefined && plan.operation_id !== request.operation_id)) throw new Error('删除计划与请求对象不一致。')
  if (result.kind === 'receipt') {
    if (plan.replay !== true && plan.replayed !== true) throw new Error('无法验证已有操作回执。')
    return freeze(result)
  }
  if (!digest(plan.snapshot) || !identity(plan.id) || !Array.isArray(plan.targets) || !plan.targets.length || plan.targets.length > 500
      || plan.targets.some(row => !record(row) || !identity(row.id) || typeof row.title !== 'string')
      || !plan.targets.some(row => row.id === plan.id)
      || new Set(plan.targets.map(row => `${row.category === true ? 'category' : 'item'}:${row.id}`)).size !== plan.targets.length) throw new Error('无法确认全部删除对象，请缩小范围后重试。')
  if (result.kind === 'bill') {
    const bill = plan.billSnapshot
    if (request.domain !== 'bills' || request.method !== 'delete_ledger' || !record(bill)
        || bill.id !== plan.id || !Number.isSafeInteger(bill.revision) || bill.revision < 1
        || (args.expectedRevision !== undefined && args.expectedRevision !== bill.revision)
        || typeof bill.name !== 'string' || typeof bill.entry_type !== 'string' || typeof bill.currency !== 'string' || typeof bill.occurred_at !== 'string'
        || !Number.isSafeInteger(bill.amount_minor) || plan.targets.length !== 1 || plan.targets[0].title !== billDeleteTitle(bill)
        || plan.snapshot !== createHash('sha256').update(JSON.stringify(bill)).digest('hex')) throw new Error('无法验证账单删除快照。')
  }
  if (result.kind === 'local-occurrence' && (!['this', 'future'].includes(plan.scope) || !identity(plan.occurrence_on))) throw new Error('无法验证重复日程范围。')
  if (result.kind === 'apple') {
    const native = result.applePlan?.native, apple = plan.apple, host = result.applePlan
    if (!record(host) || host.version !== 1 || host.domain !== request.domain
        || host.kind !== (request.domain === 'tasks' ? 'reminders' : 'calendar') || host.operation_id !== request.operation_id
        || !digest(host.plan_fingerprint) || !isDeepStrictEqual(host.local_plan, plan)) throw new Error('无法验证 Apple 删除计划。')
    if (host.mode === 'local-import-unlink') {
      const link = host.link_snapshot
      if (request.method !== 'delete_category' || native !== undefined || !record(link)
          || link.direction !== 'import' || ![true, 1].includes(link.enabled) || link.alcor_id !== plan.id
          || !identity(link.apple_id) || !identity(host.source_id) || typeof link.apple_name !== 'string') throw new Error('无法验证停止导入的具体分类与来源。')
    } else if (host.mode !== undefined || !record(apple) || !record(native) || native.version !== 1
        || !identity(native.source_id) || !identity(native.group_id) || !identity(native.series_apple_id)
        || !digest(native.native_fingerprint) || !record(native.native_snapshot)
        || native.entity !== apple.entity || native.source_id !== apple.binding?.source_id
        || native.series_apple_id !== apple.binding?.apple_id
        || native.group_id !== (apple.entity === 'category' ? apple.binding?.apple_id : apple.binding?.group_id) || native.scope !== apple.scope
        || (native.occurrence_on ?? null) !== (apple.occurrence_on ?? null)
        || !['item', 'category', 'this', 'future'].includes(native.scope)) throw new Error('无法验证 Apple 删除目标及版本。')
  }
  return freeze(result)
}

export function deletionDetail(result) {
  const { plan } = result
  const unlink = result.kind === 'apple' && result.applePlan.mode === 'local-import-unlink'
  let detail = result.kind === 'bill' ? '永久删除以下账单，无法撤销；收支统计将同步更新：\n'
    : unlink ? `停止导入并删除以下 Alcor 本地分类及所列成员，Apple 不变。以后可通过重新选择该来源恢复导入：\n`
    : result.kind === 'local-occurrence' && plan.reversible === true
    ? `调整以下 ${plan.targets.length} 项本地重复日程的截止日期，保留原系列，可通过修改截止日期恢复：\n`
    : `删除以下 ${plan.targets.length} 项及所列范围。此操作可能无法恢复：\n`
  detail += plan.targets.map((row, index) => `${index + 1}. ${row.category ? '分类：' : ''}${label(row.title)}；ID ${label(row.id)}${row.recurring ? '；重复系列' : ''}${row.apple ? `；Apple 来源 ${label(row.apple.source_id ?? '')}；容器 ${label(row.apple.group_id ?? '')}；对象 ${label(row.apple.apple_id ?? '')}` : ''}`).join('\n')
  detail += `\n本地版本 ${plan.snapshot}`
  if (result.kind === 'local-occurrence') detail += `\n日期 ${label(plan.occurrence_on)}；范围 ${label(plan.scope)}。${plan.scope === 'future' ? '影响该系列从所选日期起及之后的日程。' : '仅跳过所选实例。'}`
  if (result.kind === 'apple') {
    if (unlink) return detail + `\nApple 来源 ${label(result.applePlan.source_id)}；分类 ${label(result.applePlan.link_snapshot.apple_name)}；容器 ${label(result.applePlan.link_snapshot.apple_id)}。停止此分类自动导入，不删除 Apple 容器或成员。`
    const n = result.applePlan.native
    detail += `\nApple 来源 ${label(n.source_id)}；容器 ${label(n.group_id)}；系列 ${label(n.series_apple_id)}；实例 ${label(n.instance_apple_id ?? '')}；日期 ${label(n.occurrence_on ?? '')}；范围 ${label(n.scope)}；Apple 版本 ${n.native_fingerprint}。`
    if (n.scope === 'future') detail += '影响此系列从所选实例起及之后的事件，可能涉及已修改例外。'
    if (n.scope === 'category') detail += '删除容器及其内容，可能包含无法穷举的重复事件或例外。'
    detail += '跨系统操作可能部分完成；结果未确认时不会自动重发。确认后，断线或重启会话不取消这次操作；目标、内容、范围和权限未变且确定尚未发送时会继续。可在 Apple 同步中取消此次操作或暂停同步。'
  }
  return detail
}

// The live host execution owns the grant; model arguments and Remote tickets never do.
export function createLocalDeleteGate({ prepare, commit, getApproval, actorStillAuthorized, trustedTurnOf }) {
  const prepared = new WeakMap(), checking = new WeakSet(), active = new WeakMap(), bindings = new WeakMap()
  const release = execution => {
    const binding = bindings.get(execution)
    if (binding) {
      binding.live = false
      const entries = active.get(binding.session)
      if (entries?.get(binding.callId) === execution) entries.delete(binding.callId)
    }
    bindings.delete(execution); prepared.delete(execution)
  }
  const guard = execution => {
    if (checking.has(execution) || (!isLocalDeletion(execution) && !bindings.has(execution))) return
    const state = prepared.get(execution)
    if (!state || !state.authorize()) return '此删除尚未获得有效的具体对象审批。'
  }
  async function preExecute(execution, next) {
    if (!isLocalDeletion(execution)) return next()
    const agent = execution.agent, session = agent?.session, turn = trustedTurnOf(execution)
    const { signal, callId, toolSet, name } = execution, sessionId = session?.id
    if (!session || !identity(sessionId) || !Number.isSafeInteger(turn) || turn < 1 || !identity(callId) || !signal) return { kind: 'deny', reason: '无法确认删除会话。' }
    let entries = active.get(session)
    if (!entries) { entries = new Map(); active.set(session, entries) }
    if (entries.has(callId)) return { kind: 'deny', reason: '相同删除操作正在处理。' }
    entries.set(callId, execution)
    const binding = { session, callId, live: true }; bindings.set(execution, binding)
    let retained = false, approval, input, deadline = Infinity
    const authorize = () => {
      checking.add(execution)
      try {
        return binding.live && execution.agent === agent && agent.session === session && session.id === sessionId
          && execution.name === name && execution.callId === callId && execution.toolSet === toolSet
          && execution.signal === signal && !signal.aborted && trustedTurnOf(execution) === turn
          && (input === undefined || JSON.stringify(execution.arguments) === input) && Date.now() < deadline
          && actorStillAuthorized(execution) === true
          && (!approval || (providerIdentity(getApproval()) === providerIdentity(approval) && policyOf(approval, session) === 'ask'))
      } catch { return false }
      finally { checking.delete(execution) }
    }
    try {
      input = JSON.stringify(execution.arguments)
      const host = await next()
      if (host.kind === 'deny' || host.kind === 'cancel') return host
      if (!['allow', 'ask'].includes(host.kind) || !authorize()) throw new Error('当前会话无权删除。')
      const request = deletionRequest(execution, turn)
      const result = frozenDeletion(await prepare(request, authorize), request)
      if (!authorize()) throw new Error('删除授权已失效。')
      // A previous receipt is read-only. It never grants another dispatch, even if pending.
      if (result.kind !== 'receipt') {
        approval = getApproval()
        if (!approval || typeof approval.request !== 'function' || typeof approval.overrideOf !== 'function' || !authorize()) throw new Error('当前会话没有可用的删除审批，请核对 DSH 审批设置。')
        const detail = deletionDetail(result)
        const reason = [host.kind === 'ask' ? host.reason : undefined, detail].filter(Boolean).join('\n')
        const displayReason = host.kind === 'ask' && host.displayReason
          ? Object.fromEntries(Object.entries(host.displayReason).map(([locale, text]) => [locale, [text, detail].filter(Boolean).join('\n')])) : undefined
        if (reason.length > 120000 || Object.values(displayReason ?? {}).some(text => text.length > 120000)) throw new Error('删除预览过大，请缩小范围。')
        const outcome = await approval.request({ agent, toolName: name, callId, reason, ...(displayReason ? { displayReason } : {}), signal })
        if (outcome !== 'allowed-once') throw new Error('删除未获单次批准，未执行。')
      }
      if (!authorize()) throw new Error('删除授权已失效。')
      deadline = Date.now() + 4 * 60 * 1000
      prepared.set(execution, { result, authorize }); retained = true
      return { kind: 'allow' }
    } catch (error) {
      return signal.aborted ? { kind: 'cancel' } : { kind: 'deny', reason: error instanceof Error ? error.message : '删除未获批准。' }
    } finally { if (!retained) release(execution) }
  }
  async function execute(execution) {
    const state = prepared.get(execution); prepared.delete(execution)
    try {
      if (!state || !state.authorize()) throw new Error('删除未获有效批准。')
      return { result: state.result.kind === 'receipt' ? state.result.plan : await commit(state.result, state.authorize) }
    } finally { release(execution) }
  }
  return { preExecute, execute, release, guard }
}
