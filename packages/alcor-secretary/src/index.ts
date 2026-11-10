import {permits,selectedScope} from '../../alcor-plugin-info/src/assignments.js'
import { checkConnection } from './elearning.js'
import { Service } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { z as schema } from 'zod'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { defineDomain, domainTable } from '@deepseek-ai/dsh-storage-domain'
import { remote, overviewSchema, accountSchemas, ownershipSchema } from './remote.js'
import { qqRoute } from './spaces.js'
import { ChannelRegistry, channelRouteSchema, cachedMessageSchema } from './channels/registry.js'
import { ChannelDelivery } from './channels/delivery.js'
import { deliveryStateSchema } from './channels/schema.js'
import { ProductSpaces } from './space-host.js'
import { AlcorMemory } from './memory/service.js'
import { initializeProfile } from '../../alcor-organizer/src/profile-migration.js'

const domainSpec = defineDomain({
  name: 'alcor_secretary', version: 1,
  tables: { installation: domainTable(schema.object({ installedAt: schema.string().datetime() }).strict()) },
})

function assertCompatibleProfile(ctx) {
  const selected = ctx.get('profileContext')?.startedBundles
  if (!Array.isArray(selected)) throw new Error('无法确认当前 Profile 的插件，已停止 Alcor 启动。')
  if (selected.includes('@stella/secretary')) throw new Error('旧版 Stella 插件与 Alcor 不能同时启用，请先迁移 Profile。')
}

export default class Secretary extends TypertRemoteService {
  static inject = ['credentials', 'storageDomain', 'typert', 'tools', 'workspaceRegistry', 'sessionQuery', 'agents', 'agentPresets', 'llm', 'agentDefaultModel', 'schedule']
  static Config = z.object({ displayName: z.string().min(1).max(80).default('Alcor').volatile() })

  constructor(ctx, config) {
    assertCompatibleProfile(ctx)
    super(ctx, 'alcorSecretary', { namespace: 'alcorSecretary' })
    this.config = config
  }

  async [Service.init]() {
    assertCompatibleProfile(this.ctx)
    await initializeProfile(this.ctx)
    this.domain = await this.ctx.storageDomain.open(domainSpec)
    this.ctx.effect(() => () => this.domain.close())
    const spaces = await this.ctx.storageDomain.open(defineDomain({ name: 'alcor_spaces', version: 1, tables: { ownership: domainTable(ownershipSchema) } }))
    this.ctx.effect(() => () => spaces.close())
    this.productSpaces = new ProductSpaces(this.ctx, spaces.table('ownership'))
    const channelDomain=await this.ctx.storageDomain.open(defineDomain({name:'alcor_channels',version:1,tables:{
      routes:domainTable(channelRouteSchema),delivery:domainTable(deliveryStateSchema),messages:domainTable(cachedMessageSchema),
    }}))
    this.ctx.effect(()=>()=>channelDomain.close())
    this.channels=new ChannelRegistry(channelDomain.table('routes'),()=>this.ctx.workspaceRegistry.list().flatMap(row=>row.sessionIds),channelDomain.table('messages'),id=>this.ctx.sessionQuery.readSurface(id))
    this.ctx.on('session/event',(session,event)=>{
      if(['user/message','assistant/message'].includes(event.type) && this.channels.resolve(session.id))
        void this.channels.messages(session.id,[event]).catch(()=>this.ctx.logger.warn('通道记录摘要暂未保存，可从原会话恢复。'))
    })
    this.productSpaces.channels=this.channels
    this.memory = new AlcorMemory(this.ctx, this.productSpaces, this.config.displayName.get())
    this.delivery=new ChannelDelivery(this.ctx,this.channels,this.memory,channelDomain.table('delivery'))
    this.memory.delivery=this.delivery
    await this.delivery.initialize()
    const records = this.domain.table('installation')
    if (!records.get('product')) await records.put('product', { installedAt: new Date().toISOString() })
    this.ctx.typert.register({
      package: '@alcor/secretary', face: 'host', schemas: [],
      invocations: remote.descriptors, model: { services: [], events: [], objects: [] },
    })
  }

