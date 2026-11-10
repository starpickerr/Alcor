import test from 'node:test'
import assert from 'node:assert/strict'
import { GroupParticipation } from '../../scripts/product/qq-participation.mjs'

const inbound = (id,text,group = '10') => ({groupId:group, userId:'12', messageId:String(id), nickname:'M', text})

test('direct turns see same-group history after restart and context survives a quiet conversation', async () => {
  const groups = new GroupParticipation(async () => 0, {
    load: async group => [{id:'old', speaker:'S', text:'安排了周五远足：'+group, time:Date.now()-180000}],
  })
  try {
    const result = await groups.consider(inbound(1,'接着讲'), 'direct')
    assert.equal(result.context[0].text,'安排了周五远足：10')
    const other = await groups.consider(inbound(2,'聊聊','11'), 'direct')
    assert.deepEqual(other.context.map(row => row.text),['安排了周五远足：11'])
  } finally { groups.stop() }
})

test('new messages during an active decision do not cancel its speaker, and the next batch is evaluated', async () => {
  let unblock, started
  const began = new Promise(resolve => { started = resolve })
  const groups = new GroupParticipation(async (_,signal) => {
    if (!unblock) await new Promise(resolve => { unblock=resolve; started() })
    assert.equal(signal.aborted,false)
    return 0
  }, {batchMs:1,intervalMs:1})
  try {
    const first=groups.consider(inbound(1,'这个你怎么看'), 'candidate')
    await began
    const next=groups.consider(inbound(2,'接着解释一下'), 'candidate')
    unblock()
    assert.equal((await first).direct,false)
    assert.ok((await next).context.some(row=>row.text==='这个你怎么看'))
  } finally {groups.stop()}
})

