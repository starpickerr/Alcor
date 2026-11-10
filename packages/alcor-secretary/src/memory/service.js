import {qqConversationStyle} from '../qq-style.js'
import { existsSync, readdirSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { join } from 'node:path'
import { historyMessages } from '../qq-history.js'
import { truncateByBytes } from './engine/search.js'
import { describePersona } from '../persona.js'
import { createScopedMemory } from './scoped.js'
import { qqRoute } from '../spaces.js'

const number = value => typeof value === 'string' && /^[1-9]\d{0,19}$/.test(value)
export class AlcorMemory {
  constructor(ctx, productSpaces, initialName = 'Alcor') {
    this.ctx = ctx; this.spaces = productSpaces; this.groups = new Map(); this.turns = new WeakMap(); this.desktopInputs = new Set()
    this.root = join(ctx.get('profileContext').dir, 'alcor', 'memory')
    const options = { getInitiativeGeneration: () => this.personal?.initiative ? this.personal.initiativeGeneration : undefined, getPersona:()=>this.publicPersona(), llm: ctx.get('llm'), getDefaultModel: () => ctx.get('agentDefaultModel')?.currentSelection(), logger: ctx.logger }
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
        if(valid && !sameTurn && query && boundary?.openTurnStartSeq!=null){
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
      let selected
      if (event.type === 'user/message') {
        const actor = this.actor(session, event.data.source)
        if (!actor) return
        selected = this.target(actor)
        const qq = event.data.source.kind === 'plugin:dsh-onebot'
        const speech = qq ? historyMessages([event])[0]?.text :
          event.data.content?.filter?.(block => block.type === 'text').map(block => block.text).join(' ')
        if (!selected.hasInbound(event.data.source.qqMessageId)) selected.noteIncoming(actor, speech)
        if (qq) {
          // Store the speaker's words, not the transcript injected into the agent prompt.
          const user = speech ? '[' + actor.subject + '] ' + speech : ''
          event = { ...event, data: { ...event.data, source:{ ...event.data.source, memorySubject:actor.subject }, content: [{ type: 'text', text: user }] } }
        }
      } else {
        selected = this.sessionStore(session, registered, route)
      }
      selected?.handleSessionEvent(session, event)
    })
    ctx.on('session/flush', session => this.sessionStore(session)?.flushSession(session.id))
    ctx.effect(() => () => Promise.all([this.personal, ...this.groups.values()].map(memory => memory.dispose())))
  }
  noteQQReply(sessionId, text) {
    if (!sessionId || !text) return
    const channels = this.spaces.channels
    const route = channels?.resolve(sessionId)
    if (route?.channel !== 'qq' || !channels.adapter(route)?.trustedForDelivery?.(route)) return
    this.storeForAudience(route.audience,route).recordActivity(sessionId,'Alcor',text,Date.now())
  }
  sessionStore(session, registered = this.spaces.channels?.resolve(session.id), route = qqRoute(session.id)) {
    if (registered) return registered.audience === 'personal' ? this.personal :
      this.groups.get(registered.audience) ?? (registered.kind === 'group' ? this.groups.get(registered.peer) : undefined)
    return route?.[1] === 'group' ? this.groups.get(route[2]) : this.isPersonal(session) ? this.personal : undefined
  }
  publicPersona() {
    return describePersona(this.personal.identity(), this.personal.exampleStyle?.())
  }
  importIdentity(document, expectedRevision) {
    const unused = () => ['groups', 'channels'].every(name => {
      const directory = join(this.root, name)
      return !existsSync(directory) || !readdirSync(directory).length
    })
    return this.personal.importIdentity(document, expectedRevision, unused)
  }
  experienceScopes() {
    const policy = this.policy()
    const groups = policy?.groupPolicy === 'disabled' ? [] : policy?.groupAllowFrom ?? []
    const registry = this.spaces.channels
    const contacts = [...(registry?.table.entries() ?? [])].flatMap(([id]) => {
      const route = registry.resolve(id)
      return route?.channel === 'qq' && route.kind === 'private' && !route.owner &&
        registry.adapter(route)?.trustedForDelivery?.(route) ? [route.audience] : []
    })
    return [...new Set(['personal', ...groups.filter(number).map(id => 'group:' + id), ...contacts])]
  }
  experienceStore(audience) {
    if (!this.experienceScopes().includes(audience)) throw Error('请先重新启用对应的 QQ 联系人或群聊。')
    return this.storeForAudience(audience)
  }
  exportExperiences(audience) {
    return this.experienceStore(audience).exportExperiences(() => {
      this.experienceStore(audience)
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
    return actor?.subject === saved.actor.subject && actor?.audience === saved.actor.audience ? actor : undefined
  }
  trustedGroups(actor) {
    const policy = this.policy()
    if (!actor.owner || actor.audience !== 'personal' || !policy || policy.groupPolicy === 'disabled') return []
    return [...new Set(policy.groupAllowFrom ?? [])].filter(number).map(id => {
      this.group(id)
      return this.groups.get(id)
    })
  }
  personalContext(agent) {
    const actor = this.currentActor(agent)
    if (!actor || actor.source.kind === 'qq-group') return ''
    const query = this.turns.get(agent).query
    const context = [actor.source.kind === 'qq-private' && qqConversationStyle, this.target(actor).turnContext(query, actor)].filter(Boolean).join('\n')
    if (!query || actor.audience !== 'personal') return context
    const matches = this.trustedGroups(actor).flatMap(group => group.relevantMatches(query))
      .sort((a, b) => b.score - a.score).slice(0, 4)
    const groups = matches.map(row => '[' + row.audience + ' · ' + row.subject + '] ' + row.content)
    return [context, groups.length && truncateByBytes('来自已接入 QQ 群的相关发言，仅供本轮参考：\n' + groups.join('\n'), 3200)].filter(Boolean).join('\n')
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
      if (args.query) {
        for (const group of this.trustedGroups(actor)) results.push(...group.recall(args.query))
      }
      return results.sort((a, b) => b.score - a.score).slice(0, 16)
    }
    throw Error('未知记忆操作。')
  }
  storeForAudience(audience, knownRoute) {
    if(audience==='personal')return this.personal
    if(audience.startsWith('group:')){const id=audience.slice(6);this.group(id);return this.groups.get(id)}
    const route = knownRoute?.audience === audience ? knownRoute : [...this.spaces.channels?.table.entries() ?? []].map(([,r]) => r).find(r => r.audience === audience)
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
  async groupHistory(groupId, { before, query = '' }, agent) {
    const allowed = () => {
      const actor = this.trustedActor(agent)
      if (actor.audience !== 'group:' + groupId || actor.source.kind !== 'qq-group') throw Error('只能查看当前 QQ 群的历史。')
    }
    allowed()
    if (before != null && !Number.isSafeInteger(before)) throw Error('无效的历史游标。')
    if (typeof query !== 'string' || query.length > 200) throw Error('历史检索词最多 200 字。')
    const page = await this.spaces.channels.platformHistory(agent.session.id, before)
    allowed()
    if (!page) throw Error('群历史暂不可用。')
    const needle = query.trim().toLowerCase()
    const messages = []
    let remaining = 16000
    let nextBefore = page.hasMore ? page.beforeSeq : null
    for (const row of [...page.messages].reverse()) {
      if (needle && ![row.role, row.nickname, row.text].some(value => String(value ?? '').toLowerCase().includes(needle))) continue
      const snippet = truncateByBytes(row.text, 1800)
      if (Buffer.byteLength(snippet) > remaining) {
        nextBefore = messages[0].cursor
        break
      }
      remaining -= Buffer.byteLength(snippet)
      messages.unshift({ cursor: row.cursor ?? row.seq, time: row.time, speaker: row.role, nickname: row.nickname, text: snippet })
    }
    return { messages, nextBefore }
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
    }, participationContext: (query, speakerId) => {
      const policy = this.policy()
      if (!policy?.groupAllowFrom?.includes(groupId) || policy.groupPolicy === 'disabled') return ''
      const actor = number(speakerId) ? { subject:'qq:' + speakerId, audience:'group:' + groupId } : undefined
      return memory.turnContext(query, actor)
    }, soul: () => this.publicPersona(),
      history: (args, agent) => this.groupHistory(groupId, args, agent),
      remember: (args, agent) => this.use('remember', args, agent),
      followup: (args, agent) => this.use('followup', args, agent),
      recall: (query, agent) => this.use('recall', { query }, agent),
      change: (args, agent) => this.use(args.content === undefined ? 'forget' : 'correct', args, agent),
      handleSessionEvent() {}, flushSession() {}, dispose() {}, status: () => memory.status() }
  }
}
