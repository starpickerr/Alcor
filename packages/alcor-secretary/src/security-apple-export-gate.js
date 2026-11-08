import { isDeepStrictEqual } from 'node:util'
import { symbols } from '@deepseek-ai/cordis'
import { billOperationId } from './security-bill-delete.js'

const providerIdentity = provider => provider?.[symbols.original] ?? provider
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const identity = value => typeof value === 'string' && value.length > 0
const policyOf = (approval, session) => approval.overrideOf(session) ?? approval.config?.policy ?? 'ask'
const freeze = value => {
  if (value && typeof value === 'object') {
    for (const child of Object.values(value)) freeze(child)
    Object.freeze(value)
  }
  return value
}
export const isAppleExport = execution => execution?.name === 'alcor_business'
  && execution.arguments?.domain === 'apple'
  && ['one_time', 'transfer_batch'].includes(execution.arguments?.method)
  && execution.arguments?.arguments?.direction === 'export'

export const isApplePlanOperation = execution => isAppleExport(execution) || (execution?.name === 'alcor_business'
  && execution.arguments?.domain === 'apple' && execution.arguments?.method === 'resolve_conflict')

function exportArguments(input) {
  const args = structuredClone(input.arguments), kind = args?.kind ?? 'calendar'
  if (input.method === 'resolve_conflict') {
    const key = kind === 'calendar' ? 'alcor_id' : 'todo_id'
    if (!record(args) || !['calendar', 'reminders'].includes(kind) || !identity(args[key])
        || !['keep_apple', 'keep_alcor'].includes(args.choice)
        || Object.keys(input).some(key => !['domain', 'method', 'arguments', 'requestId'].includes(key))
        || Object.keys(args).some(field => !['kind', key, 'choice'].includes(field))) throw new Error('无法验证具体冲突目标。')
    return { kind, identity: args[key], choice: args.choice }
  }
  const allowed = input.method === 'one_time'
    ? ['kind', 'direction', kind === 'reminders' ? 'list_id' : 'calendar_id', 'item_ids', 'confirm']
    : ['kind', 'direction', 'selections', 'start', 'end', 'token', 'confirm', ...(kind === 'reminders' ? ['include_undated', 'include_completed'] : [])]
  if (!record(args) || !['calendar', 'reminders'].includes(kind) || args.direction !== 'export'
      || Object.keys(input).some(key => !['domain', 'method', 'arguments', 'requestId'].includes(key))
      || Object.keys(args).some(key => !allowed.includes(key))
      || (args.confirm !== undefined && typeof args.confirm !== 'boolean')
      || (args.token !== undefined && typeof args.token !== 'string')) throw new Error('导出参数无法验证。')
  if (input.method === 'one_time') {
    if (!identity(args[kind === 'reminders' ? 'list_id' : 'calendar_id']) || !Array.isArray(args.item_ids)
        || !args.item_ids.length || args.item_ids.some(id => !identity(id))
        || new Set(args.item_ids).size !== args.item_ids.length) throw new Error('无法验证导出目标。')
  } else if (!Array.isArray(args.selections) || !args.selections.length
      || args.selections.some(row => !record(row) || Object.keys(row).some(key => !['apple_id', 'alcor_id'].includes(key)) || !identity(row.apple_id) || !identity(row.alcor_id))
      || new Set(args.selections.map(row => row.apple_id)).size !== args.selections.length
      || new Set(args.selections.map(row => row.alcor_id)).size !== args.selections.length
      || !/^\d{4}-\d{2}-\d{2}$/.test(args.start ?? '') || !/^\d{4}-\d{2}-\d{2}$/.test(args.end ?? '')
      || ['include_undated', 'include_completed'].some(key => args[key] !== undefined && typeof args[key] !== 'boolean')) throw new Error('无法验证导出范围。')
  // Legacy preview tokens and model confirmation flags never become approval.
  delete args.confirm; delete args.token
  return { ...args, kind }
}