  @Remote
  async spaces(request) {
    accountSchemas.spaces[0].parse(request)
    return this.productSpaces.info()
  }

  @Remote
  async qqConversation(request) {
    const {sessionId}=accountSchemas.qqConversation[0].parse(request)
    const route=qqRoute(sessionId)
    if (!route || !this.ctx.workspaceRegistry.list().some(row=>row.sessionIds.includes(sessionId))) return null
    return await this.productSpaces.qqConversationInfo?.(route[1],route[2]) ?? null
  }

  @Remote
  async channelCatalog(request) {
    accountSchemas.channelCatalog[0].parse(request)
    return this.channels.catalog()
  }
  @Remote
  async channelConversation(request) {
    const {sessionId}=accountSchemas.channelConversation[0].parse(request)
    await this.channels.catalog()
    return this.channels.info(sessionId)
  }
  @Remote
  async channelHistory(request) {
    const {sessionId,beforeSeq}=accountSchemas.channelHistory[0].parse(request)
    await this.channels.catalog()
    if(!this.channels.table.get(sessionId))throw Error('通道会话不存在。')
    const platform=await this.channels.platformHistory(sessionId,beforeSeq)
    if(platform)return platform
    const surface=await this.ctx.sessionQuery.readSurface(sessionId)
    const events=surface.events.filter(e=>(beforeSeq==null || e.seq<beforeSeq) && ['user/message','assistant/message'].includes(e.type))
    const selected=events.slice(-80)
    const messages=await this.channels.messages(sessionId,selected)
    const from=selected[0]?.time ?? 0,to=beforeSeq==null?Infinity:(selected.at(-1)?.time ?? 0)
    for(const receipt of this.delivery.state().receipts)if(receipt.status==='sent' && receipt.route.sessionId===sessionId && receipt.updatedAt>=from && receipt.updatedAt<=to)
      messages.push({seq:-receipt.updatedAt,role:'Alcor',text:receipt.content,time:receipt.updatedAt})
    messages.sort((a,b)=>(a.time ?? 0)-(b.time ?? 0) || a.seq-b.seq)
    return {messages,hasMore:events.length>80,beforeSeq:selected[0]?.seq ?? null}
  }
  @Remote
  async deliverySettings(request) {
    accountSchemas.deliverySettings[0].parse(request)
    return this.delivery.settings()
  }
  @Remote
  async allowChannelDelivery(request) {
    const {sessionId,enabled}=accountSchemas.allowChannelDelivery[0].parse(request)
    return this.delivery.configure(sessionId,enabled)
  }

  @Remote
  async claimSpace(request) {
    return this.productSpaces.claim(accountSchemas.claimSpace[0].parse(request))
  }

  @Remote
  async editProject(request) {
    return this.productSpaces.editProject(accountSchemas.editProject[0].parse(request))
  }

  @Remote
  async searchSpace(request) {
    const { space, query, sessionIds } = accountSchemas.searchSpace[0].parse(request)
    return this.productSpaces.search(query, sessionIds, space)
  }

  @Remote
  memoryState(request) {
    accountSchemas.memoryState[0].parse(request)
    return { sharedRecall:this.memory.personal.sharedRecall, initiative: this.memory.personal.initiative, desktopDelivery:this.memory.personal.desktopDelivery, soul: this.memory.personal.soul, entries: this.memory.personal.records().slice(0, 200) }
  }

  @Remote
  async updateSoul(request) {
    return this.memory.personal.editIdentity(accountSchemas.updateSoul[0].parse(request))
  }

  @Remote
  allowSharedRecall(request) {
    return this.memory.personal.allowSharedRecall(accountSchemas.allowSharedRecall[0].parse(request).enabled)
  }

