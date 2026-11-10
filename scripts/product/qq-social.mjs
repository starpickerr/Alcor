import {stickerExperience} from './alcor-space-diary.js'
import {readFile} from 'node:fs/promises'
import {createHash} from 'node:crypto'

const bears = new Map([
  ['cheer','小熊击掌庆祝，适合好消息或事情终于做成了'],
  ['huh','小熊歪头疑惑，适合意外反转或熟人轻松斗嘴'],
  ['company','小熊安静陪伴，适合对方想有人听自己说'],
  ['night','小熊收工，适合结束忙碌或互道晚安'],
])
const output={schema:{type:'object'},render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]}
const string=description=>({type:'string',description})
const digest=text=>createHash('sha256').update(text).digest('hex').slice(0,24)
function favorite(row) {
  let url
  try { url=new URL(row?.url) } catch { return }
  if (!['https:','http:'].includes(url.protocol) || url.username || url.password || url.port ||
      !/(^|\.)(qpic\.cn|qq\.com|qq\.com\.cn)$/i.test(url.hostname)) return
  url.protocol='https:'
  return {id:'fav:'+digest(String(row.md5 || row.resId || url.href)),url:url.href,
    label:String(row.desc || row.emojiDesc || '').trim().slice(0,90)}
}

// A verbatim publishing request comes from the authenticated QQ text, before any model or quoted content.
export function qzoneRequest(text) {
  const m=/^(?:请|帮我|请帮我)?(?:发|发布)(?:一条|个|条)?(?:QQ空间|空间)?说说(?:[（(](好友可见|仅自己可见)[）)])?[：:]([\s\S]+)$/u.exec(String(text || '').trim())
  const compose=!m && /^(?:请|帮我|请帮我)?(?:代写并发布|写并发|写并发布)(?:一条|条)?(?:QQ空间|空间)?说说(?:[（(](好友可见|仅自己可见)[）)])?[：:]([\s\S]+)$/u.exec(String(text || '').trim())
  const request=m || compose
  if(!request || !request[2].trim() || request[2].trim().length>1500)return
  return {text:request[2].trim(),compose:!!compose,audience:request[1]==='仅自己可见'?'self':'friends'}
}

