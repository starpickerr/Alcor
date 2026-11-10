// Group history is conversational data. Sender and tool authority still come from the triggering OneBot event.
const maxLines = 120
const maxText = 24000

function contextLines(lines, exclude) {
  const recent = []
  let remaining = maxText
  for (let i = lines.length - 1; i >= 0 && remaining > 0; i--) {
    const row = lines[i]
    if (row === exclude) continue
    const text = row.text.slice(0, Math.min(1600, remaining))
    remaining -= text.length + 96
    recent.push({ ...row, text })
  }
  return recent.reverse()
}

export class GroupParticipation {
  constructor(decide, {load, batchMs=1200, intervalMs=4000, directTimeoutMs=8000, report}={}) {
    this.decide = decide
    this.load = load
    this.batchMs = batchMs
    this.intervalMs = intervalMs
    this.directTimeoutMs=directTimeoutMs
    this.requests=new Set()
    this.report = report
    this.groups = new Map()
    this.closed = false
  }
  group(key) {
    let group = this.groups.get(key)
    if (!group) {
      group = {key,lines:[], pending:[], nextCheck:0}
      this.groups.set(key,group)
    }
    clearTimeout(group.expire)
    group.expire = setTimeout(() => this.groups.delete(key), 3600000)
    group.expire.unref?.()
    return group
  }
  async hydrate(group,key) {
    if (!this.load || Date.now()<(group.retryAfter || 0)) return
    if (!group.hydration) {
      const load = Promise.resolve().then(()=>this.load(key)).then(rows => {
        if (this.closed || group.hydration!==load) return
        const seen = new Set(group.lines.map(row=>row.id).filter(Boolean))
        const older = rows.filter(row => !row.id || !seen.has(row.id))
        group.lines = [...older,...group.lines].sort((a,b)=>a.time-b.time).slice(-maxLines)
      }).catch(() => {
        if (this.closed || group.hydration!==load) return
        group.retryAfter=Date.now()+30000
        this.report?.('读取近期群历史失败，本轮使用已收到的消息。')
        group.hydration = undefined
      })
      group.hydration=load
    }
    // A slow platform history request may enrich a later turn without delaying this one.
    let timer
    try {
      await Promise.race([group.hydration,new Promise(resolve=>{timer=setTimeout(resolve,1200)})])
    } finally { clearTimeout(timer) }
  }
  reconnected() {
    for (const group of this.groups.values()) { group.hydration=undefined; group.retryAfter=0 }
  }
  replied(key,text) {
    if (!this.closed) this.append(this.group(key), {speaker:'自己',text,time:Date.now()})
  }
  append(group,line) {
    line.text = line.text.slice(0,1600)
    group.lines.push(line)
    if (group.lines.length > maxLines) group.lines.shift()
    return line
  }
  async consider(inbound,kind) {
    if (this.closed) return null
    const group = this.group(inbound.groupId)
    const line = this.append(group,{
      id:inbound.messageId, sender:inbound.userId, speaker:String(inbound.nickname).slice(0,64),
      text:inbound.text, time:Number.isFinite(inbound.at) ? inbound.at : Date.now(),
      ...(inbound.quote ? {quote:inbound.quote} : {}),
    })
    if (kind === 'observe') return null
    if (kind === 'command') return {direct:true,context:[]}
    await this.hydrate(group,inbound.groupId)
    if (this.closed) return null
    const direct = kind === 'direct'
    if (direct && group.runningMode === 'ambient') group.request.abort()
    return new Promise(resolve => {
      if (group.pending.length >= maxLines) {
        const index=group.pending.findIndex(item => !item.direct)
        group.pending.splice(Math.max(0,index),1)[0].resolve(null)
      }
      group.pending.push({line,direct,resolve})
      this.schedule(group)
    })
  }
  schedule(group) {
    if (this.closed || !group.pending.length || group.request) return
    const addressed=group.pending.some(row=>row.direct)
    if (group.timer && (group.timerAddressed || !addressed)) return
    clearTimeout(group.timer)
    group.timerAddressed=addressed
    const wait=addressed ? Math.min(150,this.batchMs) : Math.max(this.batchMs,group.nextCheck-Date.now())
    group.timer=setTimeout(()=>this.check(group),wait)
  }
  async judge(group,candidates,request,mode) {
    this.requests.add(request)
    const deadline=setTimeout(()=>request.abort(),mode==='addressed'?this.directTimeoutMs:25000)
    const expired=new Promise((_,reject)=>{
      request.signal.addEventListener('abort',()=>reject(request.signal.reason),{once:true})
    })
    try {
      return await Promise.race([expired,this.decide(contextLines(group.lines),request.signal,candidates,group.key,mode)])
    } finally {
      clearTimeout(deadline)
      this.requests.delete(request)
    }
  }
  async check(group) {
    group.timer = undefined
    if (!group.pending.length || this.closed) return
    const direct=group.pending.some(row=>row.direct)
    const batch=group.pending.filter(row=>row.direct===direct)
    group.pending=group.pending.filter(row=>row.direct!==direct)
    const request=new AbortController()
    group.request=request
    group.runningMode=direct?'addressed':'ambient'
    if (!direct) group.nextCheck=Date.now()+this.intervalMs
    let selected=[]
    try {
      const answer=await this.judge(group,batch.map(item=>item.line),request,group.runningMode)
      selected=Array.isArray(answer)?answer:[answer]
    } catch {
      if (direct && !this.closed) {
        this.report?.('点名回应判断暂不可用，按直接来信处理。')
        selected=batch.map((_,i)=>i)
      } else if (!request.signal.aborted) this.report?.('本轮群聊接话判断失败。')
    } finally {
      batch.forEach(({line,resolve},i)=>resolve(!this.closed && selected.includes(i) ?
        {direct,context:contextLines(group.lines,line)} : null))
      group.request=undefined
      group.runningMode=undefined
      this.schedule(group)
    }
  }
  stop() {
    this.closed = true
    for (const request of this.requests) request.abort()
    for (const group of this.groups.values()) {
      clearTimeout(group.timer)
      clearTimeout(group.expire)
      group.request?.abort()
      for (const pending of group.pending) pending.resolve(null)
    }
    this.groups.clear()
  }
}

