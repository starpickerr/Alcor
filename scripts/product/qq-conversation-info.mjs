import {platformHistory} from './alcor-platform-history.js'
import { dmAllowed, groupAllowed } from './chat.js'
import { historyMessages } from './alcor-history.js'

// Platform identities and permissions are checked here, before Alcor receives normalized events.
export function installConversationInfo(ctx,connection,bridge,config,policy) {
  const account=String(config.botQQ || connection.selfId || '')
  if(!/^[1-9]\d{0,19}$/.test(account))throw Error('QQ 接入需要配置专用账号 botQQ。')
  const registry=ctx.alcorSecretary.channels
  const resolve=sessionId=>{
    const chatId=bridge.registry.bySession.get(sessionId)
    const match=chatId ? /^(group|private):([1-9]\d{0,19})$/.exec(chatId)
      : /^onebot-(group|private)-([1-9]\d{0,19})(?:-|$)/.exec(sessionId)
    if(!match)return
    const [,kind,peer]=match,owner=kind==='private' && policy.adminUsers.includes(peer)
    return {kind,peer,owner,audience:kind==='group'?'group:'+peer:owner?'personal':'qq:'+account+':private:'+peer,
      label:ctx.alcorSecretary.productSpaces.table.get('ownership')?.qq?.[sessionId] || ''}
  }
  const allowed=route=>Number.isSafeInteger(Number(route.peer)) && (route.kind==='group'?groupAllowed(route.peer,policy):dmAllowed(route.peer,policy))
  const lookup=async route=>{
    const group=route.kind==='group',chatId=route.kind+':'+route.peer
    const info={online:connection.connected && String(connection.selfId || account)===account,
      response:!allowed(route)?'disabled':!group?'private':config.requireMention?'mention':policy.groupPolicy==='allowlist'?'ambient':'addressed',
      queue:config.newMessagePolicy,interim:bridge.effectiveInterim(chatId),limit:config.rateLimitPerMinute}
    if(!group)info.admin=policy.adminUsers.includes(route.peer)
    if(!info.online || !Number.isSafeInteger(Number(route.peer)))return info
    try{
      const data=await connection.call(group?'get_group_info':'get_stranger_info',group?{group_id:Number(route.peer)}:{user_id:Number(route.peer)})
      const name=group?data.group_name:data.nickname
      if(typeof name==='string' && name.trim())info.name=name.trim().slice(0,128)
      if(group && Number.isSafeInteger(data.member_count) && data.member_count>=0)info.members=data.member_count
    }catch{}
    return info
  }
  const release=registry.register({id:'qq',account,label:'QQ',sourceKind:'plugin:dsh-onebot',resolve,
    canSend:route=>connection.connected && String(connection.selfId || account)===account && allowed(route) &&
      !(bridge.registry.getSettings(route.kind+':'+route.peer).silentUntil>Date.now()),
    info:lookup,
    async history(route,before){
      if(route.kind!=='group')return null;
      if(!connection.connected || String(connection.selfId || account)!==account || !allowed(route))throw Error('群消息暂不可读取。');
      const data=await connection.call('get_group_msg_history',{group_id:Number(route.peer),count:80,reverseOrder:true,...(before!=null?{message_seq:before}: {})});
      if(!Array.isArray(data?.messages))return null;
      for(const row of data.messages)await record({kind:'group',groupId:route.peer,userId:String(row.user_id),
        messageId:String(row.message_id ?? row.message_seq ?? ''),segments:Array.isArray(row.message)?row.message:[],at:Number(row.time)*1000});
      const messages=platformHistory(data,before,account);
      return {messages,hasMore:data.messages.length>=80 && messages.length>0,beforeSeq:messages[0]?.cursor ?? null};
    },
    actor(route,source) {
      const user=source.qqUserId,group=route.kind==='group'
      if(typeof user!=='string' || !/^[1-9]\d{0,19}$/.test(user))return
      const owner=policy.adminUsers.includes(user)
      if(group ? source.qqGroupId!==route.peer || !policy.groupAllowFrom.includes(route.peer) : !owner || user!==route.peer)return
      return {subject:group?'qq:'+user:'owner',audience:route.audience,owner,
        source:{kind:group?'qq-group':'qq-private',sessionId:route.sessionId,qqUserId:user,...(group?{qqGroupId:route.peer}:{}),at:new Date().toISOString()}}
    },
    messages:(route,records)=>historyMessages(records,route.kind==='private'?route.peer:undefined),
    async send(route,text,authorize) {
      const chatId=route.kind+':'+route.peer
      return bridge.outbound.enqueue(chatId,async()=>{
        // Check at the wire boundary, after waiting for earlier messages in this chat.
        if(!authorize() || !allowed(route) || !connection.connected || String(connection.selfId || account)!==account ||
           bridge.registry.getSettings(chatId).silentUntil>Date.now())return {status:'deferred'}
        const reject=bridge.outbound.writeGateRejectReason(chatId,'alcor_followup')
        if(reject)return {status:'deferred'}
        try{
          const id=await bridge.sendMsg(chatId,[{type:'text',data:{text}}],{})
          if(!id)return {status:'unknown'}
          bridge.outbound.auditWrite(chatId,'alcor_followup',true,'delivered')
          return {status:'sent',messageId:String(id)}
        }catch{return {status:'unknown'}}
      })
    },
  })
  const record=async inbound=>{
    if(inbound.kind!=='group' || !groupAllowed(inbound.groupId,policy) || !connection.connected || String(connection.selfId || account)!==account || inbound.userId===account)return;
    const sessionId=[...bridge.registry.bySession].find(([,chat])=>chat==='group:'+inbound.groupId)?.[0];
    if(!sessionId)return;
    const route=registry.resolve(sessionId),actor=route && registry.actor({id:sessionId},{kind:'plugin:dsh-onebot',qqUserId:inbound.userId,qqGroupId:inbound.groupId});
    if(!actor)return;
    const text=(inbound.segments ?? []).filter(s=>s.type==='text').map(s=>String(s.data?.text ?? '')).join('').trim();
    if(text)ctx.alcorSecretary.memory.storeForAudience(route.audience,route).captureAmbient(inbound.messageId,text,actor,inbound.at ?? Date.now());
  };
  bridge.alcorRecordGroup=record;
  // Older secretary callers keep their read-only metadata endpoint.
  const lookupLegacy=(kind,number)=>lookup({kind,peer:number})
  ctx.alcorSecretary.productSpaces.qqConversationInfo=lookupLegacy
  return ()=>{if(bridge.alcorRecordGroup===record)delete bridge.alcorRecordGroup;release();if(ctx.alcorSecretary.productSpaces.qqConversationInfo===lookupLegacy)delete ctx.alcorSecretary.productSpaces.qqConversationInfo}
}