import {execFileSync} from 'node:child_process'
import {mkdir,mkdtemp, readFile,writeFile,symlink,rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {fileURLToPath,pathToFileURL} from 'node:url'
import {adaptQQ,adaptedFiles} from '../../scripts/product/qq-alcor-adapter.mjs'
import {chooseGroupReply} from '../../scripts/product/qq-participation.mjs'
import {historyMessages} from '../../packages/alcor-secretary/src/qq-history.js'

test('actual QQ bridge retains quotes and persona-aware background in direct, ambient and busy turns', {skip:!process.env.ALCOR_QQ_SOURCE},async t=>{
  const dir=await mkdtemp(join(tmpdir(),'alcor-group-bridge-'))
  t.after(()=>rm(dir,{recursive:true,force:true}))
  execFileSync('tar',['-xf',process.env.ALCOR_QQ_SOURCE,'-C',dir])
  await symlink(fileURLToPath(new URL('../../node_modules',import.meta.url)),join(dir,'node_modules'))
  if (process.env.ALCOR_QQ_DEPENDENCIES) {
    await symlink(process.env.ALCOR_QQ_DEPENDENCIES,join(dir,'package/node_modules'))
  } else {
    // Group text admission must never invoke media rendering, even for a quoted message.
    const canvas=join(dir,'package/node_modules/@napi-rs/canvas')
    await mkdir(canvas,{recursive:true})
    await writeFile(join(canvas,'package.json'),JSON.stringify({type:'module',exports:'./index.js'}))
    await writeFile(join(canvas,'index.js'),"export function createCanvas(){throw Error('unexpected image operation')} export const loadImage=createCanvas; export const GlobalFonts={};")
  }
  for (const file of adaptedFiles) {
    const path=join(dir,'package',file)
    await writeFile(path,adaptQQ(file,await readFile(path,'utf8')))
  }
  await writeFile(join(dir,'package/lib/alcor-replies.js'),await readFile(new URL('../../scripts/product/qq-replies.mjs',import.meta.url)))
  const load=async name=>import(pathToFileURL(join(dir,'package/lib',name)))
  const {ChatBridge}=await load('bridge.js')
  const {InboundPipeline,normalizeOneBot11}=await load('inbound.js')
  let prompt='',answer='1'
  const soul='岚舟喜欢与朋友讨论星空，遇到分歧会说自己的理由。'
  const judgeCtx={get:()=>({memory:{publicPersona:()=>soul,group:id=>({
    participationContext:(query,sender)=>{
      assert.equal(id,'10')
      assert.equal(sender,'12')
      assert.ok(query)
      return 'M 上周在本群分享了天文摄影兴趣。'
    },
  })}}),llm:{async *stream({system,messages}){
    assert.match(system,/岚舟/)
    assert.ok(!system.includes('M 上周在本群分享了天文摄影兴趣。'))
    prompt=system+'\n'+messages[0].content[0].text
    yield {type:'text-delta',text:answer}
  }}}
  const turns=[], recorded=[]
  const groups=new GroupParticipation((lines,signal,candidates,id,mode)=>chooseGroupReply(judgeCtx,{provider:'test',model:'test'},lines,signal,candidates,id,mode),{
    batchMs:1,intervalMs:1,load:async()=>[{id:'old',sender:'24',speaker:'Other',text:'昨天投票选的是火星。',time:1}],
  })
  t.after(()=>groups.stop())
  const ctx={
    getChat:()=>({agent:{status:'running'}}), config:{botQQ:'99',rateLimitPerMinute:0,requireMention:false},
    policy:{groupPolicy:'allowlist',groupAllowFrom:['10'],adminUsers:['12']}, selfId:()=>'99',personaName:()=>'岚舟',
    considerGroup:(...args)=>groups.consider(...args), recordGroup:row=>recorded.push(row.messageId),
    call:async(_,params)=>({message_type:'group',group_id:params.message_id===77?'66':'10',sender:{user_id:'99',nickname:'岚舟'},
      message:[{type:'text',data:{text:params.message_id===77?'OTHER-GROUP-SECRET':'上面说的是火星。'}}]}),
    log(){},sweepIdleChats:async()=>{},media:{cleanupExpired:async()=>{}}, tryHandleCommand:async()=>false,
    buildBody:text=>text, expandQuote:(_,quote)=>'[引用]'+quote.message[0].data.text,
    interceptBusyTurn:async(...args)=>{turns.push(args); return true},
  }
  const pipeline=new InboundPipeline(ctx)
  let seq=200
  const receive=(message,group_id='10')=>ChatBridge.prototype.processInbound.call({inbound:pipeline},
    normalizeOneBot11({message_type:'group',group_id,user_id:12,message_id:++seq,sender:{nickname:'M'},message,time:1000+seq}))
  await receive([{type:'text',data:{text:'岚舟，昨天选了哪个？'}}])
  assert.equal(turns.length,1)
  assert.match(turns.at(-1)[1],/昨天投票选的是火星/)
  assert.equal(turns.at(-1)[5].ambient,undefined)
  assert.equal(turns.at(-1)[5].groupContext,true)
  const [message]=historyMessages([{type:'user/message',data:{source:{kind:'plugin:dsh-onebot',qqUserId:'12',qqGroupContext:true},content:[{type:'text',text:turns.at(-1)[1]}]}}])
  assert.equal(message.text,'岚舟，昨天选了哪个？')
  groups.replied('10','我也选火星。')
  await receive([{type:'text',data:{text:'为什么你也这样觉得？'}}])
  assert.equal(turns.length,2)
  assert.equal(turns.at(-1)[5].ambient,true)
  assert.match(prompt,new RegExp(soul))
  assert.match(prompt,/M 上周在本群分享了天文摄影兴趣。/)
  assert.match(prompt,/自己.*我也选火星/s)
  await receive([{type:'reply',data:{id:'42'}},{type:'text',data:{text:'再解释一下'}}])
  assert.match(turns.at(-1)[1],/\[引用\]上面说的是火星/)
  assert.equal(turns.at(-1)[5].ambient,undefined)
  answer='PASS'
  await receive([{type:'text',data:{text:'我和张三说两句'}}])
  assert.equal(turns.length,3)
  await receive([{type:'reply',data:{id:'77'}},{type:'text',data:{text:'跨群引用'}}])
  assert.equal(turns.length,3)
  assert.doesNotMatch(prompt,/OTHER-GROUP-SECRET/)
  assert.match(prompt,/"mode":"ambient"/)
  await receive([{type:'at',data:{qq:'99'}},{type:'text',data:{text:'大家都 @ 你起哄！'}}])
  assert.equal(turns.length,3,'deliberate PASS on direct mentions')
  assert.match(prompt,/"mode":"addressed"/)
  await receive([{type:'text',data:{text:'岚舟，又喊你凑热闹'}}])
  assert.equal(turns.length,3)
  await receive([{type:'reply',data:{id:'42'}},{type:'text',data:{text:'刷屏'}}])
  assert.equal(turns.length,3)
  assert.match(prompt,/"quote":\{"sender":"99"/)
  ctx.config.requireMention=true
  await receive([{type:'text',data:{text:'能接话吗'}}])
  assert.equal(turns.length,3)
  answer='invalid model output'
  await receive('[CQ:at,qq=99]具体为什么？')
  assert.equal(turns.length,4)
  assert.match(turns.at(-1)[1],/昨天投票选的是火星/)
  await receive([{type:'at',data:{qq:'99'}}],'13')
  assert.equal(turns.length,4)
  assert.equal(recorded.length,10)
  let commands=0
  ctx.tryHandleCommand=async()=>{commands++;return true}
  ctx.isSilenced=()=>true
  const lastPrompt=prompt
  await receive([{type:'at',data:{qq:'99'}},{type:'text',data:{text:'/silent off'}}])
  assert.equal(commands,1)
  assert.equal(prompt,lastPrompt,'explicit commands bypass persona gate')
})

test('a stalled model may silence ordinary chatter but cannot indefinitely hold a direct reply', async()=>{
  const groups=new GroupParticipation(async()=>new Promise(()=>{}),{directTimeoutMs:4})
  try { assert.ok((await groups.consider(inbound(5,'我想问你'), 'direct')).direct) }
  finally {groups.stop()}
})

test('an addressed burst uses one judge and preserves several independent requests', async()=>{
  let calls=0
  const groups=new GroupParticipation(async(_history,_signal,candidates,_id,mode)=>{
    assert.equal(mode,'addressed')
    assert.equal(candidates.length,3)
    calls++
    return [0,2]
  },{batchMs:2})
  try {
    const results=await Promise.all(['求助一','随口喊你','求助二'].map((text,i)=>groups.consider({...inbound(i,text),userId:String(12+i)},'direct')))
    assert.equal(calls,1)
    assert.deepEqual(results.map(Boolean),[true,false,true])
  } finally {groups.stop()}
})