export function registerSocial(ctx, bridge, chatId) {
  const {connection,config,policy}=bridge.deps
  const [kind,peer]=chatId.split(':')
  const memory=()=>bridge.deps.ctx.get('alcorSecretary').memory
  const access=(exec, ownerOnly=false)=>{
    const agent=exec?.agent, actor=memory().currentActor(agent)
    if(!actor || bridge.chatForSession(agent.session.id)!==chatId ||
       actor.source.kind!==(kind==='group'?'qq-group':'qq-private') ||
       (kind==='group'? actor.source.qqGroupId!==peer || !policy.groupAllowFrom.includes(peer) || policy.groupPolicy==='disabled' :
         actor.source.qqUserId!==peer || policy.dmPolicy==='disabled' || !(policy.adminUsers.includes(peer) || (policy.dmPolicy==='allowlist' && policy.allowFrom.includes(peer))))) throw Error('此回合没有当前 QQ 会话的权限。')
    if(ownerOnly && !(kind==='private' && actor.owner && policy.adminUsers.includes(peer))) throw Error('此操作只供主人私聊使用。')
    if(!config.botQQ || String(connection.selfId)!==String(config.botQQ) || !connection.connected ||
       bridge.stopping || config.alcorAutoReply===false || (bridge.silentUntil.get(chatId) || 0)>Date.now()) throw Error('QQ 已离线或此聊天暂停响应。')
    return actor
  }
  // A gateway timeout can mean the write succeeded. Do not retry the same action in that turn.
  const attempts=new WeakMap()
  const write=(exec,action,verify,work)=>bridge.outbound.enqueue(chatId,async()=>{
    const actor=verify()
    const boundary=ctx.agents.turnBoundaryOf(exec.agent.session)
    if(!boundary || boundary.openTurnStartSeq==null)throw Error('没有进行中的 QQ 回合。')
    let state=attempts.get(exec.agent)
    if(!state || state.start!==boundary.openTurnStartSeq || state.turn!==boundary.lastTurn){
      state={start:boundary.openTurnStartSeq,turn:boundary.lastTurn,actions:new Set()}
      attempts.set(exec.agent,state)
    }
    if(state.actions.has(action))throw Error('此回合已尝试该操作，请勿重复执行。')
    const denied=bridge.outbound.writeGateRejectReason(chatId,action)
    if(denied)throw Error(denied)
    state.actions.add(action)
    try {
      const result=await work(actor)
      bridge.outbound.auditWrite(chatId,action,true,'社交动作完成')
      return result
    } catch {
      bridge.outbound.auditWrite(chatId,action,false,'社交动作未确认')
      throw Error('网关未确认结果，请勿重复执行。')
    }
  })
  function forTurn(exec, ownerOnly=false) {
    const original=ctx.agents.turnBoundaryOf(exec?.agent?.session)
    const start=original?.openTurnStartSeq, turn=original?.lastTurn
    access(exec,ownerOnly)
    if(start==null || !Number.isSafeInteger(turn))throw Error('没有有效的 QQ 回合。')
    return ()=>{
      const current=ctx.agents.turnBoundaryOf(exec.agent.session)
      if(current?.openTurnStartSeq!==start || current.lastTurn!==turn)throw Error('QQ 回合已结束。')
      return access(exec,ownerOnly)
    }
  }
  const listFavorites=async exec=>{
    const verify=forTurn(exec,true)
    const rows=await connection.call('fetch_custom_face_detail',{count:48})
    verify()
    if(!Array.isArray(rows))throw Error('网关没有返回收藏表情清单。')
    return rows.map(favorite).filter(item=>item?.label)
  }
  const tool=(name,description,parameters,execute)=>ctx.tools.register({name,description,parameters,output,execute})
  tool('qq_list_stickers','列出当前聊天可用的表情包和含义。主人私聊还可查看自己带备注的 QQ 收藏。',{},async(_args,exec)=>{
    const actor=access(exec)
    const items=Array.from(bears,([id,label])=>({id,label}))
    if(kind==='private' && actor.owner){
      try {items.push(...(await listFavorites(exec)).map(({id,label})=>({id,label})))}
      catch {access(exec,true);return {items,note:'私人收藏暂不可用，小熊表情可用。'}}
    }
    return {items}
  })
  tool('qq_send_sticker','把一张贴合此刻语境的表情发到当前聊天。用清单中的 ID。适合时图本身就可以回答；发送成功后可用 [沉默] 结束本轮。',{id:{...string('表情清单中的 ID'),required:true}},async({id},exec)=>{
    const verify=forTurn(exec,!bears.has(id))
    let file
    if(bears.has(id)) file='base64://'+(await readFile(new URL('./stickers/'+id+'.png',import.meta.url))).toString('base64')
    else {
      const picked=(await listFavorites(exec)).find(row=>row.id===id)
      if(!picked)throw Error('该表情不在当前可用清单。')
      file=picked.url
    }
    return write(exec,'send_msg',verify,async()=>{
      const messageId=await bridge.sendMsg(chatId,[{type:'image',data:{file,sub_type:1}}],{})
      if(!messageId)throw Error('缺少 QQ 消息回执')
      bridge.spaceDiary?.record(chatId,messageId,stickerExperience(id))
      bridge.alcorStickerReplies ??= new Set()
      bridge.alcorStickerReplies.add(chatId)
      return {sent:true,messageId}
    })
  })
  tool('qq_poke_back','在轻松的互动中拍拍本轮发言人。只在当前聊天使用，不能指定第三人。',{},(_args,exec)=>
    write(exec,'send_poke',forTurn(exec),async(actor)=>{
      const params={user_id:actor.source.qqUserId,...(kind==='group'?{group_id:peer}:{})}
      await bridge.guardedConnectionCall('send_poke',params,chatId)
      return {sent:true}
    }))
  if(kind==='private' && policy.adminUsers.includes(peer)){
    tool('qq_write_qzone','起草小熊的文字说说；主人在私聊给出“发条说说：正文”时按原文发布；“代写并发布说说：主题”允许围绕主题创作并发布。只写本次明确给的公开主题，不引用其他聊天。否则返回草稿及可直接发送的发布指令。',{text:{...string('说说正文，最多1500字'),required:true}},async({text},exec)=>{
      const verify=forTurn(exec,true)
      if(typeof text!=='string' || !text.trim() || text.trim().length>1500)throw Error('说说正文需为 1–1500 字。')
      const content=text.trim(), request=memory().turns.get(exec.agent)?.rawSource?.alcorQzone
      if(!request || (!request.compose && request.text!==content) || !['friends','self'].includes(request.audience)){
        return {published:false,draft:content,ownerMessage:'发条说说（好友可见）：'+content}
      }
      return write(exec,'send_qzone_msg',verify,async()=>{
        if (memory().turns.get(exec.agent)?.rawSource?.alcorQzone !== request) throw Error('发布指令已经失效。')
        const data=await bridge.guardedConnectionCall('send_qzone_msg',{content,ugc_right:request.audience==='self'?64:4},chatId)
        if(!data?.tid)throw Error('缺少空间回执')
        return {published:true,tid:String(data.tid),audience:request.audience}
      })
    })
  }

}
