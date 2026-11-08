import { symbols } from '@deepseek-ai/cordis'
import { billOperationId, prepareApprovedBillDeletion } from './security-bill-delete.js'

const providerIdentity = provider => provider?.[symbols.original] ?? provider

// Prototype only: the caller must register these hooks in the trusted host scope.
export function createBillDeleteGate({ business, getApproval, actorStillAuthorized, trustedTurnOf, commitApproved }) {
  const approved = new WeakMap()
  const inflight = new WeakMap()
  function release(execution) {
    const session = execution.agent?.session
    const entries = session && inflight.get(session)
    if (entries?.get(execution.callId) === execution) entries.delete(execution.callId)
    approved.delete(execution)
  }
  async function preExecute(execution, next) {
    const input = execution?.arguments
    if (execution?.name !== 'alcor_business' || input?.domain !== 'bills' || input?.method !== 'delete_ledger') return next()
    const session = execution.agent?.session
    if (!session || typeof execution.callId !== 'string' || !execution.callId) return { kind: 'deny', reason: '无法确认发起会话。' }
    let entries = inflight.get(session)
    if (!entries) { entries = new Map(); inflight.set(session, entries) }
    if (entries.has(execution.callId)) return { kind: 'deny', reason: '相同操作正在处理，请勿重复。' }
    entries.set(execution.callId, execution)
    let retained = false
    try {
      const host = await next()
      if (host.kind === 'deny' || host.kind === 'cancel') return host
      if (host.kind !== 'allow' && host.kind !== 'ask') return { kind: 'deny', reason: '宿主权限状态无法确认。' }
      let usedProvider
      const combinedApproval = {
        request: async request => {
          const approval = typeof getApproval === 'function' ? getApproval() : undefined
          if (!approval || typeof approval.request !== 'function') throw new Error('无法取得账单删除审批。')
          usedProvider = approval
          const reason = [host.kind === 'ask' ? host.reason : undefined, request.reason].filter(Boolean).join('\n')
          const displayReason = host.kind === 'ask' && host.displayReason
            ? Object.fromEntries(Object.entries(host.displayReason).map(([locale, text]) => [locale, `${text}\n${request.reason}`]))
            : undefined
          const outcome = await approval.request({ ...request, reason, ...(displayReason ? { displayReason } : {}) })
          if (providerIdentity(getApproval()) !== providerIdentity(approval)) throw new Error('审批服务已变化，账单未删除。')
          return outcome
        },
      }
      const turn = typeof trustedTurnOf === 'function' ? trustedTurnOf(execution) : undefined
      const operationId = billOperationId(execution, turn)
      const authorize = current => current.agent?.session === session && trustedTurnOf(current) === turn && actorStillAuthorized(current) === true && (!usedProvider || providerIdentity(getApproval()) === providerIdentity(usedProvider))
      const prepared = await prepareApprovedBillDeletion({ execution, business, approval: combinedApproval, actorStillAuthorized: authorize, operationId })
      if (prepared.replay && host.kind === 'ask') {
        const approval = typeof getApproval === 'function' ? getApproval() : undefined
        if (!approval || typeof approval.request !== 'function') return { kind: 'deny', reason: '无法取得宿主审批。' }
        usedProvider = approval
        const outcome = await approval.request({ agent: execution.agent, toolName: execution.name, callId: execution.callId,
          ...(host.reason ? { reason: host.reason } : {}), ...(host.displayReason ? { displayReason: host.displayReason } : {}), signal: execution.signal })
        if (providerIdentity(getApproval()) !== providerIdentity(approval) || outcome !== 'allowed-once') return { kind: 'deny', reason: '宿主审批未通过。' }
      }
      if (!authorize(execution) || execution.signal.aborted) return { kind: 'cancel' }
      approved.set(execution, { prepared, authorize })
      retained = true
      return { kind: 'allow' }
    } catch (error) {
      if (execution.signal.aborted) return { kind: 'cancel' }
      return { kind: 'deny', reason: error instanceof Error ? error.message : '账单删除未获批准。' }
    } finally { if (!retained) release(execution) }
  }
  async function execute(execution) {
    const state = approved.get(execution)
    approved.delete(execution)
    try {
      if (!state || !state.authorize(execution) || execution.signal.aborted) throw new Error('账单删除未获有效批准。')
      const { prepared: request, authorize } = state
      const result = await commitApproved(request, () => authorize(execution) && !execution.signal.aborted)
      return { result }
    } finally { release(execution) }
  }
  return { preExecute, execute, release }
}
