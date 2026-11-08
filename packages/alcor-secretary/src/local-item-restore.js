import { billOperationId } from './security-bill-delete.js'

// Low-impact local recovery, bound only to the current user execution, not an approval grant.
export async function restoreLocalItem(execution, { trustedTurnOf, actorStillAuthorized, restore }) {
  const { agent, signal, callId, toolSet, name } = execution, session = agent?.session
  const turn = trustedTurnOf(execution), input = JSON.stringify(execution.arguments)
  if (!session?.id || !callId || !signal || !Number.isSafeInteger(turn) || turn < 1) throw new Error('恢复需要当前用户会话。')
  const sessionId = session.id
  let live = true
  const authorize = () => {
    try { return live && execution.agent === agent && agent.session === session && session.id === sessionId
      && execution.signal === signal && !signal.aborted && execution.callId === callId
      && execution.name === name && execution.toolSet === toolSet && trustedTurnOf(execution) === turn
      && JSON.stringify(execution.arguments) === input && actorStillAuthorized(execution) === true
    } catch { return false }
  }
  try {
    if (!authorize()) throw new Error('恢复需要当前用户会话。')
    const result = await restore({ ...execution.arguments.arguments, operation_id: billOperationId(execution, turn) }, authorize)
    return { result }
  } finally { live = false }
}