export function frozenExportPlan(proposal, request) {
  const copy = structuredClone(proposal)
  if (!isDeepStrictEqual(copy, JSON.parse(JSON.stringify(copy)))) throw new Error('导出计划必须为完整 JSON 数据。')
  freeze(copy)
  const finish = copy?.mode === 'receipt_finish', plan = finish ? copy.plan : copy, items = plan?.items
  const conflict = request.method === 'resolve_conflict'
  if (!record(plan) || (finish ? !conflict || !/^[a-f0-9]{64}$/.test(plan.operation_id ?? '')
        || Object.keys(copy).some(key => !['mode', 'plan'].includes(key)) : plan.operation_id !== request.operation_id)
      || plan.kind !== request.arguments.kind
      || plan.mode !== (conflict ? 'conflict' : request.method === 'one_time' ? 'one_time' : 'batch')
      || !/^[a-f0-9]{64}$/.test(plan.plan_fingerprint ?? '') || !Array.isArray(items)
      || !Number.isSafeInteger(plan.skipped) || plan.skipped < 0
      || items.some(item => !record(item) || !identity(item.local_id) || !identity(item.source_id) || !identity(item.group_id)
        || !(conflict ? ['update', 'local_update'] : ['create', 'update']).includes(item.action) || !record(item.wire_payload)
        || !record(item.owner_snapshot) || !record(item.destination_snapshot)
        || (item.action === 'create' ? item.mapping_snapshot !== null || item.native_snapshot !== null
          : !['one_time', 'conflict'].includes(plan.mode) || !record(item.mapping_snapshot) || !record(item.native_snapshot)))
      || new Set(items.map(item => item.local_id)).size !== items.length) throw new Error('无法验证完整导出计划。')
  if (conflict && (plan.method !== request.method || !isDeepStrictEqual(plan.arguments, request.arguments) || items.length !== 1 || plan.skipped !== 0
      || items[0].local_id !== request.arguments.identity
      || items[0].action !== (request.arguments.choice === 'keep_apple' ? 'local_update' : 'update')
      || (request.arguments.choice === 'keep_apple' && !record(items[0].owner_inbound_snapshot)))) throw new Error('无法验证完整冲突计划。')
  if (items.length > 500) throw new Error('一次最多导出 500 项，请分批处理。')
  return copy
}

function calendarWriteScope(plan, item) {
  if (plan.kind !== 'calendar' || item.action !== 'update') return ''
  const payload = item.wire_payload
  return `Apple 写入范围：${payload.series === true ? '本次及此后日程' : '仅本次日程'}；起始实例 ${JSON.stringify(payload.occurrence_date ?? item.native_snapshot?.occurrence_date)}；系列/对象 ${JSON.stringify(payload.event_id)}。\n`
}

export function applePlanDetail(proposal) {
  const plan = proposal.mode === 'receipt_finish' ? proposal.plan : proposal
  if (plan.mode === 'conflict') {
    const item = plan.items[0]
    return `${proposal.mode === 'receipt_finish' ? '仅核对原操作回执，不再次写入。' : plan.arguments.choice === 'keep_apple' ? '采用 Apple 内容更新 Alcor 本地项目，Apple 保持不变。' : '采用 Alcor 内容更新下列已绑定 Apple 项目。不会新建、移动或删除项目。结果未知时不会重发。'}\n`
      + calendarWriteScope(plan, item) + `本地 ID ${JSON.stringify(item.local_id)}；Apple 来源 ${JSON.stringify(item.source_id)}；容器 ${JSON.stringify(item.group_id)}；目标 ${JSON.stringify(item.destination_snapshot)}；原绑定 ${JSON.stringify(item.mapping_snapshot)}；当前 Apple 内容 ${JSON.stringify(item.native_snapshot)}；本次写入内容 ${JSON.stringify(item.wire_payload)}`
  }
  return `将向 Apple ${plan.kind === 'calendar' ? '日历' : '提醒事项'}导出 ${plan.items.length} 项，跳过 ${plan.skipped} 项。可能部分完成；开始后无法保证撤回。\n`
    + plan.items.map((item, index) => calendarWriteScope(plan, item) + `${index + 1}. ${item.action === 'create' ? '新增' : '更新'}；本地 ID ${JSON.stringify(item.local_id)}；Apple 来源 ${JSON.stringify(item.source_id)}；容器 ${JSON.stringify(item.group_id)}；目标 ${JSON.stringify(item.destination_snapshot)}${item.action === 'update' ? `；更新对象 ${JSON.stringify(item.mapping_snapshot)}` : ''}；发送内容 ${JSON.stringify(item.wire_payload)}`).join('\n')
}

