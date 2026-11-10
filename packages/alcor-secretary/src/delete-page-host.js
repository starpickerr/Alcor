import { randomBytes, randomUUID } from 'node:crypto'
import { frozenDeletion, deletionDetail } from './security-local-delete-gate.js'
import { confirmMacDeletion } from './mac-delete-confirmation.js'

// A page ticket only retains a preview. The separate local prompt grants this commit.
export function createDeletePageHost({ prepare, commit, confirm = confirmMacDeletion, now = Date.now }) {
  const tickets = new Map()
  let active = true
  const context = invocation => {
    if (!active || invocation?.peer?.principal?.kind !== 'owner') throw new Error('删除预览仅供所有者使用。')
    const signal = AbortSignal.any([invocation.signal, invocation.peer.signal]); signal.throwIfAborted()
    return { peer: invocation.peer, signal }
  }
  const remove = (ticket, state) => { state.phase = 'closed'; state.controller.abort(); tickets.delete(ticket) }
  return {
    async preview(invocation, input) {
      const { peer, signal } = context(invocation), category = input.kind === 'category', task = input.domain === 'tasks'
      const bill = input.domain === 'bills'
      if ((bill && (category || input.scope || input.occurrenceOn || input.excludeOccurrence || !Number.isSafeInteger(input.expectedRevision) || input.expectedRevision < 1)) || (!bill && input.expectedRevision !== undefined)) throw new Error('请明确本次删除范围。')
      if (((task || category) && (input.scope || input.occurrenceOn || input.excludeOccurrence))
          || (input.excludeOccurrence && (!input.occurrenceOn || input.scope === 'future'))) throw new Error('请明确本次删除范围。')
      const request = { domain: input.domain, method: bill ? 'delete_ledger' : category ? 'delete_category' : task ? 'delete_todo' : input.excludeOccurrence ? 'exclude_occurrence' : 'delete_event',
        arguments: { [task ? category ? 'category_id' : 'todo_id' : input.excludeOccurrence ? 'series_id' : 'id']: input.id,
          ...(bill && input.expectedRevision !== undefined ? { expectedRevision: input.expectedRevision } : {}),
          ...(!task && !category && input.occurrenceOn ? { occurrence_on: input.occurrenceOn } : {}),
          ...(!task && !category && !input.excludeOccurrence && input.scope ? { scope: input.scope } : {}) }, operation_id: randomBytes(32).toString('hex') }
      for (const [id, state] of tickets) if (state.expires <= now() || state.peer.signal.aborted) remove(id, state)
      if (tickets.size >= 30) throw new Error('待确认预览过多，请关闭旧预览后重试。')
      const ticket = randomUUID(), state = { peer, phase: 'prepare', signal, expires: now() + 300000, controller: new AbortController() }
      const authorize = () => active && peer.principal.kind === 'owner' && !peer.signal.aborted && !state.controller.signal.aborted
        && !state.signal?.aborted && state.expires > now() && ['prepare', 'approved'].includes(state.phase)
      try {
        const prepared = frozenDeletion(await prepare(request, authorize), request)
        if (prepared.kind === 'receipt') throw new Error('该操作已有回执，请刷新后核对。')
        signal.throwIfAborted()
        if (!authorize()) throw new Error('删除预览已失效。')
        state.phase = 'waiting'; state.signal = undefined
        Object.assign(state, { prepared, authorize }); tickets.set(ticket, state)
        return { ticket, targets: prepared.plan.targets, snapshot: prepared.plan.snapshot,
          ...(prepared.kind === 'local-occurrence' ? { occurrence: { scope: prepared.plan.scope, occurrence_on: prepared.plan.occurrence_on } } : {}),
          ...(prepared.applePlan?.native ? { apple: prepared.applePlan.native } : {}),
          ...(prepared.applePlan?.mode === 'local-import-unlink' ? { importUnlink: { source_id: prepared.applePlan.source_id,
            apple_id: prepared.applePlan.link_snapshot.apple_id, apple_name: prepared.applePlan.link_snapshot.apple_name } } : {}) }
      } catch (error) { remove(ticket, state); throw error }
    },
    async commit(invocation, { ticket }) {
      const { peer, signal } = context(invocation), state = tickets.get(ticket)
      if (!state || state.peer !== peer || state.phase !== 'waiting' || state.expires <= now()) throw new Error('删除预览已失效，请重新预览。')
      state.phase = 'confirming'; state.signal = AbortSignal.any([signal, state.controller.signal])
      try {
        if (await confirm({ version: 1, operation_id: state.prepared.request.operation_id, detail: deletionDetail(state.prepared) }, state.signal) !== true) throw new Error('删除已取消或未获确认，未执行。')
        state.phase = 'approved'
        if (!state.authorize()) throw new Error('删除确认已失效，未执行。')
        return await commit(state.prepared, state.authorize)
      } finally { remove(ticket, state) }
    },
    cancel(invocation, { ticket }) {
      const { peer } = context(invocation), state = tickets.get(ticket)
      if (!state || state.peer !== peer) return false
      remove(ticket, state); return true
    },
    close() { active = false; for (const [ticket, state] of tickets) remove(ticket, state) },
  }
}
