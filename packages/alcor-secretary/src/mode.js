const KEY = 'alcor.product-mode.v1'
const modes = ['alcor', 'dsh']
const oldPanels = { stella: 'alcor-tasks', 'stella-history': 'alcor-tasks', 'stella-attention': 'alcor-tasks', 'stella-agents': 'alcor-tasks', 'stella-sources': 'alcor-briefing', 'stella-tasks': 'alcor-tasks', 'stella-calendar': 'alcor-calendar', 'stella-briefing': 'alcor-briefing', 'stella-bills': 'alcor-bills', 'stella-schedules': 'alcor-schedules' }
export const legacyPanelIds = Object.keys(oldPanels)
export const canonicalPanel = panel => Object.hasOwn(oldPanels, panel) ? oldPanels[panel] : panel
const ownPanels = new Set(Object.values(oldPanels))
export function startSecretarySession(ctx, prompt) {
  return ctx.uiWorkspace.startSession(undefined, {
    agentPreset: 'alcor',
    ...(prompt ? { prompt, whenDraftExists: 'append' } : {}),
  })
}

function loadModeState(storage) {
  const fallback = { mode: 'dsh', locations: {} }
  try {
    let current = storage.getItem(KEY)
    let source = current ?? storage.getItem('stella.product-mode.v1')
    if (source === null) return { state: fallback, writable: true, expected: current }
    function parse(raw) {
      const value = JSON.parse(raw)
      if (!value || !['stella', ...modes].includes(value.mode) || (value.locations != null && (typeof value.locations !== 'object' || Array.isArray(value.locations)))) throw new Error('Invalid mode state')
      const locations = { ...value.locations }
      for (const key of ['stella', ...modes]) {
        const location = locations[key]
        if (location !== undefined && (!location || typeof location !== 'object' || Array.isArray(location) || !(location.panel === null || typeof location.panel === 'string') || (location.sessionId !== undefined && typeof location.sessionId !== 'string'))) throw new Error('Invalid mode location')
      }
      if (locations.alcor === undefined && locations.stella !== undefined) {
        locations.alcor = locations.stella
        delete locations.stella
      }
      for (const key of modes) if (locations[key]) locations[key] = { ...locations[key], panel: canonicalPanel(locations[key].panel) }
      return { ...value, mode: value.mode === 'stella' ? 'alcor' : value.mode, locations }
    }
    let state = parse(source)
    if (current === null) {
      // A newer destination wins if it appeared while reading the old key.
      const newer = storage.getItem(KEY)
      if (newer !== null) { current = newer; state = parse(newer) }
      else {
        const normalized = JSON.stringify(state)
        try { storage.setItem(KEY, normalized); current = normalized } catch { /* Keep the legacy source and continue in memory. */ }
      }
    }
    return { state, writable: true, expected: current }
  } catch { return { state: fallback, writable: false, expected: undefined } }
}

export function readModeState(storage) { return loadModeState(storage).state }

export function createModeController(ctx, storage) {
  const loaded = loadModeState(storage), state = loaded.state
  const listeners = new Set()
  let expected = loaded.expected
  let navigating = false
  let waitingForCatalog = false
  let waitingForPanel = false
  const protectedLocations = new Set()
  let notice = loaded.writable ? '' : '已保存的模式状态无法读取，原数据已保留。'
  const panelRegistered = panel => !ctx.slots?.entries || ctx.slots.entries('main').some(entry => entry.options.key === panel)
  function persist() {
    if (!loaded.writable) return
    try {
      // Avoid overwriting another window's already-observed update. This is not an atomic compare-and-swap.
      if (storage.getItem(KEY) !== expected) { notice = '另一窗口已更新模式位置，本窗口的更改未保存。'; return }
      const next = JSON.stringify(state)
      storage.setItem(KEY, next)
      expected = next
    } catch { notice = '当前模式无法保存到此设备。' }
  }
  function saveLocation(fromEvent = false) {
    if (navigating || waitingForCatalog || waitingForPanel) return
    if (protectedLocations.has(state.mode)) {
      if (!fromEvent) return
      protectedLocations.delete(state.mode)
    }
    const { sessionId } = ctx.uiWorkspace.selection.getSnapshot()
    const active = ctx.layout.panelInfo.getSnapshot().activePanelId
    const panel = canonicalPanel(active)
    if (active !== panel && panelRegistered(panel)) {
      navigating = true
      try { ctx.layout.selectPanel(panel) } catch { /* Retain the canonical target for the next restore. */ }
      finally { navigating = false }
    }
    state.locations[state.mode] = { ...state.locations[state.mode], panel, sessionId }
    persist()
  }
  function restore() {
    waitingForCatalog = false
    waitingForPanel = false
    navigating = true
    try {
      const saved = state.locations[state.mode]
      const panel = saved ? saved.panel : state.mode === 'alcor' ? 'alcor-tasks' : 'dsh'
      if (saved?.sessionId) {
        const catalog = ctx.sessions.list.getSnapshot()
        if (catalog.phase === 'pending') { waitingForCatalog = true; return }
        if (!catalog.byId[saved.sessionId]) throw new Error('Session no longer available')
      }
      if (ownPanels.has(panel) && !panelRegistered(panel)) { waitingForPanel = true; return }
      if (saved?.sessionId) ctx.uiWorkspace.openSession(saved.sessionId)
      ctx.layout.selectPanel(panel)
      protectedLocations.delete(state.mode)
      persist()
    } catch {
      protectedLocations.add(state.mode)
      notice = '上次页面已不可用，已返回模式首页。'
      const fallback = state.mode === 'alcor' ? 'alcor-tasks' : 'dsh'
      if (ownPanels.has(fallback) && !panelRegistered(fallback)) { waitingForPanel = true; return }
      ctx.layout.selectPanel(fallback)
    } finally { navigating = false }
  }
  const stopPreset = ctx.on('ui-workspace/default-preset', async () => {
    if (state.mode === 'alcor') return 'alcor'
    const result = await ctx.remote.agentPresets.list()
    const preset = result.ok && result.value.presets.find(item => item.isDefault)
    if (!preset) throw new Error('无法读取 DSH 默认 Agent，请稍后重试。')
    return preset.id
  })
  const stopPanel = ctx.layout.panelInfo.subscribe(() => saveLocation(true))
  const stopSelection = ctx.uiWorkspace.selection.subscribe(() => saveLocation(true))
  const notify = () => { for (const listener of listeners) listener() }
  const stopCatalog = ctx.sessions.list.subscribe(() => {
    if (waitingForCatalog && ctx.sessions.list.getSnapshot().phase === 'ready') { restore(); notify() }
  })
  const stopRegistry = ctx.slots?.subscribe?.('main', () => {
    if (waitingForPanel) { restore(); notify() }
  }) ?? (() => {})
  if (state.locations[state.mode]) restore()
  else saveLocation()
  return {
    getSnapshot: () => state.mode,
    subscribe: listener => { listeners.add(listener); return () => listeners.delete(listener) },
    get notice() { return notice },
    switchMode(mode) {
      if (!modes.includes(mode)) throw new Error('Unknown product mode')
      if (mode === state.mode) return
      saveLocation()
      state.mode = mode
      notice = loaded.writable ? '' : '已保存的模式状态无法读取，原数据已保留。'
      restore()
      saveLocation()
      if (!waitingForCatalog && !waitingForPanel) persist()
      notify()
    },
    dispose() { stopPreset(); stopPanel(); stopSelection(); stopCatalog(); stopRegistry(); listeners.clear() },
  }
}