  @Remote
  async allowInitiative(request) {
    const enabled=accountSchemas.allowInitiative[0].parse(request).enabled
    const result=await this.memory.personal.allowInitiative(enabled)
    if(enabled && this.delivery.hasActiveGrants())await this.delivery.ensureJob()
    if(!enabled)await this.delivery.pause()
    return result
  }

  @Remote
  allowDesktopDelivery(request) {
    return this.memory.personal.allowDesktopDelivery(accountSchemas.allowDesktopDelivery[0].parse(request).enabled)
  }

  @Remote
  async nextFollowup(request) {
    accountSchemas.nextFollowup[0].parse(request)
    const candidate=()=>this.memory.personal.desktopFollowup(new Date(), note=>note.kind !== 'idle' && !this.delivery.assigned(note.id))
    const planned=candidate()
    const note=await this.memory.personal.reconsider(planned,()=>candidate()?.id===planned?.id)
    if(note && (!this.memory.personal.validReview(note.id,note.stamp) || candidate()?.id!==note.id))return null
    if(!note)return null
    return {id:note.id,evidence:note.evidence,content:note.content,due:note.due}
  }

  @Remote
  dismissFollowup(request) {
    return this.memory.personal.dismissFollowup(accountSchemas.dismissFollowup[0].parse(request).id)
  }

  @Remote
  experienceScopes(request) {
    accountSchemas.experienceScopes[0].parse(request)
    return this.memory.experienceScopes()
  }

  @Remote
  exportExperiences(request) {
    const { audience } = accountSchemas.exportExperiences[0].parse(request)
    return this.memory.exportExperiences(audience)
  }

  @Remote
  restoreExperiences(request) {
    const { document } = accountSchemas.restoreExperiences[0].parse(request)
    const target = this.memory.experienceStore(document.audience)
    return target.restoreExperiences(document, () => {
      this.memory.experienceStore(document.audience)
      return this.memory.personal.identity().id
    })
  }

  @Remote
  exportIdentity(request) {
    accountSchemas.exportIdentity[0].parse(request)
    return this.memory.personal.identity()
  }

  @Remote
  importIdentity(request) {
    const { document, expectedRevision } = accountSchemas.importIdentity[0].parse(request)
    return this.memory.importIdentity(document, expectedRevision)
  }

  @Remote
  async changeMemory(request) {
    const args = accountSchemas.changeMemory[0].parse(request)
    return this.memory.personal.change(args, { owner: true, subject: 'owner', source: { kind: 'desktop-settings', at: new Date().toISOString() } })
  }

  @Remote
  async elearningAccount(request) {
    accountSchemas.elearningAccount[0].parse(request)
    return (permits(selectedScope(this.ctx,'@alcor/elearning'),'alcor') ? this.ctx.get('alcorElearning') : undefined)?.state({ includeUsername: true }) ?? { installed: false, configured: false, connected: false, writable: false }
  }

  @Remote
  async saveElearningAccount(request) {
    const service = permits(selectedScope(this.ctx,'@alcor/elearning'),'alcor') && this.ctx.get('alcorElearning')
    if (!service) throw new Error('请先在 DSH 中配置 eLearning 连接。')
    return service.save(request)
  }

  @Remote
  async removeElearningAccount(request) {
    accountSchemas.removeElearningAccount[0].parse(request)
    const service = permits(selectedScope(this.ctx,'@alcor/elearning'),'alcor') && this.ctx.get('alcorElearning')
    if (!service) throw new Error('eLearning 连接未配置。')
    return service.remove()
  }

  @Remote
  async checkElearning(request) {
    accountSchemas.checkElearning[0].parse(request)
    if(!permits(selectedScope(this.ctx,'@alcor/elearning'),'alcor'))return {status:'disconnected'}
    return checkConnection(this.ctx)
  }

  @Remote
  overview() {
    return overviewSchema.parse({
      displayName: this.memory.personal.identity().name,
      installedAt: this.domain.table('installation').get('product').installedAt,
      message: '准备好和你一起安排今天。',
    })
  }
}