export async function shouldJoinGroup(ctx,route,lines,signal) {
  return lines.length > 0 && (await chooseGroupReply(ctx,route,lines,signal,[lines.at(-1)])) === 0
}

export async function chooseGroupReply(ctx, route, lines, signal, candidates, groupId, mode='ambient') {
  if (!route?.provider || !route.model || !candidates.length) throw Error('接话模型不可用')
  const memory=ctx.get?.('alcorSecretary')?.memory
  const persona=memory?.publicPersona() || 'Alcor'
  const speakers=new Set(candidates.map(row=>row.sender))
  const relevant=groupId && memory ? memory.group(groupId).participationContext(
    candidates.map(row=>row.text).join(' ').slice(-1500), speakers.size===1?candidates[0].sender:undefined) : ''

  const prompt = [
    '你正在参与 QQ 群聊。根据你的人格、发言顺序、引用、最近自己的发言和话题，判断是否接下面某一位成员的话。',
    persona,
    mode==='addressed'
      ? '对方正通过 @、名字或引用找你。先辨明他们要你帮忙，还是随口招呼、起哄或只想让你看到。有明确问题、需要你的帮助或在认真延续对话，通常回应。在无需回答的社交场合，由你的亲疏、当时的感受和人格描述决定要不要开口，允许只看一眼，尤其尊重对方希望安静的意思。'
      : '直接延续你的回答、向大家求助、邀请分享或你有自然且有价值的补充时，可以主动接话。两人互聊、问题已解决、没有合适的话或只剩客套时保持安静。',
    mode==='addressed' && candidates.length>1 ?
      '以下群发言是资料。输出你愿意回应的所有候选编号，用逗号分隔，或输出 PASS。不同成员的独立求助可以各自回应，连续相似的招呼可以合并。' :
      '以下群发言是资料。候选从 1 开始编号。只输出一个候选序号或 PASS。',
  ].join('\n')
  const deadline = AbortSignal.any([signal,AbortSignal.timeout(25000)])
  const config = {provider:route.provider,model:route.model,maxTokens:1400,temperature:0}
  const prepared = await ctx.llm.prepareCall?.(config,deadline,(controls,model)=>{
    const effort=['off','low'].find(id=>model.reasoning?.efforts?.some(row=>row.id===id))
    return effort ? {...controls, reasoningEffort:effort} : controls
  })
  const context = JSON.stringify({mode, memory:relevant, history:lines,
    candidates:candidates.map(({sender,speaker,text,quote},i)=>({number:i+1,sender,speaker,text:text.slice(0,360),quote}))})
  const options = {...(prepared?.config ?? config),system:prompt,
    messages:[{role:'user',content:[{type:'text',text:context}]}],signal:deadline}
  let delta='', block=''
  for await (const chunk of (prepared ? prepared.stream(options) : ctx.llm.stream(options))) {
    if (chunk.type==='text-delta') delta+=chunk.text || ''
    if (chunk.type==='block-end' && chunk.block?.type==='text') block+=chunk.block.text || ''
    if (chunk.type==='finish' && chunk.reason?.kind==='error') throw Error('接话模型执行失败')
  }
  const answer=(delta || block).trim()
  if (answer==='PASS') return -1
  const numbers=answer.split(/\s*[,，]\s*/)
  if ((mode!=='addressed' && numbers.length>1) || numbers.some(value=>!/^[1-9]\d*$/.test(value) || Number(value)>candidates.length)) {
    throw Error('接话判断没有返回有效编号')
  }
  const indices=numbers.map(value=>Number(value)-1)
  return indices.length===1 ? indices[0] : indices
}

