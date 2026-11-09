import { randomUUID } from 'node:crypto'
import { setTimeout as wait } from 'node:timers/promises'
export class ChannelDelivery {
  constructor(ctx,channels,memory,table) {
    this.ctx=ctx;this.channels=channels;this.memory=memory;this.table=table;this.writes=Promise.resolve();this.running=Promise.resolve()
  }
  state(){return this.table.get('state') ?? {grants:[],receipts:[],job:null}}
  mutate(fn) {
    const run=this.writes.then(async()=>{const state=structuredClone(this.state());await fn(state);await this.table.put('state',state);return state})
    this.writes=run.catch(()=>{});return run
  }
  store(scope) {
    if(scope==='personal')return this.memory.personal
    return this.memory.storeForAudience(scope)
  }
  notes(scope){return this.store(scope).pendingFollowups().filter(note=>(note.generation ?? 0)===(this.memory.personal.initiativeGeneration ?? 0))}
  valid(grant,now=Date.now()) {
    return grant.until>now && !!this.channels.permitted(grant.route) &&
      (grant.route.audience==='personal'?grant.route.owner:true)
  }
  async initialize() {
    await this.mutate(state=>{for(const receipt of state.receipts)if(receipt.status==='sending'){receipt.status='unknown';receipt.updatedAt=Date.now()}})
    this.active=true
    this.ctx.effect(()=>async()=>{
      this.active=false
      if(this.ctx.root.fiber.state===2 && this.state().job)await this.ctx.schedule.edit({id:this.state().job.id,action:'update',patch:{active:false}})
      await this.running
      await this.writes
    })
    this.ctx.on('agent/pre-step',async({agent,messages},next)=>{
      const job=this.state().job
      const incoming=messages.map(message=>({id:message.id,kind:message.source.kind}))
      if(!job || agent.session.id!==job.sessionId)return next()
      // This session is reserved for receipt-verified host dispatch and never runs agent tools.
      if(incoming.length && incoming.every(message=>message.kind==='schedule')) {
        try {
          for(const delay of [0,25,75,200,400,800]){
            if(delay)await wait(delay)
            const current=await this.ctx.schedule.view({id:job.id})
            if(current?.prompt!==job.prompt || current?.target?.sessionId!==job.sessionId)break
            if(incoming.some(message=>message.id===current.lastDelivery?.messageId) && current.lastDelivery.targetSessionId===job.sessionId){await this.drain();break}
          }
        } catch {this.ctx.logger.warn('通道跟进调度暂未完成，将由下一次宿主调度核对。')}
      }
      return {kind:'reject'}
    },{prepend:true})
    if(this.memory.personal.initiative && this.state().grants.some(g=>g.until>Date.now()))await this.ensureJob()
    else if(this.state().job)await this.pause()
  }
  async ensureJob() {
    return this.mutate(async state=>{
      if(state.job) {
        const job=await this.ctx.schedule.view({id:state.job.id})
        if(job && !job.code && job.prompt===state.job.prompt && job.target?.sessionId===state.job.sessionId) {
          if(job.status!=='active')await this.ctx.schedule.edit({id:job.id,action:'update',patch:{active:true}})
          return
        }
      }
      if(state.job)await this.ctx.schedule.edit({id:state.job.id,action:'update',patch:{active:false}})
      const spaces=await this.memory.spaces.info()
      const workspace=this.ctx.workspaceRegistry.list().find(row=>row.id===spaces.defaults.alcor)
      const prompt='Alcor channel delivery '+randomUUID()
      const job=await this.ctx.schedule.create({title:'Alcor 跟进投递',prompt,timing:{kind:'every',every_seconds:60},
        target:{kind:'new_once',create:{cwd:workspace.path,agentPreset:'alcor-delivery'}}})
      state.job={id:job.id,prompt,sessionId:job.target.sessionId}
    })
  }
  async configure(sessionId,enabled) {
    await this.channels.catalog()
    const route=this.channels.table.get(sessionId) && this.channels.resolve(sessionId)
    if(!route)throw Error('通道会话不存在。')
    if(enabled && (!this.channels.permitted(route) || (route.kind==='private' && !route.owner)))throw Error('请选择当前可用的主人私聊或已启用群聊。')
    if(enabled && route.kind==='group' && !this.memory.canManageAudience(route.audience))throw Error('主人尚未参与此群，无法授权主动投递。')
    if(enabled && this.memory.personal.initiative)await this.ensureJob()
    await this.mutate(state=>{
      state.grants=state.grants.filter(g=>g.route.sessionId!==sessionId && !(enabled && route.audience==='personal' && g.route.audience==='personal'))
      if(enabled){
        const grant={route,nonce:randomUUID(),until:Date.now()+7*86400000}
        state.grants.push(grant)
        for(const receipt of state.receipts)if(receipt.route.sessionId===sessionId && receipt.status==='pending')receipt.grant=grant.nonce
      }
    })
    if(enabled && route.audience!=='personal')await this.store(route.audience).allowInitiative(true)
    await this.trackAll()
    if(!this.state().grants.some(grant=>grant.until>Date.now()))await this.pause()
    return {ok:true}
  }
  async pause() {
    const job=this.state().job
    if(job)await this.ctx.schedule.edit({id:job.id,action:'update',patch:{active:false}})
  }
  async track(scope,note) {
    await this.mutate(state=>{
      if(state.receipts.some(row=>row.id===note.id))return
      const grant=state.grants.find(g=>g.route.audience===scope && this.valid(g))
      if(grant && (note.generation ?? 0)===(this.memory.personal.initiativeGeneration ?? 0))state.receipts.push({id:note.id,scope,content:note.content,route:grant.route,grant:grant.nonce,status:'pending',updatedAt:Date.now()})
    })
  }
  async trackAll(){
    for(const scope of new Set(this.state().grants.map(g=>g.route.audience))) {
      try{for(const note of this.notes(scope))await this.track(scope,note)}catch{}
    }
  }
  assigned(id){return this.state().receipts.some(row=>row.id===id && row.status!=='desktop')}
  next(scope) {
    return this.store(scope).nextFollowup(new Date(),note=>(note.generation ?? 0)===(this.memory.personal.initiativeGeneration ?? 0) && !this.state().receipts.some(row=>row.id===note.id && ['sent','unknown','cancelled','expired','desktop'].includes(row.status)))
  }
  authorized(receipt) {
    if(!this.active || !this.memory.personal.initiative)return false
    const grant=this.state().grants.find(g=>g.nonce===receipt.grant && g.route.sessionId===receipt.route.sessionId)
    try{return !!grant && this.valid(grant) && (receipt.scope==='personal' || this.memory.canManageAudience(receipt.scope)) && this.next(receipt.scope)?.id===receipt.id}catch{return false}
  }
  drain() {
    const run=this.running.then(()=>this.dispatch())
    this.running=run.catch(()=>{});return run
  }
  async dispatch(){
    if(!this.memory.personal.initiative || !this.state().grants.some(g=>g.until>Date.now())){await this.pause();return}
    await this.trackAll()
    await this.mutate(state=>{state.receipts=state.receipts.filter(row=>row.status==='pending' || row.updatedAt>Date.now()-30*86400000)})
    for(const receipt of this.state().receipts.filter(r=>r.status==='pending')) {
      let store
      try{store=this.store(receipt.scope)}catch{continue}
      const note=this.next(receipt.scope)
      const live=this.notes(receipt.scope).find(n=>n.id===receipt.id)
      if(!live){await this.status(receipt.id,'cancelled');continue}
      if(Date.now()-live.due>=86400000){await this.status(receipt.id,'expired');continue}
      if(!this.authorized(receipt))continue
      if(note?.id!==receipt.id)continue
      const adapter=this.channels.adapter(receipt.route)
      if(!adapter)continue
      const destinations=['channel']
      if(receipt.scope==='personal' && store.desktopDelivery)destinations.push('desktop')
      const reviewed=await store.reconsider(note,()=>this.authorized(receipt),destinations)
      if(!reviewed){
        if(!this.notes(receipt.scope).some(row=>row.id===receipt.id))await this.status(receipt.id,'cancelled')
        continue
      }
      const ready=()=>this.authorized(receipt) && store.validReview(receipt.id,reviewed.stamp)
      if(!ready())continue
      if(reviewed.destination==='desktop'){
        await this.mutate(state=>{
          const row=state.receipts.find(r=>r.id===receipt.id)
          if(row && receipt.scope==='personal' && store.desktopDelivery && ready()){
            row.status='desktop';row.content=reviewed.content;row.updatedAt=Date.now()
          }
        })
        continue
      }
      await this.mutate(state=>{
        const row=state.receipts.find(r=>r.id===receipt.id)
        if(row){row.status='sending';row.content=reviewed.content;row.updatedAt=Date.now()}
      })
      let result
      try{result=await adapter.send(receipt.route,reviewed.content,ready)}
      catch{result={status:'unknown'}}
      const status=result.status==='deferred'?'pending':result.status==='sent'?'sent':'unknown'
      await this.status(receipt.id,status,result.messageId)
      if(status==='sent')await store.dismissFollowup(note.id)
    }
  }
  status(id,status,messageId) {
    return this.mutate(state=>{const row=state.receipts.find(r=>r.id===id);if(row){row.status=status;row.updatedAt=Date.now();if(messageId)row.messageId=messageId}})
  }
  async settings(){
    const routes=await this.channels.catalog(true),state=this.state()
    return {targets:routes.filter(route=>route.kind==='group' || route.owner).map(route=>{
      const grant=state.grants.find(g=>g.route.sessionId===route.sessionId)
      return {route,until:grant?.until ?? 0,available:!!this.channels.permitted(route) && (route.kind!=='group' || this.memory.canManageAudience(route.audience))}
    }),receipts:state.receipts.slice(-50)}
  }
}
