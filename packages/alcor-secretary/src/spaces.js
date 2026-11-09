export const spaceHook = Symbol.for('@alcor/secretary.workspace-navigation')
export const qqRoute = id => /^onebot-(group|private)-([1-9]\d{0,19})(?:-|$)/.exec(String(id || ''))
export function channelRoute(id,metadata) {
  const route=metadata?.channelRoutes?.find(row=>row.sessionId===id)
  if(route)return route
  const legacy=qqRoute(id)
  return legacy ? {sessionId:id,channel:'qq',account:'legacy',kind:legacy[1],peer:legacy[2],label:metadata?.qq?.[id] || '',channelName:'QQ'} : undefined
}
export const internalSession=row=>row?.projectionValues?.agentPreset==='alcor-delivery' || row?.agentPreset==='alcor-delivery'
const presets = new Set(['alcor', 'alcor-briefing', 'stella', 'stella-briefing', 'qq-group-chat'])
export function sessionSpace(row, fallback = 'unassigned') {
  if (qqRoute(row?.id)) return 'alcor'
  const preset = row?.projectionValues?.agentPreset ?? row?.agentPreset
  return preset ? (presets.has(preset) ? 'alcor' : 'dsh') : fallback
}
export function spaceOf(id, catalog, metadata) {
  return channelRoute(id,metadata) ? 'alcor' : sessionSpace(catalog.byId[id], metadata?.sessions?.[id] ?? 'unassigned')
}
// Undefined denotes a legacy workspace without an explicit project assignment.
export function projectViews(metadata, id, title) {
  if (metadata?.projectViews?.[id]) return metadata.projectViews[id]
  const owner = metadata?.projects?.[id]
  return owner ? { [owner]:title } : undefined
}
export function projectSpace(catalog, workspaces, mode, metadata) {
  const defaults = metadata?.defaults ?? {}
  const ids = metadata ? catalog.ids.filter(id => !channelRoute(id,metadata) && !internalSession(catalog.byId[id]) && spaceOf(id, catalog, metadata) === mode) : []
  const allowed = new Set(ids)
  const items = workspaces.flatMap(w => {
    // Default workspaces are not projects: their sessions remain in the main list.
    if (Object.values(defaults).includes(w.workspaceId) || w.sessionIds.some(id=>channelRoute(id,metadata))) return []
    const sessionIds = w.sessionIds.filter(id => allowed.has(id))
    const views = projectViews(metadata,w.workspaceId,w.title)
    if (views ? !Object.hasOwn(views,mode) : !sessionIds.length) return []
    return [{ ...w, ...(views?.[mode] ? { title:views[mode] } : {}), sessionIds }]
  })
  const scoped={...catalog,ids,byId:Object.fromEntries(ids.map(id=>[id,catalog.byId[id]]))}
  if (Array.isArray(catalog.items)) scoped.items=catalog.items.filter(row=>allowed.has(row.id ?? row.sessionId))
  if (catalog.projectionsBySession) scoped.projectionsBySession=Object.fromEntries(Object.entries(catalog.projectionsBySession).filter(([id])=>allowed.has(id)))
  return {catalog:scoped,items}
}
export function projectedStore(sources, project) {
  let previous, value
  return {
    subscribe(listener) { const stops = sources.map(source => source.subscribe(listener)); return () => stops.forEach(stop => stop()) },
    getSnapshot() {
      const snapshots = sources.map(source => source.getSnapshot())
      if (!previous || snapshots.some((item, i) => item !== previous[i])) { previous = snapshots; value = project(...snapshots) }
      return value
    },
  }
}