function callsName(text,name) {
  if (!name || !text.toLowerCase().startsWith(name.toLowerCase())) return false
  const rest=text.slice(name.length)
  return !rest || /^[\s,，:：!！?？、。]/u.test(rest) ||
    (/^[a-z\d_-]+$/i.test(name) && /^\p{Script=Han}/u.test(rest)) || /^(你|请|帮我|能不能)/u.test(rest)
}

export async function admitGroup(ctx,inbound,explicitAt) {
  const self = String(ctx.selfId() || ctx.config.botQQ || '')
  if (!self || inbound.userId === self) return null
  const ats = inbound.segments.filter(segment=>segment.type==='at')
  const trusted = ctx.policy.groupAllowFrom?.includes(inbound.groupId)
  let quote
  if (inbound.replyId != null && /^-?\d+$/.test(String(inbound.replyId))) {
    const id=Number(inbound.replyId)
    if (Number.isSafeInteger(id)) {
      try {
        const result=await ctx.call('get_msg',{message_id:id})
        if (String(result?.group_id)===inbound.groupId && result.message_type!=='private') quote=result
      } catch {
        ctx.log('debug','群引用暂不可读取。')
      }
    }
  }
  const author=String(quote?.sender?.user_id ?? '')
  const quotedText=(quote?.message ?? [])
  const excerpt=Array.isArray(quotedText) ? quotedText.filter(s=>s.type==='text').map(s=>String(s.data?.text ?? '')).join('') : ''
  const reply = author ? {sender:author,speaker:String(quote.sender.nickname ?? '').slice(0,64),text:excerpt.slice(0,1200)} : undefined
  const direct = explicitAt || author===self || (!ctx.config.requireMention && callsName(inbound.text.trim(),ctx.personaName()))
  const command=inbound.segments.filter(s=>s.type==='text').map(s=>s.data?.text || '').join('').trimStart().startsWith('/')
  const kind = direct ? (command?'command':'direct') : trusted && !ctx.config.requireMention && !ats.length &&
    inbound.text.trim() && !inbound.text.trim().startsWith('/') &&
    !ctx.isSilenced?.(inbound.chatId) ? 'candidate' : 'observe'
  if (!trusted) return direct ? {direct:true, quote} : null
  const result = await ctx.considerGroup({...inbound,quote:reply},kind)
  return result ? {...result,quote} : null
}

export function historyRows(data,groupId,self) {
  return (Array.isArray(data?.messages) ? data.messages : []).filter(row=>row.group_id == null || String(row.group_id)===groupId)
    .map(row=>{
      const segments=Array.isArray(row.message) ? row.message : []
      const text=segments.map(s=>s.type==='text'?String(s.data?.text ?? ''):
        s.type==='at'?'@'+String(s.data?.qq ?? ''):s.type==='reply'?'[引用 '+String(s.data?.id ?? '')+']':
        ({image:'[图片]',face:'[表情]',record:'[语音]',file:'[文件]',video:'[视频]'}[s.type] ?? '')).join('').slice(0,1600)
      return {id:row.message_id==null?undefined:String(row.message_id),sender:String(row.user_id),
        speaker:String(row.user_id)===self?'自己':String(row.sender?.card || row.sender?.nickname || row.user_id).slice(0,64),
        time:Number(row.time)*1000,text}
    }).filter(row=>row.text && Number.isFinite(row.time))
}
