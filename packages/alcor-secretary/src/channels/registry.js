import { z } from 'zod'
export const channelRouteSchema=z.object({
  sessionId:z.string().min(1).max(200),channel:z.string().min(1).max(80),account:z.string().min(1).max(100),
  kind:z.enum(['private','group']),peer:z.string().min(1).max(100),label:z.string().max(128),
  audience:z.string().min(1).max(250),owner:z.boolean(),channelName:z.string().max(128).optional(),
}).strict()
export const channelMessageSchema=z.object({seq:z.number(),role:z.string(),text:z.string(),time:z.number().optional(),nickname:z.string().optional(),ambient:z.boolean().optional()}).strict()
export const cachedMessageSchema=z.object({sessionId:z.string(),message:channelMessageSchema}).strict()
const keyOf=route=>JSON.stringify([route.channel,route.account,route.kind,route.peer])
export class ChannelRegistry {
  constructor(table,sessionIds,cache,readSurface) {this.table=table;this.sessionIds=sessionIds;this.cache=cache;this.readSurface=readSurface;this.adapters=new Map();this.names=new Map();this.queue=Promise.resolve()}
  register(adapter) {
    if (!/^[a-z0-9][a-z0-9._-]{0,79}$/.test(adapter.id) ||
        !/^[A-Za-z0-9._-]{1,100}$/.test(adapter.account) ||
        typeof adapter.label!=='string' || !adapter.label || adapter.label.length>128 ||
        typeof adapter.resolve!=='function' || typeof adapter.messages!=='function') {
      throw Error('通道适配器缺少有效的账号、名称或会话接口。')
    }
    if(this.adapters.has(adapter.id+'\0'+adapter.account))throw Error('通道账号已经注册。')
    const key=adapter.id+'\0'+adapter.account
    this.adapters.set(key,adapter)
    if(this.readSurface)void this.backfill(adapter).catch(()=>{})
    return ()=>{if(this.adapters.get(key)===adapter)this.adapters.delete(key)}
  }
  adapter(route) {return this.adapters.get(route.channel+'\0'+route.account)}
  resolve(sessionId) {
    const stored=this.table.get(sessionId)
    const candidates=[...this.adapters.values()].filter(a=>!stored || (a.id===stored.channel && a.account===stored.account))
    const matches=candidates.map(adapter=>{
      const route=adapter.resolve(sessionId)
      if(!route)return
      const parsed=channelRouteSchema.parse({...route,label:this.names.get(sessionId)?.value || stored?.label || route.label,sessionId,channel:adapter.id,account:adapter.account,channelName:adapter.label})
      if(parsed.kind==='group' && parsed.audience==='personal')throw Error('群聊不能使用私人记忆范围。')
      if(parsed.kind==='private' && !parsed.owner && parsed.audience==='personal')throw Error('未绑定主人身份。')
      if(adapter.id!=='qq' && parsed.audience!=='personal' && !parsed.audience.startsWith('channel:'+adapter.id+':'+adapter.account+':'))throw Error('通道记忆范围缺少账号边界。')
      return parsed
    }).filter(Boolean)
    if(matches.length>1)throw Error('通道会话的账号归属不明确。')
    const current=matches[0]
    if(stored && current && keyOf(stored)!==keyOf(current))throw Error('通道会话的账号归属发生变化。')
    return current ?? stored
  }
  async backfill(adapter) {
    await this.catalog()
    for(const id of this.sessionIds()){
      const route=this.resolve(id)
      if(!route || this.adapter(route)!==adapter)continue
      try{const surface=await this.readSurface(id);await this.messages(id,surface.events)}catch{}
    }
  }
  async catalog(refreshNames=false) {
    const attempt=this.queue.then(async()=>{
      if(refreshNames)await Promise.all(this.sessionIds().map(async id=>{
        const route=this.resolve(id), adapter=route && this.adapter(route)
        const cached=this.names.get(id)
        if(!adapter?.info || (cached && Date.now()-cached.at<60000))return
        try{
          const info=await adapter.info(route)
          if(info?.name?.trim())this.names.set(id,{value:info.name.trim().slice(0,128),at:Date.now()})
        }catch{}
      }))
      for(const id of this.sessionIds()) {
        const route=this.resolve(id)
        if(route && JSON.stringify(route)!==JSON.stringify(this.table.get(id)))await this.table.put(id,route)
      }
      return [...this.table.entries()].map(([,route])=>({...route,label:route.label || route.peer,
        available:!!this.adapter(route),channelName:this.adapter(route)?.label ?? route.channelName ?? route.channel}))
    })
    this.queue=attempt.catch(()=>{})
    return attempt
  }
  actor(session,source) {
    const route=this.resolve(session.id), adapter=route && this.adapter(route)
    if (!adapter || source?.kind!==adapter.sourceKind)return undefined
    const actor=adapter.actor?.(route,source)
    if(actor && actor.audience!==route.audience)throw Error('入站身份与会话听众不一致。')
    if(actor)actor.source={...actor.source,channelId:route.channel,accountId:route.account,sessionId:session.id}
    return actor
  }
  permitted(route) {
    const current=this.resolve(route.sessionId), adapter=current && this.adapter(current)
    return current && keyOf(current)===keyOf(route) && current.audience===route.audience && current.owner===route.owner && typeof adapter?.send==='function' && adapter.canSend?.(current) ? current : null
  }
  async info(sessionId) {
    if(!this.table.get(sessionId) && !this.sessionIds().includes(sessionId))return null
    const route=this.resolve(sessionId)
    if (!route)return null
    const info=await this.adapter(route)?.info(route) ?? null
    if(info?.name?.trim()){
      this.names.set(sessionId,{value:info.name.trim().slice(0,128),at:Date.now()})
      await this.catalog()
    }
    return info
  }
  async messages(sessionId,records) {
    const route=this.resolve(sessionId)
    if(!route)return []
    const adapter=this.adapter(route)
    if(adapter){
      const messages=adapter.messages(route,records).map(message=>channelMessageSchema.parse(message))
      if(this.cache)for(const message of messages)await this.cache.put(JSON.stringify([sessionId,message.seq]),{sessionId,message})
      return messages
    }
    const allowed=new Set(records.map(r=>r.seq))
    const cached=this.cache ? [...this.cache.entries()].map(([,r])=>r).filter(r=>r.sessionId===sessionId && allowed.has(r.message.seq)).map(r=>r.message) : []
    if(records.length && !cached.length)throw Error('通道插件已停用，旧记录尚未缓存。重新启用插件后可恢复查看。')
    return cached
  }
}