// Only the exact live host execution retains this single-use grant and frozen plan.
export function createAppleExportGate({ prepare, commit, getApproval, actorStillAuthorized, trustedTurnOf }) {
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
    if (checking.has(execution) || (!isApplePlanOperation(execution) && !bindings.has(execution))) return
    const state = prepared.get(execution)
    if (!state || !state.authorize()) return isAppleExport(execution) ? '此 Apple 导出尚未获得有效的具体目标审批。' : '此 Apple 操作缺少有效的当前会话或具体目标范围。'
  }
  async function preExecute(execution, next) {
    if (!isApplePlanOperation(execution)) return next()
    const agent = execution.agent, session = agent?.session, turn = trustedTurnOf(execution)
    const { signal, callId, toolSet, name } = execution, sessionId = session?.id
    if (!session || !identity(sessionId) || !Number.isSafeInteger(turn) || turn < 1 || !identity(callId) || !signal) return { kind: 'deny', reason: '无法确认导出会话。' }
    let entries = active.get(session)
    if (!entries) { entries = new Map(); active.set(session, entries) }
    if (entries.has(callId)) return { kind: 'deny', reason: '相同导出正在处理。' }
    entries.set(callId, execution)
    const binding = { session, callId, live: true }
    bindings.set(execution, binding)
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
      if (!['allow', 'ask'].includes(host.kind) || !authorize()) throw new Error('当前会话无权导出。')
      const args = exportArguments(execution.arguments)
      if (execution.arguments.method !== 'resolve_conflict') {
        approval = getApproval()
        if (!approval || typeof approval.request !== 'function' || typeof approval.overrideOf !== 'function' || !authorize()) throw new Error('无法取得导出审批。')
      }
      const request = { method: execution.arguments.method, arguments: args, operation_id: billOperationId(execution, turn) }
      const plan = frozenExportPlan(await prepare(request, authorize), request)
      if (!authorize()) throw new Error('操作会话已失效。')
      if (plan.mode === 'receipt_finish' || (plan.mode === 'conflict' && args.choice === 'keep_apple')) {
        deadline = Date.now() + 4 * 60 * 1000
        prepared.set(execution, { plan, authorize }); retained = true
        // Preserve any normal Host ask. This local choice adds no destructive approval.
        return host
      }
      approval = getApproval()
      if (!approval || typeof approval.request !== 'function' || typeof approval.overrideOf !== 'function' || !authorize()) throw new Error('无法取得导出审批。')
      const detail = applePlanDetail(plan)
      const reason = [host.kind === 'ask' ? host.reason : undefined, detail].filter(Boolean).join('\n')
      const displayReason = host.kind === 'ask' && host.displayReason
        ? Object.fromEntries(Object.entries(host.displayReason).map(([locale, text]) => [locale, [text, detail].filter(Boolean).join('\n')])) : undefined
      if (reason.length > 120000 || Object.values(displayReason ?? {}).some(text => text.length > 120000)) throw new Error('导出预览过大，请分批处理。')
      const outcome = await approval.request({ agent, toolName: name, callId, reason, ...(displayReason ? { displayReason } : {}), signal })
      if (outcome !== 'allowed-once') throw new Error('导出未获单次批准。')
      if (!authorize()) throw new Error('导出授权已失效。')
      deadline = Date.now() + 4 * 60 * 1000
      prepared.set(execution, { plan, authorize })
      retained = true
      return { kind: 'allow' }
    } catch (error) {
      return signal.aborted ? { kind: 'cancel' } : { kind: 'deny', reason: error instanceof Error ? error.message : '导出未获批准。' }
    } finally { if (!retained) release(execution) }
  }
  async function execute(execution) {
    const state = prepared.get(execution)
    if (!state) throw new Error('导出未获有效批准。')
    prepared.delete(execution)
    try {
      if (!state.authorize()) throw new Error('导出未获有效批准。')
      return { result: await commit(state.plan, state.authorize) }
    } finally { release(execution) }
  }
  return { preExecute, execute, release, guard }
}
