const internalPresets = new Set(['alcor-delivery', 'alcor-briefing', 'stella', 'stella-briefing', 'qq-group-chat'])

export function visiblePresetStore(store) {
  let previous, filtered
  return {
    subscribe: listener => store.subscribe(listener),
    getSnapshot() {
      const state = store.getSnapshot()
      if (previous !== state) {
        previous = state
        filtered = {...state, options:state.options.filter(item => !internalPresets.has(item.id))}
      }
      return filtered
    },
  }
}

// Keep the native picker (including keyboard navigation, translations and selection).
export function installPresetFilter(ctx) {
  const name = 'conversation.hero.agentPreset'
  ctx.slots.inject(name, () => ctx.effect(() => {
    let source, release, active = true
    function reconcile() {
      if (!active) return
      const entry = ctx.slots.entries(name).find(row => row.locale === 'settings.agentPreset' && row.options.priority === undefined)
      if (entry === source) return
      source = entry
      release?.()
      release = undefined
      if (!entry) return
      const stores = new WeakMap()
      release = ctx.slots.register({name, locale:entry.locale, priority:-10, inject(...args) {
        const props = entry.inject(...args)
        const seat = props.hooks.agentPresetSeat
        if (!stores.has(seat)) stores.set(seat, visiblePresetStore(seat))
        return {...props, hooks:{...props.hooks, agentPresetSeat:stores.get(seat)}}
      }}, entry.component)
    }
    const stop = ctx.slots.subscribe(name, () => queueMicrotask(reconcile))
    reconcile()
    return () => { active = false; stop(); release?.() }
  }))
}
