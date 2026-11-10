import { existsSync, readdirSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { join } from 'node:path'
import { historyMessages } from '../qq-history.js'
import { publicSoul } from '../persona.js'
import { createScopedMemory } from './scoped.js'
import { qqRoute } from '../spaces.js'

const number = value => typeof value === 'string' && /^[1-9]\d{0,19}$/.test(value)
export class AlcorMemory {
  constructor(ctx, productSpaces, initialName = 'Alcor') {
    this.ctx = ctx; this.spaces = productSpaces; this.groups = new Map(); this.turns = new WeakMap(); this.desktopInputs = new Set()
    this.root = join(ctx.get('profileContext').dir, 'alcor', 'memory')
    const options = { getPersona:()=>this.publicPersona(), llm: ctx.get('llm'), getDefaultModel: () => ctx.get('agentDefaultModel')?.currentSelection(), logger: ctx.logger }
    this.options = options
    this.personal = createScopedMemory({ ...options, dataDir: join(this.root, 'personal'), audience: 'personal', initialName })
    ctx.on('agent/pre-step', ({ agent, messages, modelSelection }, next) => {
      const route = this.spaces.channels?.resolve(agent.session.id) || qqRoute(agent.session.id)
      if (route && messages.some(message => ['user', 'user-rpc'].includes(message.source.kind))) {
        this.desktopInputs.add(agent.session.id)
        return Promise.resolve({ kind: 'reject' })
      }
      if (route && messages.length) this.desktopInputs.delete(agent.session.id)
      if (messages.length) {
        const actors = messages.map(message => this.actor(agent.session, message.source))
        const actor = actors[0]
        const boundary = ctx.agents.turnBoundaryOf(agent.session)
        const previous = this.turns.get(agent)
        const sameTurn = previous && previous.turn === boundary?.lastTurn && previous.start === boundary?.openTurnStartSeq
        const valid = actor && actors.every(other => other?.subject === actor.subject && other?.audience === actor.audience)
          && (!sameTurn || previous.actor?.subject === actor.subject)
        const speech=message=>message.source.kind==='plugin:dsh-onebot'
          ? historyMessages([{type:'user/message',data:message}])[0]?.text ?? ''
          : (message.content ?? []).filter(block=>block.type==='text').map(block=>block.text).join(' ')
        const query=valid ? messages.map(speech).join(' ').slice(-1500) : ''
        if(valid && !sameTurn && query && boundary?.openTurnStartSeq!=null && (actor.audience==='personal' || route?.kind==='group' || route?.[1]==='group')){
          const selection=modelSelection?.provider && modelSelection?.model ? {provider:modelSelection.provider,model:modelSelection.model} : undefined
          void this.target(actor).observe(query,actor,agent.session.id+':'+boundary.openTurnStartSeq,selection,()=>this.actor(agent.session,messages[0].source)?.audience===actor.audience)
        }
        if (boundary?.openTurnStartSeq != null) this.turns.set(agent, { actor: valid ? actor : undefined, query, turn: boundary.lastTurn, start: boundary.openTurnStartSeq, model:modelSelection?.provider && modelSelection?.model ? {provider:modelSelection.provider,model:modelSelection.model} : undefined, rawSource:messages[0].source })
        else this.turns.delete(agent)
      }
      return next()
    }, { prepend: true })
    ctx.on('session/event', (session, event) => {
      const registered=this.spaces.channels?.resolve(session.id)
      const route = qqRoute(session.id)
      const memory = registered ? (registered.audience==='personal'?this.personal:this.groups.get(registered.audience) ?? this.groups.get(registered.peer)) : route?.[1] === 'group' ? this.groups.get(route[2]) : this.isPersonal(session) ? this.personal : undefined
      if (!memory && registered?.kind==='group' && this.actor(session,event.data?.source))this.storeForAudience(registered.audience,registered)
      const selected=memory || (registered && this.groups.get(registered.audience))
      if (!selected) return
      // Invalid inputs are not captured or used to update a person's identity.
      if (event.type === 'user/message') {
        const actor=this.actor(session,event.data.source)
        if(!actor)return
        const speech=event.data.source.kind==='plugin:dsh-onebot' ? historyMessages([event])[0]?.text :
          event.data.content?.filter?.(block=>block.type==='text').map(block=>block.text).join(' ')
        if(!selected.hasInbound(event.data.source.qqMessageId))selected.noteIncoming(actor,speech)
      }
      selected.handleSessionEvent(session, event)
    })
    ctx.on('session/flush', session => {
      const registered=this.spaces.channels?.resolve(session.id)
      const route = qqRoute(session.id)
      const memory = registered ? (registered.audience==='personal'?this.personal:this.groups.get(registered.audience) ?? this.groups.get(registered.peer)) : route?.[1] === 'group' ? this.groups.get(route[2]) : this.isPersonal(session) ? this.personal : undefined
      memory?.flushSession(session.id)
    })
    ctx.effect(() => () => Promise.all([this.personal, ...this.groups.values()].map(memory => memory.dispose())))
  }
  publicPersona() {
    const identity = this.personal.identity()
    return '名字：' + identity.name + '\n' + publicSoul(identity.soul)
  }
  importIdentity(document, expectedRevision) {
    const directory = join(this.root, 'groups')
    return this.personal.importIdentity(document, expectedRevision, () => !existsSync(directory) || !readdirSync(directory).length)
  }
  experienceScopes() {
    return ['personal', ...(this.policy()?.groupAllowFrom ?? []).map(id => 'group:' + id)]
  }
  experienceStore(audience) {
    if (audience === 'personal') return this.personal
    if (!this.experienceScopes().includes(audience)) throw Error('请先在本机重新绑定并启用这个 QQ 群。')
    const id = audience.slice(6)
    this.group(id)
    return this.groups.get(id)
  }
  exportExperiences(audience) {
    const memory = this.experienceStore(audience)
    return memory.exportExperiences(() => {
      if (audience !== 'personal' && (!this.experienceScopes().includes(audience) || !this.policy()?.adminUsers?.some(id => memory.hasParticipant(id)))) throw Error('只能导出主人参与过且仍启用的群。')
      return this.personal.identity().id
    })
  }
  desktopBlocked(sessionId) { return this.desktopInputs.has(sessionId) }
  policy(activeOnly = true) {
    const entry = this.ctx.get('configEditor')?.entries().find(entry => entry.options.id === 'dsh-onebot')
    return entry && (!activeOnly || entry.fiber?.state === 2) ? entry.options.config : undefined
  }
  isPersonal(session) {
    if (session.header?.origin === 'subagent' || session.header?.parentSession) return false
    const registered=this.spaces.channels?.resolve(session.id)
    if(registered)return registered.audience==='personal' && registered.owner
    const route = qqRoute(session.id)
    if (route) return route[1] === 'private' && this.policy()?.adminUsers?.includes(route[2])
    return ['alcor','stella'].includes(this.ctx.get('agentPresets')?.presetForSession(session))
  }
  actor(session, source) {
    if (!source || session.header?.origin === 'subagent' || session.header?.parentSession) return
    const registered=this.spaces.channels?.resolve(session.id)
    if(registered)return this.spaces.channels.actor(session,source)
    const route = qqRoute(session.id), policy = this.policy()
    if (route) {
      if (source.kind !== 'plugin:dsh-onebot' || !number(source.qqUserId)) return
      const owner = policy?.adminUsers?.includes(source.qqUserId) === true
      if (route[1] === 'group') {
        if (source.qqGroupId !== route[2] || !policy?.groupAllowFrom?.includes(route[2])) return
        return { subject: 'qq:' + source.qqUserId, audience: 'group:' + route[2], owner,
          source: { kind: 'qq-group', sessionId: session.id, qqUserId: source.qqUserId, qqGroupId: route[2], at: new Date().toISOString() } }
      }
      if (!owner || source.qqUserId !== route[2]) return
      return { subject: 'owner', audience: 'personal', owner: true, source: { kind: 'qq-private', sessionId: session.id, qqUserId: source.qqUserId, at: new Date().toISOString() } }
    }
    if (this.isPersonal(session) && ['user', 'user-rpc'].includes(source.kind)) return { subject: 'owner', audience: 'personal', owner: true,
      source: { kind: 'desktop', sessionId: session.id, at: new Date().toISOString() } }
  }
  currentActor(agent) {
    const saved = this.turns.get(agent), boundary = agent?.session && this.ctx.agents.turnBoundaryOf(agent.session)
    if (!saved?.actor || saved.turn !== boundary?.lastTurn || saved.start !== boundary?.openTurnStartSeq) return
    // Re-check revocation at execution time.
    const actor = this.actor(agent.session,saved.rawSource)
    return actor
  }
  personalContext(agent) {
    return this.currentActor(agent)?.audience === 'personal' ? this.personal.turnContext(this.turns.get(agent).query,this.currentActor(agent)) : ''
  }
  trustedActor(agent) {
    const actor = this.currentActor(agent)
    if (!actor) throw Error('当前回合没有有效的记忆访问身份。')
    return actor
  }
  target(actor) { return this.storeForAudience(actor.audience,this.spaces.channels?.resolve(actor.source.sessionId)) }
  async use(operation, args, agent) {
    if (typeof args !== 'object' || args === null || Array.isArray(args)) throw Error('无效的记忆参数。')
    if (operation === 'recall' && (typeof args.query !== 'string' || args.query.length > 1000)) throw Error('检索词最多 1000 字。')
    const actor = this.trustedActor(agent), memory = this.target(actor)
    if (!memory) throw Error('记忆暂不可用。')
    if(operation==='followup') {
      if(!this.personal.initiative)throw Error('请先由主人开启主动跟进。')
      if(actor.audience!=='personal' && !memory.initiative)await memory.allowInitiative(true)
      const note=await memory.planFollowup({...args,model:this.turns.get(agent)?.model,generation:this.personal.initiativeGeneration})
      await this.delivery?.track(actor.audience,note)
      const {id,evidence,content,due}=note
      return {id,evidence,content,due}
    }
    if (operation === 'reflect' && actor.audience === 'personal') return memory.reflect(args, actor)
    if (operation === 'remember') return memory.remember(args, actor)
    if (operation === 'correct' || operation === 'forget') return memory.change({ id: args.id, ...(operation === 'correct' ? { content: args.content } : {}) }, actor)
    if (operation === 'recall') {
      const results = memory.recall(args.query)
      if (actor.audience === 'personal' && args.query) {
        const policy = this.policy()
        const owners = actor.source.kind === 'qq-private' ? [actor.source.qqUserId] : policy?.adminUsers ?? []
        for (const id of policy?.groupAllowFrom ?? []) {
          this.group(id)
          const group = this.groups.get(id)
          if (owners.some(owner => group.hasParticipant(owner))) results.push(...group.recall(args.query, 2))
        }
      }
      return results.slice(0, 10)
    }
    throw Error('未知记忆操作。')
  }
  storeForAudience(audience, knownRoute) {
    if(audience==='personal')return this.personal
    if(audience.startsWith('group:')){const id=audience.slice(6);this.group(id);return this.groups.get(id)}
    const route=knownRoute?.audience===audience && knownRoute.kind==='group' ? knownRoute : [...this.spaces.channels?.table.entries() ?? []].map(([,r])=>r).find(r=>r.audience===audience && r.kind==='group')
    if(!route || !this.spaces.channels.adapter(route))throw Error('此通道的记忆范围尚不可用。')
    let store=this.groups.get(audience)
    if(!store){store=createScopedMemory({...this.options,dataDir:join(this.root,'channels',createHash('sha256').update(audience).digest('hex')),audience});this.groups.set(audience,store)}
    return store
  }
  canManageAudience(audience) {
    if(audience==='personal')return true
    try {
      if(audience.startsWith('group:')){const store=this.storeForAudience(audience);return this.policy()?.adminUsers?.some(id=>store.hasParticipant(id))===true}
      const route=[...this.spaces.channels?.table.entries() ?? []].map(([,r])=>r).find(r=>r.audience===audience)
      return route && this.spaces.channels.adapter(route)?.isOwnerMember?.(route)===true
    }catch{return false}
  }
  group(groupId) {
    if (!number(groupId)) throw Error('无效的 QQ 群。')
    const policy = this.policy(false)
    if (!policy?.groupAllowFrom?.includes(groupId)) throw Error('此群未启用。')
    let memory = this.groups.get(groupId)
    if (!memory) {
      memory = createScopedMemory({ ...this.options, dataDir: join(this.root, 'groups', groupId), audience: 'group:' + groupId })
      this.groups.set(groupId, memory)
    }
    return { contextText: agent => {
      if (!this.policy()?.groupAllowFrom?.includes(groupId)) return ''
      if (!agent) return memory.contextText()
      return this.currentActor(agent)?.audience === 'group:' + groupId ? memory.turnContext(this.turns.get(agent).query,this.currentActor(agent)) : ''
    }, soul: () => this.publicPersona(),
      remember: (args, agent) => this.use('remember', args, agent),
      followup: (args, agent) => this.use('followup', args, agent),
      recall: (query, agent) => this.use('recall', { query }, agent),
      change: (args, agent) => this.use(args.content === undefined ? 'forget' : 'correct', args, agent),
      handleSessionEvent() {}, flushSession() {}, dispose() {}, status: () => memory.status() }
  }
}
