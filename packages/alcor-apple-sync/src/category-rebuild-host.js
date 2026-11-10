import { randomUUID } from 'node:crypto'

// Same owner-peer-bound, five-minute one-use confirmation pattern as organizer page deletion.
// The Host retains the prepared scope. A request never supplies its own approved snapshot.
export function createCategoryRebuildHost(worker, now = Date.now) {
  const tickets = new Map()
  let active = true
  function context(invocation) {
    if (!active || invocation?.peer?.principal?.kind !== 'owner') throw new Error('创建空 Apple 容器仅供所有者页面确认。')
    const peer = invocation.peer, signal = AbortSignal.any([invocation.signal, peer.signal])
    signal.throwIfAborted()
    return { peer, signal }
  }
  return {
    async preview(invocation, input) {
      const { peer, signal } = context(invocation)
      const prepared = await worker().call('preview_category_rebuild', input)
      signal.throwIfAborted()
      if (!active || peer.principal.kind !== 'owner') throw new Error('页面已关闭，请重新预览。')
      for (const [ticket, state] of tickets) if (state.expires <= now() || state.peer.signal.aborted) tickets.delete(ticket)
      if (tickets.size >= 100) throw new Error('待确认预览过多，请稍后重试。')
      if (prepared?.kind !== input.kind || prepared.apple_id !== input.apple_id || prepared.source_id !== input.source_id
          || prepared.mode !== 'empty_container_only' || !/^[a-f0-9]{64}$/.test(prepared.snapshot)) throw new Error('分类预览身份不完整。')
      const ticket = randomUUID()
      tickets.set(ticket, { peer, expires: now() + 300000, input: { ...input, snapshot: prepared.snapshot } })
      return { ...prepared, ticket }
    },
    async commit(invocation, { ticket }) {
      const { peer, signal } = context(invocation), state = tickets.get(ticket)
      if (!state || state.peer !== peer || state.expires <= now()) throw new Error('确认已失效，请重新预览。')
      tickets.delete(ticket)
      signal.throwIfAborted()
      // owner is also used by API operators. A ticket proves scope, not a human decision.
      // Keep the production send closed until official Desktop UI / allowed-once evidence is wired.
      throw new Error('创建尚未获得可信页面或单次批准凭据，未向 Apple 发送；本地成员已保留。')
    },
    cancel(invocation, { ticket }) {
      const { peer } = context(invocation), state = tickets.get(ticket)
      if (!state || state.peer !== peer) return false
      tickets.delete(ticket)
      return true
    },
    close() { active = false; tickets.clear() },
  }
}
