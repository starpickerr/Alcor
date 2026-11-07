const KEY = 'stella.product-mode.v1'
const modes = ['stella', 'dsh']
export function startSecretarySession(ctx, prompt) {
  return ctx.uiWorkspace.startSession(undefined, {
    agentPreset: 'stella',
    ...(prompt ? { prompt, whenDraftExists: 'append' } : {}),
  })
}

export function readModeState(storage) {
  try {
    const value = JSON.parse(storage.getItem(KEY))
    if (!modes.includes(value?.mode)) throw new Error('Invalid mode')
    const locations = {}
    for (const mode of modes) {
      const location = value.locations?.[mode]
      if (location && (location.panel === null || typeof location.panel === 'string')
        && (location.sessionId === undefined || typeof location.sessionId === 'string')) {
        locations[mode] = { panel: mode === 'stella' && location.panel === 'stella-sources' ? 'stella-briefing' : mode === 'stella' && ['stella', 'stella-history', 'stella-attention', 'stella-agents'].includes(location.panel) ? 'stella-tasks' : location.panel, sessionId: location.sessionId }
      }
    }
    return { mode: value.mode, locations }
  } catch { return { mode: 'dsh', locations: {} } }
}

export function createModeController(ctx, storage) {
  const state = readModeState(storage)
  const listeners = new Set()
  let navigating = false
  let waitingForCatalog = false
  let notice = ''
  function persist() {
    try { storage.setItem(KEY, JSON.stringify(state)) }
    catch { notice = '当前模式无法保存到此设备。' }
  }
  function saveLocation() {
    if (navigating || waitingForCatalog) return
    const { sessionId } = ctx.uiWorkspace.selection.getSnapshot()
    state.locations[state.mode] = { panel: ctx.layout.panelInfo.getSnapshot().activePanelId, sessionId }
    persist()
  }
  function restore() {
    waitingForCatalog = false
    navigating = true
    try {
      const saved = state.locations[state.mode]
      if (saved?.sessionId) {
        const catalog = ctx.sessions.list.getSnapshot()
        if (catalog.phase === 'pending') { waitingForCatalog = true; return }
        if (!catalog.byId[saved.sessionId]) throw new Error('Session no longer available')
        ctx.uiWorkspace.openSession(saved.sessionId)
      }
      ctx.layout.selectPanel(saved ? saved.panel : state.mode === 'stella' ? 'stella-tasks' : 'dsh')
    } catch {
      notice = '上次页面已不可用，已返回模式首页。'
      ctx.layout.selectPanel(state.mode === 'stella' ? 'stella-tasks' : 'dsh')
    } finally { navigating = false }
  }
  const stopPreset = ctx.on('ui-workspace/default-preset', async () => {
    if (state.mode === 'stella') return 'stella'
    const result = await ctx.remote.agentPresets.list()
    const preset = result.ok && result.value.presets.find(item => item.isDefault)
    if (!preset) throw new Error('无法读取 DSH 默认 Agent，请稍后重试。')
    return preset.id
  })
  const stopPanel = ctx.layout.panelInfo.subscribe(saveLocation)
  const stopSelection = ctx.uiWorkspace.selection.subscribe(saveLocation)
  const stopCatalog = ctx.sessions.list.subscribe(() => {
    if (waitingForCatalog && ctx.sessions.list.getSnapshot().phase === 'ready') {
      restore()
      for (const listener of listeners) listener()
    }
  })
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
      notice = ''
      restore()
      saveLocation()
      persist()
      for (const listener of listeners) listener()
    },
    dispose() { stopPreset(); stopPanel(); stopSelection(); stopCatalog(); listeners.clear() },
  }
}
