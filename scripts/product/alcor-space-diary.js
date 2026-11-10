import {DatabaseSync} from 'node:sqlite'
import {createHash} from 'node:crypto'
import {join} from 'node:path'

const hour=3600000
const reactions={cheer:'用小熊击掌图庆祝',huh:'用小熊歪头图接梗',company:'用小熊陪伴图回应',night:'用小熊晚安图道别'}
const topics=[[/编程|代码|调试/,'编程'],[/乐器|钢琴|吉他|音乐/,'音乐'],[/天文|星空|星座/,'星空'],[/绘画|插画|画画/,'画画'],[/做饭|烘焙|菜谱/,'做饭'],[/游戏|通关/,'游戏'],[/植物|园艺|养花/,'植物']]
export function replyExperience(text) {
  const topic=topics.find(([pattern])=>pattern.test(text))?.[1]
  return topic ? '在 QQ 回复了关于'+topic+'的话题' : '在 QQ 回复了一段对话'
}
export function stickerExperience(id) { return reactions[id] || '在 QQ 用表情回应' }

export function createSpaceDiary({file,account,active,decide,publish,now=Date.now,log=()=>{}}) {
  const db=new DatabaseSync(file)
  db.exec('PRAGMA busy_timeout=2000; PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS experiences(id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, receipt TEXT NOT NULL, at INTEGER NOT NULL, fact TEXT NOT NULL, UNIQUE(account,receipt)); CREATE TABLE IF NOT EXISTS diary(account TEXT PRIMARY KEY, cursor INTEGER NOT NULL DEFAULT 0, reviewed INTEGER NOT NULL DEFAULT 0, attempted INTEGER NOT NULL DEFAULT 0, status TEXT, tid TEXT)')
  db.prepare('INSERT OR IGNORE INTO diary(account) VALUES(?)').run(account)
  let busy=false,closed=false
  function record(chat,id,fact) {
    if(closed || !id || !active())return
    const key=createHash('sha256').update(JSON.stringify([chat,id])).digest('hex')
    try {
      db.prepare('DELETE FROM experiences WHERE account=? AND at<?').run(account,now()-24*hour)
      db.prepare('INSERT OR IGNORE INTO experiences(account,receipt,at,fact) VALUES(?,?,?,?)').run(account,key,now(),fact)
    } catch { log('空间经历暂未保存') }
  }
  async function tick() {
    if(closed || busy || !active())return
    busy=true
    try {
      const state=db.prepare('SELECT * FROM diary WHERE account=?').get(account)
      if(now()-state.reviewed<hour || now()-state.attempted<24*hour)return
      const rows=db.prepare('SELECT id,fact FROM experiences WHERE account=? AND id>? AND at>=? ORDER BY id DESC LIMIT 24').all(account,state.cursor,now()-24*hour)
      if(!rows.length)return
      const facts=rows.reverse().map(row=>row.fact)
      const content=await decide(facts)
      if(closed || !active())return
      const end=rows.at(-1).id
      if(content===null){
        db.prepare('UPDATE diary SET cursor=?,reviewed=?,status=? WHERE account=? AND cursor=?').run(end,now(),'skip',account,state.cursor)
        return
      }
      if(typeof content!=='string' || !content.trim() || content.length>350)throw Error('无效的空间正文')
      // Commit the reservation before the remote write. A crash or lost receipt cannot replay it.
      const claim=db.prepare('UPDATE diary SET cursor=?,reviewed=?,attempted=?,status=?,tid=NULL WHERE account=? AND cursor=? AND attempted=?').run(end,now(),now(),'unknown',account,state.cursor,state.attempted)
      if(!claim.changes)return
      const result=await publish(content.trim())
      if(!closed && result?.tid)db.prepare("UPDATE diary SET status='published',tid=? WHERE account=? AND cursor=?").run(String(result.tid),account,end)
    } catch {
      if(!closed){
        db.prepare('UPDATE diary SET reviewed=? WHERE account=?').run(now(),account)
        log('本次空间决定或发表未确认')
      }
    } finally {busy=false}
  }
  return {record,tick,close(){closed=true;db.close()}}
}

export function attachSpaceDiary(ctx,bridge,config,connection,account) {
  if(config.alcorAutoQzone!==true)return ()=>{}
  const active=()=>config.alcorAutoQzone===true && config.alcorAutoReply!==false && !bridge.stopping &&
    connection.connected && String(connection.selfId)===account && !bridge.deps.config.inject?.intercepting
  const diary=createSpaceDiary({
    file:join(ctx.get('profileContext').dir,'alcor-qq-space.sqlite'), account,active,
    log:message=>bridge.deps.log('warn',message),
    decide:async facts=>{
      const route=bridge.deps.defaultModel?.()
      if(!route?.provider || !route.model)throw Error('没有空间写作模型')
      const signal=AbortSignal.timeout(40000)
      const settings={provider:route.provider,model:route.model,maxTokens:1500,temperature:0.55}
      const system='ALCOR_QZONE_DIARY。根据给出的已发生动作，决定是否值得写一条好友可见的小熊手记。可自然表达感想；琐碎重复、缺乏素材时跳过。不要杜撰人物、线下见闻、对话、成功结果或他人感受，不提联系人、群和私事。人格是表达参考，不是经历。只输出 JSON：{"post":null} 或 {"post":"正文"}。'
      const messages=[{role:'user',content:[{type:'text',text:JSON.stringify({persona:ctx.alcorSecretary.memory.publicPersona(),experiences:facts})}]}]
      const prepared=await ctx.llm.prepareCall?.(settings,signal,(controls,model)=>{
        const effort=['off','low'].find(id=>model.reasoning?.efforts?.some(option=>option.id===id))
        return effort ? {...controls,reasoningEffort:effort} : controls
      })
      const options={...(prepared?.config ?? settings),system,messages,signal}
      let delta='',final=''
      for await(const chunk of (prepared?prepared.stream(options):ctx.llm.stream(options))){
        if(chunk.type==='text-delta')delta+=chunk.text || ''
        if(chunk.type==='block-end' && chunk.block?.type==='text')final+=chunk.block.text || ''
        if(chunk.type==='finish' && chunk.reason?.kind==='error')throw Error('空间写作失败')
        if(delta.length+final.length>8000)throw Error('空间写作结果过长')
      }
      return JSON.parse((delta || final).trim().replace(/^```(?:json)?\s*|\s*```$/gi,'')).post
    },
    publish:async content=>{
      if(!active())throw Error('自主发布已停用')
      const gate=bridge.outbound.writeGateRejectReason('qzone:self','send_qzone_msg')
      if(gate)throw Error(gate)
      const result=await bridge.guardedConnectionCall('send_qzone_msg',{content,ugc_right:4})
      if(!result?.tid)throw Error('未取得空间回执')
      bridge.outbound.auditWrite('qzone:self','send_qzone_msg',true,'自主说说')
      return result
    },
  })
  bridge.spaceDiary=diary
  const timer=setInterval(()=>void diary.tick(),60000)
  timer.unref?.()
  return ()=>{clearInterval(timer);delete bridge.spaceDiary;diary.close()}
}
