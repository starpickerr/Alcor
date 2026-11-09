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

export function normalizePosition(value) {
  return Array.isArray(value) && value.length === 2 && value.every(Number.isFinite)
    ? value.map(coordinate => Math.max(0, Math.min(1, coordinate))) : [1,1]
}

export function bearWorkspace(workspaces, defaultId) {
  return workspaces.items.find(item => item.workspaceId === defaultId)?.workspaceId
}

export function bearSession(workspace, id, catalog, workspaces) {
  const summary = catalog.byId[id]
  if (summary && !summary.origin && summary.projectionValues?.agentPreset === 'alcor'
    && !workspaces.archivedSessionIds.includes(id)
    && workspaces.items.some(item => item.workspaceId === workspace && item.sessionIds.includes(id))) return id
}

const prefix = 'alcor.bear.v1.'
export function createBearState(storage) {
  const memory = new Map(), listeners = new Set(), transient = new Set()
  const changed = new EventTarget(), sending = new Set()
  function notify(key) { changed.dispatchEvent(new Event(key)) }
  function read(key, fallback) {
    if (transient.has(key)) return memory.get(key) ?? fallback
    try { return JSON.parse(storage.getItem(prefix + key)) ?? memory.get(key) ?? fallback }
    catch { return memory.get(key) ?? fallback }
  }
  function write(key, value) {
    memory.set(key, value)
    try { storage.setItem(prefix + key, JSON.stringify(value)); transient.delete(key) } catch { transient.add(key) }
    notify(key)
  }
  function settings() {
    const value = read('settings', {})
    return {
      enabled:value.enabled !== false, hover:value.hover !== false, position:normalizePosition(value.position),
      name:typeof value.name === 'string' && value.name.trim() ? value.name.trim().slice(0,40) : 'Alcor 小熊',
      size:[48,60,84].includes(value.size) ? value.size : 60,
    }
  }
  let snapshot = settings()
  function refresh() { snapshot = settings(); for (const listener of listeners) listener() }
  const preferences = {
    getSnapshot:() => snapshot, subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    update(changes) { write('settings', {...settings(), ...changes}); refresh() },
  }
  return {read, write, preferences, sending,
    subscribe(key, fn) { changed.addEventListener(key, fn); return () => changed.removeEventListener(key, fn) },
    onStorage(event) {
      if (!event.key || event.key === prefix + 'settings') refresh()
      if (event.key?.startsWith(prefix)) notify(event.key.slice(prefix.length))
    },
  }
}
