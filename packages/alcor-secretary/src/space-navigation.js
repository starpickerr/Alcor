import { projectViews, projectSpace, projectedStore, qqRoute, channelRoute, spaceOf, spaceHook } from './spaces.js'

export function createSpaceNavigation(ctx, controller) {
  const listeners = new Set()
  let metadata, error = '', selection
  const snapshot = () => ({ metadata, error, selection })
  let current = snapshot()
  function notify() { current = snapshot(); for (const listener of listeners) listener() }
  const state = { getSnapshot: () => current, subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) } }
  function checked(result) { if (!result.ok) throw Error(result.error.message); return result.value }
  let ready
  function reconcile() {
    const catalog = ctx.sessions.list.getSnapshot(), id = ctx.uiWorkspace.selection?.getSnapshot().sessionId
    if (!metadata || catalog.phase !== 'ready' || !id || ctx.layout.panelInfo?.getSnapshot().activePanelId !== null) return
    const owner = spaceOf(id, catalog, metadata), mode = controller.getSnapshot()
    if (['alcor', 'dsh'].includes(owner) && owner !== mode) ctx.layout.selectPanel(mode === 'alcor' ? 'alcor-tasks' : 'dsh')
  }
  async function refresh() {
    try { metadata = checked(await ctx.remote.alcorSecretary.spaces({})); error = ''; notify(); reconcile(); return metadata }
    catch (reason) { error = reason.message; notify(); throw reason }
  }
  async function ensure() { if (!metadata) await (ready ??= refresh().catch(reason => { ready = undefined; throw reason })); return metadata }
  const sources = [ctx.sessions.list, ctx.workspaces.list, controller, state]
  const projection = projectedStore(sources, (catalog, workspaces, mode, product) => projectSpace(catalog, workspaces.items, mode, product.metadata))
  const sessions = projectedStore([projection], value => value.catalog)
  const workspaces = projectedStore([ctx.workspaces.list, projection], (all, value) => ({ ...all, items: value.items }))
  const picker = projectedStore([ctx.workspaces.list, projection, state, controller], (all, value, product, mode) => ({ ...all,
    items: [...all.items.filter(item => item.workspaceId === product.metadata?.defaults[mode]), ...value.items],
  }))
  async function claim(request) { metadata = checked(await ctx.remote.alcorSecretary.claimSpace(request)); notify() }
  async function editProject(space,workspaceId,action,title) {
    metadata = checked(await ctx.remote.alcorSecretary.editProject({space,workspaceId,action,...(title ? {title} : {})}))
    notify()
  }
  async function nativePreset() {
    const list = checked(await ctx.remote.agentPresets.list()).presets
    const eligible = list.filter(item => !['qq-dsh-chat','alcor-delivery','alcor', 'stella', 'qq-group-chat', 'alcor-briefing', 'stella-briefing'].includes(item.id))
    const preset = eligible.find(item => item.isDefault) ?? eligible.find(item => item.id === 'standard') ?? eligible.find(item => item.id === 'default')
    if (!preset) throw Error('DSH 默认 Agent 暂不可用。')
    return preset.id
  }
  const api = {
    mode:controller, state, sessions, workspaces, picker, ensure, nativePreset, claim,
    async createWorkspace(input) {
      const mode = controller.getSnapshot()
      const created = await ctx.workspaces.create(input)
      await editProject(mode,created.workspaceId,'create',input.title)
      return {...created,title:metadata.projectViews[created.workspaceId][mode]}
    },
    renameWorkspace(id,title) { return editProject(controller.getSnapshot(),id,'rename',title) },
    deleteWorkspace(id) { return editProject(controller.getSnapshot(),id,'remove') },
    async startSession(workspaceId, options = {}) {
      const mode = controller.getSnapshot(), info = await ensure()
      const target = workspaceId ?? info.defaults[mode]
      const workspace = ctx.workspaces.list.getSnapshot().items.find(item => item.workspaceId === target)
      if (!workspace || workspace.sessionIds.some(id=>channelRoute(id,info))) throw Error('此工作区不可创建桌面对话。')
      const defaultSpace = Object.entries(info.defaults).find(([,id]) => id === target)?.[0]
      const views = projectViews(info,target,workspace.title)
      if ((defaultSpace && defaultSpace !== mode) || (views && !Object.hasOwn(views,mode))) throw Error('该项目属于另一个空间。')
      if (workspaceId && !defaultSpace && !views) await editProject(mode,target,'create')
      const agentPreset = mode === 'alcor' ? 'alcor' : await nativePreset()
      const id = await ctx.sessions.create({ workspaceId: target, agentPreset })
      await claim({ sessionId: id, space: mode })
      if (mode === controller.getSnapshot()) ctx.uiWorkspace.openSession(id)
      if (options.prompt) {
        const binding = ctx.sessions.binding(id)
        if (!binding) throw Error('对话已创建，输入框暂不可用。')
        ctx.uiWorkspace.prepareDraft(binding, { prompt: options.prompt, whenDraftExists: 'replace' })
      }
      return id
    },
    isChannelSession(target) { return !!channelRoute(target,metadata) },
    openQQ(target) {
      if (typeof target !== 'string' || !channelRoute(target,metadata)) return false
      controller.switchMode('alcor')
      selection = target
      notify()
      ctx.layout.selectPanel('alcor-qq-history')
      return true
    },
    selectSpace(target) {
      if (api.routing || typeof target !== 'string' || !metadata) return
      const owner = spaceOf(target, ctx.sessions.list.getSnapshot(), metadata)
      if (!['alcor', 'dsh'].includes(owner) || owner === controller.getSnapshot()) return
      api.routing = true
      try { controller.switchMode(owner) } finally { api.routing = false }
    },
    async search(query, signal) {
      await ensure()
      signal?.throwIfAborted()
      const mode = controller.getSnapshot(), sessionIds = sessions.getSnapshot().ids
      const result = checked(await ctx.remote.alcorSecretary.searchSpace({ space:mode, query, sessionIds }))
      signal?.throwIfAborted()
      return mode === controller.getSnapshot() ? result : { items: [], hasMore: false }
    },
    dispose() { stopCatalog(); if (globalThis[spaceHook] === api) { delete globalThis[spaceHook]; globalThis.dispatchEvent?.(new Event('alcor-navigation-ready')) }; listeners.clear() },
  }
  let refreshing=false,refreshAgain=false,active=true
  function catalogChanged(){
    reconcile()
    if(!active)return
    if(refreshing){refreshAgain=true;return}
    refreshing=true
    void refresh().catch(()=>{}).finally(()=>{
      refreshing=false
      if(refreshAgain && active){refreshAgain=false;catalogChanged()}
    })
  }
  const stopScopes=ctx.remote?.$on?.('plugin-manager/changed',catalogChanged)
  const stops=[ctx.sessions.list.subscribe(catalogChanged),ctx.workspaces.list.subscribe(catalogChanged)]
  globalThis.addEventListener?.('alcor-channel-catalog-changed',catalogChanged)
  const stopCatalog=()=>{active=false;stopScopes?.();for(const stop of stops)stop();globalThis.removeEventListener?.('alcor-channel-catalog-changed',catalogChanged)}
  globalThis[spaceHook] = api
  globalThis.dispatchEvent?.(new Event('alcor-mode-changed'))
  globalThis.dispatchEvent?.(new Event('alcor-navigation-ready'))
  void ensure().catch(() => {})
  return api
}
