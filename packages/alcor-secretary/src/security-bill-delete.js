import { createHash } from 'node:crypto'

// Host-side preparation for a specific bill deletion; never trust model-supplied approval flags.
export function billOperationId(execution, turn) {
  const sessionId = execution?.agent?.session?.id
  if (typeof sessionId !== 'string' || !sessionId || !Number.isSafeInteger(turn) || turn < 1
      || typeof execution?.callId !== 'string' || !execution.callId) throw new Error('无法确认操作身份。')
  return createHash('sha256').update(JSON.stringify([sessionId, turn, execution.callId])).digest('hex')
}

export async function prepareApprovedBillDeletion() {
  throw new Error('缺少可验证的本次人工确认通道，不可恢复删除暂不可用。')
}

// Retained for the isolated lifecycle tests; runtime dispatch uses the pre-execute preparation.
export async function approvedBillDeletion({ execution, business, approval, actorStillAuthorized, commitApproved, trustedTurn }) {
  if (typeof commitApproved !== 'function') throw new Error('无法验证账单删除请求。')
  const request = await prepareApprovedBillDeletion({ execution, business, approval, actorStillAuthorized, operationId: billOperationId(execution, trustedTurn) })
  const result = await commitApproved(request, () => actorStillAuthorized(execution) === true)
  return { result }
}
