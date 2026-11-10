import { mkdir, lstat } from 'node:fs/promises'
import { join } from 'node:path'
import { projectViews, qqRoute, sessionSpace } from './spaces.js'

// Workspace membership is navigation metadata, never an execution permission.
export class ProductSpaces {
  constructor(ctx, table) { this.ctx = ctx; this.table = table; this.queue = Promise.resolve() }
  async info() {
    if (!this.ready) this.ready = this.initialize().catch(error => { this.ready = undefined; throw error })
    const defaults = await this.ready
    return { defaults, ...structuredClone(this.table.get('ownership') ?? { projects: {}, sessions: {} }) }
  }
  async initialize() {
    const root = join(this.ctx.get('profileContext').dir, 'alcor', 'workspaces')
    await mkdir(root, {recursive:true,mode:0o700})
    if ((await lstat(root)).isSymbolicLink()) throw Error('默认工作区不能使用软链接。')
    const defaults = {}
    for (const space of ['alcor', 'dsh']) {
      const path = join(root, space)
      await mkdir(path, { recursive: true, mode: 0o700 })
      if ((await lstat(path)).isSymbolicLink()) throw Error('默认工作区不能使用软链接。')
      defaults[space] = (await this.ctx.workspaceRegistry.create(path, space === 'alcor' ? 'Alcor 默认工作区' : 'DSH 默认工作区')).id
    }
    return defaults
  }
  mutate(update) {
    const attempt = this.queue.then(async () => {
      const info = await this.info()
      await update(info)
      const { defaults, channelRoutes, ...ownership } = info
      await this.table.put('ownership',ownership)
      return info
    })
    this.queue = attempt.catch(() => {})
    return attempt
  }
  workspace(info,id) {
    if (Object.values(info.defaults).includes(id)) throw Error('默认工作区的归属不能更改。')
    const workspace = this.ctx.workspaceRegistry.list().find(w => w.id === id)
    if (!workspace) throw Error('工作区不存在。')
    if (workspace.sessionIds.some(id=>this.channels?.resolve(id) || qqRoute(id))) throw Error('QQ 工作区由接入插件管理。')
    return workspace
  }
  async sessionOwner(id, metadata, signal) {
    if (this.channels?.resolve(id) || qqRoute(id)) return 'alcor'
    const row = await this.ctx.sessionQuery.observeSession(id, {signal})
    try {
      if (row.header.origin === 'subagent' || row.header.parentSession) return 'subagent'
      return sessionSpace({projectionValues:row.projections?.values},metadata.sessions[id] ?? 'unassigned')
    } finally { row[Symbol.dispose]() }
  }
  claim(request) {
    return this.mutate(async info => {
      const {space,workspaceId,sessionId}=request
      if (workspaceId) {
        const workspace=this.workspace(info,workspaceId)
        if (Object.keys(info.projectViews?.[workspaceId] ?? {}).length > 1) throw Error('这个目录在两个空间中各有一个项目。')
        const title=Object.values(info.projectViews?.[workspaceId] ?? {})[0] ?? workspace.title
        info.projects[workspaceId]=space
        info.projectViews ??= {}
        info.projectViews[workspaceId] = { [space]:title }
      } else {
        if (this.channels?.resolve(sessionId) || qqRoute(sessionId)) throw Error('QQ 会话由接入插件管理。')
        const owner=await this.sessionOwner(sessionId,info)
        if (owner !== 'unassigned' && owner !== space) throw Error('会话已经属于另一个空间，或由宿主管理。')
        info.sessions[sessionId]=space
      }
    })
  }
  editProject({space,workspaceId,action,title}) {
    return this.mutate(async info=>{
      const workspace=this.workspace(info,workspaceId)
      const saved=projectViews(info,workspaceId,workspace.title)
      let views=saved
      if (!views) {
        views={}
        for (const id of workspace.sessionIds) {
          let owner
          try { owner=await this.sessionOwner(id,info) }
          catch (error) { if (error.code === 'SESSION_QUERY_SESSION_NOT_FOUND') continue; throw error }
          if (owner==='alcor' || owner==='dsh') views[owner]=workspace.title
        }
      }
      if (action!=='create' && !Object.hasOwn(views,space)) throw Error('这个项目不属于当前空间。')
      if (action==='remove') delete views[space]
      else if (action==='rename') {
        if (!title) throw Error('请输入项目名称。')
        views[space]=title
      } else if (!saved || !Object.hasOwn(views,space)) views[space]=title ?? workspace.title
      info.projectViews ??= {}
      info.projectViews[workspaceId]=views
      const primary=Object.keys(views)[0]
      if (primary) info.projects[workspaceId]=primary
      else delete info.projects[workspaceId]
    })
  }
  async search(query, ids, space) {
    const ownership = this.table.get('ownership') ?? {sessions:{}}
    const allowed = new Set(ids), seen = new Set(), cursors = new Set(), items = []
    if (!allowed.size) return { items, hasMore: false }
    const signal = AbortSignal.timeout(10000)
    const localMatch = async id => {
      const surface = await this.ctx.sessionQuery.readSurface(id)
      for (const event of [...surface.events].reverse()) {
        let message
        if (event.type === 'assistant/message') message=event.data.message
        else if (event.type === 'user/message' && ['user','user-rpc'].includes(event.data.source?.kind)) message=event.data
        if (!message) continue
        for (const block of message.content ?? []) {
          if (block.type!=='text') continue
          const at=block.text.toLowerCase().indexOf(query.toLowerCase())
          if (at>=0) return {sessionId:id,snippet:block.text.slice(Math.max(0,at-72),at+168)}
        }
      }
    }
    const add = async id => {
      if (seen.has(id) || this.channels?.resolve(id) || qqRoute(id) || !allowed.has(id)) return
      seen.add(id)
      signal.throwIfAborted()
      try {
        if (await this.sessionOwner(id,ownership,signal)!==space) return
        const match=await localMatch(id)
        if (match) items.push(match)
      } catch (error) { if (error.code !== 'SESSION_QUERY_SESSION_NOT_FOUND') throw error }
    }
    let cursor
    // Filter before the result limit; other-space hits cannot displace this space's results.
    for (let pageNumber = 0; pageNumber < 128; pageNumber++) {
      let page
      try { page = await this.ctx.sessionQuery.searchSessions({ query, limit: 20, ...(cursor ? { cursor } : {}),
        eventFilters: [{ kind: 'type', values: ['user/message', 'assistant/message'] }, { kind: 'surface', values: ['current'] }],
      }, { signal }) } catch (error) {
        if (error.code !== 'SESSION_QUERY_SEARCH_DISABLED') throw error
        // Desktop can disable its global index. Read only visible sessions, without creating an index.
        for (const id of allowed) {
          await add(id)
          if (items.length===21) break
        }
        return {items:items.slice(0,20),hasMore:items.length>20}
      }
      for (const hit of page.items) {
        const id = hit.header.id, match = hit.bestMatch
        if (!allowed.has(id) || this.channels?.resolve(id) || qqRoute(id) || seen.has(id) || match.sessionId !== id || match.surface !== 'current'
          || !['user/message', 'assistant/message'].includes(match.type)) continue
        await add(id)
        if (items.length === 21) return { items: items.slice(0,20), hasMore: true }
      }
      if (!page.nextCursor) return { items, hasMore: false }
      if (cursors.has(page.nextCursor)) throw Error('会话搜索游标重复，请重试。')
      cursors.add(page.nextCursor); cursor = page.nextCursor
    }
    throw Error('匹配过多，请缩小搜索范围。')
  }
}
