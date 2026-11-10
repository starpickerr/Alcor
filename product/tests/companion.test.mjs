import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp,rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {createScopedMemory} from '../../packages/alcor-secretary/src/memory/scoped.js'
import {currentMood} from '../../packages/alcor-secretary/src/memory/life.js'

async function fixture(t, answer, audience='personal'){
  const dataDir=await mkdtemp(join(tmpdir(),'alcor-life-'))
  t.after(()=>rm(dataDir,{recursive:true,force:true}))
  const calls=[]
  const llm={async *stream(request){
    const prompt=request.messages[0].content[0].text
    const input=JSON.parse(prompt.split('以下是对话资料：\n').at(-1))
    calls.push(input)
    yield {type:'text-delta',text:JSON.stringify(await answer(input))}
  }}
  const open=()=>createScopedMemory({dataDir,audience,llm,getDefaultModel:()=>({provider:'test',model:'test'})})
  let memory=open()
  t.after(()=>memory.dispose())
  return {get m(){return memory},calls,async restart(){await memory.dispose();memory=open()}}
}
const actor=(subject,audience='personal')=>({subject,audience,owner:subject==='owner',source:{kind:'desktop',at:new Date().toISOString(),sessionId:'session-'+subject}})
test('mood ages; relationship quotes belong to verified speakers and survive restart, correction and forgetting',async t=>{
  const a=actor('qq:1','group:3'), b=actor('qq:2','group:3')
  const env=await fixture(t,input=>({mood:'高兴',explore:null,preference:{topic:'交流',quote:input.speech==='invent'?'not said':input.speech}}),'group:3')
  await env.m.observe('我喜欢安静',a,'evt1')
  await env.m.observe('我喜欢热闹',b,'evt2')
  await env.restart()
  assert.match(env.m.turnContext('你好',a),/高兴.*|高兴/s)
  assert.match(env.m.turnContext('你好',a),/我喜欢安静/)
  assert.doesNotMatch(env.m.turnContext('你好',a),/我喜欢热闹/)
  await env.m.observe('我现在喜欢热闹',a,'evt3')
  assert.doesNotMatch(env.m.turnContext('你好',a),/我喜欢安静/)
  await env.m.observe('invent',b,'evt4')
  assert.ok(!env.m.records().some(row=>row.content==='not said'))
  const pref=env.m.records().find(row=>row.subject===a.subject)
  await env.m.change({id:pref.id},a)
  assert.doesNotMatch(env.m.turnContext('你好',a),/我现在喜欢热闹/)
  assert.match(currentMood({value:'高兴',at:0},2*3600000),/淡/)
  assert.equal(currentMood({value:'高兴',at:0},6*3600000),'')
})
test('two independent local searches with different evidence form an assistant interest; recommendations alone do not',async t=>{
  const me=actor('owner')
  const env=await fixture(t,input=>({mood:null,preference:null,explore:input.speech==='听我推荐' ? null:'音乐'}))
  const first=await env.m.remember({content:'音乐：第一份资料谈钢琴'},me)
  await env.m.remember({content:'音乐：第二份资料谈作曲'},me)
  await env.m.observe('听我推荐',me,'recommend')
  assert.ok(!env.m.turnContext('你好',me).includes('兴趣'))
  await env.m.observe('上次的话题还有什么有意思的？',me,'choice1')
  await env.m.observe('同一回合重试',me,'choice1')
  assert.ok(!env.m.records().some(row=>row.kind==='interest'))
  await env.m.observe('今天想聊点什么？',me,'choice2')
  await env.restart()
  const interest=env.m.records().find(row=>row.kind==='interest')
  assert.equal(new Set(interest.evidence).size,2)
  assert.match(env.m.turnContext('你好',me),/小熊自己的兴趣：音乐/)
  await env.m.change({id:first.id},me)
  assert.ok(!env.m.records().some(row=>row.kind==='interest'))
})
test('a new utterance invalidates a pending or queued decision; revised, cancelled and deferred outcomes persist',async t=>{
  const who=actor('owner')
  let answer,started,release
  const env=await fixture(t,async()=>{if(started){started();await new Promise(r=>release=r)}return answer})
  const item=await env.m.remember({content:'实验'},who)
  await env.m.allowInitiative(true)
  const plan=()=>env.m.planFollowup({content:'好了没有？',evidence:item.id,dueAt:new Date(Date.now()+30000).toISOString()})
  const note=await plan()
  const wait=new Promise(r=>started=r)
  answer={action:'send',text:'旧问法'}
  const pending=env.m.reconsider(note,()=>true)
  await wait
  env.m.noteIncoming(who,'有新进展')
  release();started=null
  assert.equal(await pending,null)
  const current=()=>env.m.pendingFollowups()[0]
  answer={action:'send',text:'想聊聊新结果吗？'}
  const changed=await env.m.reconsider(current(),()=>true)
  assert.equal(changed.content,answer.text)
  assert.ok(env.m.validReview(note.id,changed.stamp))
  env.m.noteIncoming(who,'稍后再说')
  assert.equal(env.m.validReview(note.id,changed.stamp),false)
  answer={action:'defer'}
  assert.equal(await env.m.reconsider(current(),()=>true),null)
  await env.restart()
  assert.equal(current().review.action,'defer')
  env.m.noteIncoming(who,'实验跑通了，不必再问')
  answer={action:'cancel'}
  await env.m.reconsider(current(),()=>true)
  assert.equal(current(),undefined)
})
test('passive group messages persist once across restart without waking an agent',async t=>{
 const who=actor('qq:8','group:3')
 const env=await fixture(t,()=>assert.fail('Ambient capture called a model'),'group:3')
 env.m.captureAmbient('platform1','Group remark',who)
 await env.restart()
 env.m.captureAmbient('platform1','Group remark',who)
 const rows=env.m.records().filter(row=>row.content.includes('Group remark'))
 assert.equal(rows.length,1)
 assert.ok(rows[0].content.includes('qq:8'))
 assert.equal(env.calls.length,0)
})

test('a shared future event can prompt a later check-in without a reminder command',async t=>{
  const who = actor('owner'), speech = '我下午要答辩，有点紧张。'
  const answer = {mood:null, preference:null, explore:null, followup:{quote:'下午要答辩',afterMinutes:120,text:'今天答辩怎么样？'}}
  const env = await fixture(t, () => answer)
  await env.m.observe(speech, who, 'off')
  assert.equal(env.m.pendingFollowups().length, 0)
  await env.m.allowInitiative(true)
  await env.m.observe('今天天气不错', who, 'unrelated')
  assert.equal(env.m.pendingFollowups().length, 0)
  await env.m.observe(speech, who, 'event')
  await env.restart()
  await env.m.observe(speech, who, 'event')
  const notes = env.m.pendingFollowups()
  assert.equal(notes.length, 1)
  assert.equal(notes[0].content, '今天答辩怎么样？')
  assert.equal(env.m.records().find(row=>row.id===notes[0].evidence).content, '下午要答辩')
  await env.m.change({id:notes[0].evidence}, who)
  assert.equal(env.m.pendingFollowups().length, 0)
})

test('edited persona examples persist with the public identity and stay out of episodic memory', async t => {
  const env=await fixture(t,()=>({style:'对熟人的问候给出亲切回应'}))
  const before=env.m.identity()
  assert.equal(before.examples,undefined)
  const examples=[{user:'虚构的朋友问候',assistant:'见到你了……真好...'}]
  await env.m.editIdentity({soul:before.soul,examples,expectedRevision:before.revision})
  const updated=env.m.identity()
  assert.equal(updated.id,before.id)
  assert.notEqual(updated.revision,before.revision)
  await env.restart()
  assert.deepEqual(env.m.identity(),updated)
  await env.m.editIdentity({name:'小满',soul:before.soul,expectedRevision:updated.revision})
  assert.deepEqual(env.m.identity().examples,examples)
  const {describePersona}=await import('../../packages/alcor-secretary/src/persona.js')
  assert.doesNotMatch(describePersona(env.m.identity()),/虚构的朋友问候|很高兴见到你/)
  assert.deepEqual(env.m.recall('虚构的朋友问候'),[])
  env.m.exampleStyle()
  await new Promise(resolve=>setImmediate(resolve))
  assert.equal(env.m.exampleStyle(),'对熟人的问候给出亲切回应')
  await env.restart()
  assert.equal(env.m.exampleStyle(),'对熟人的问候给出亲切回应')
  assert.equal(env.calls.filter(input=>input.examples).length,1)
})

function idleClock(t) {
  const start = new Date(2026,10,8,12).getTime()
  t.mock.timers.enable({apis:['Date'],now:start})
  return {start,advance:ms=>t.mock.timers.setTime(Date.now()+ms)}
}
const hour=3600000
function idleActor(sessionId='onebot-private-8') {
  return {subject:'owner',owner:true,audience:'personal',source:{kind:'qq-private',sessionId}}
}
test('idle greeting reflects timing preferences across restarts, backs off after unanswered sends and records uncertainty', async t=>{
  const {start,advance}=idleClock(t)
  const actor=idleActor()
  let answer={action:'wait',afterHours:48}
  const env=await fixture(t,()=>answer)
  const options={allowed:()=>true,grant:'private-grant',sessionId:actor.source.sessionId,destination:'channel'}
  const consider=()=>env.m.considerIdle(options)
  await env.m.allowInitiative(true)
  assert.equal(await consider(),null)
  env.m.noteIncoming(actor,'Last conversation finished here.', start-6*hour)
  assert.equal(await consider(),null)
  assert.equal(env.calls.length,1)
  await env.restart()
  advance(24*hour)
  assert.equal(await consider(),null);assert.equal(env.calls.length,1)
  answer={action:'send',text:'嗨，今天过得怎么样？'}
  advance(24*hour)
  let note=await consider()
  assert.equal(note.kind,'idle');assert.equal(note.evidence,undefined)
  assert.equal(note.idleGrant,'private-grant')
  await env.m.recordIdleOutcome(note.id,'sent')
  await env.restart()
  assert.equal(await consider(),null)
  advance(24*hour)
  note=await consider()
  assert.equal(env.calls.at(-1).unansweredGreetings,1)
  await env.m.recordIdleOutcome(note.id,'sent')
  advance(48*hour);assert.equal(await consider(),null)
  advance(24*hour)
  note=await consider()
  assert.equal(env.calls.at(-1).unansweredGreetings,2)
  await env.m.recordIdleOutcome(note.id,'unknown')
  await env.restart()
  advance(96*hour)
  env.m.recordActivity(actor.source.sessionId,'Alcor','另一条回复',Date.now()-2*hour)
  assert.equal(await consider(),null)
  env.m.noteIncoming(actor,'new human message',Date.now())
  advance(24*hour)
  assert.equal((await consider()).kind,'idle')
})

test('new real QQ or owner desktop speech, revocation and persona changes invalidate stale greetings', async t=>{
  const {start,advance}=idleClock(t)
  const actor=idleActor(), opts={sessionId:actor.source.sessionId,destination:'channel',grant:'first',allowed:()=>granted}
  let granted=true, answer={action:'wait',afterHours:480}, began,release
  const env=await fixture(t,async()=>{if(began){began();await new Promise(r=>release=r)}return answer})
  await env.m.allowInitiative(true)
  const run=()=>env.m.considerIdle(opts)
  env.m.noteIncoming(actor,'昨天聊的是电影',start-3*hour)
  await run();assert.equal(env.calls.length,1)
  await env.m.setSoul('喜欢经常主动找主人说话。')
  answer={action:'send',text:'昨天的电影，还有哪段你喜欢？'}
  const note=await run();assert.ok(note);assert.equal(env.m.validReview(note.id,0),true)
  await env.m.setSoul('谨慎并尊重界限。')
  assert.equal(env.m.validReview(note.id,0),false)
  await run()
  env.m.noteIncoming({...actor,source:{kind:'desktop',sessionId:'desk'}},'最近请不要主动联系我。',Date.now())
  advance(24*hour)
  answer={action:'skip',reason:'boundary'}
  assert.equal(await run(),null)
  assert.match(env.calls.at(-1).lastMessage,/不要主动/)
  assert.equal(env.calls.at(-1).lastInteractionSource,'desktop')
  await env.m.setSoul('新的热情人格')
  const checked=env.calls.length
  assert.equal(await run(),null);assert.equal(env.calls.length,checked)
  env.m.noteIncoming(actor,'现在可以聊了',Date.now())
  advance(24*hour)
  answer={action:'send',text:'你好。'}
  const started=new Promise(r=>began=r), pending=run()
  await started
  granted=false
  release()
  assert.equal(await pending,null);assert.equal(env.m.pendingFollowups().length,0)
})

test('a new message arriving during an idle decision supersedes it before any outbound note exists',async t=>{
  const {start}=idleClock(t), actor=idleActor()
  let begin,release
  const env=await fixture(t,async()=>{begin();await new Promise(r=>release=r);return {action:'send',text:'过时的问候'}})
  await env.m.allowInitiative(true)
  env.m.noteIncoming(actor,'已完成的话题',start-2*hour)
  const started=new Promise(r=>begin=r)
  const pending=env.m.considerIdle({allowed:()=>true,grant:'grant',destination:'channel',sessionId:actor.source.sessionId})
  await started
  env.m.noteIncoming(actor,'刚在聊天',Date.now())
  release()
  assert.equal(await pending,null)
  assert.deepEqual(env.m.pendingFollowups(),[])
})

test('disabling with a reserved greeting clears the reservation and re-enabling waits for human speech',async t=>{
  const {start,advance}=idleClock(t), actor=idleActor()
  const env=await fixture(t,()=>({action:'send',text:'嗨，好久不见。'}))
  const consider=()=>env.m.considerIdle({sessionId:actor.source.sessionId,grant:'g',destination:'channel',allowed:()=>true})
  await env.m.allowInitiative(true)
  env.m.noteIncoming(actor,'上一次聊天',start-24*hour)
  const queued=await consider()
  assert.equal(env.m.pendingIdleId,queued.id)
  await env.m.allowInitiative(false)
  assert.equal(env.m.pendingIdleId,undefined);assert.equal(env.m.pendingFollowups().length,0)
  await env.m.allowInitiative(true);await env.restart()
  advance(24*hour);assert.equal(await consider(),null)
  env.m.recordActivity(actor.source.sessionId,'Alcor','另一条普通回复',Date.now())
  advance(24*hour);assert.equal(await consider(),null)
  env.m.noteIncoming(actor,'今天又碰到了',Date.now());advance(24*hour)
  assert.ok(await consider())
})

test('an ordinary assistant reply retains the last human request for quiet from desktop',async t=>{
  const {start,advance}=idleClock(t), actor=idleActor()
  const env=await fixture(t,()=>({action:'skip',reason:'boundary'}))
  const consider=()=>env.m.considerIdle({sessionId:actor.source.sessionId,grant:'g',destination:'channel',allowed:()=>true})
  await env.m.allowInitiative(true)
  env.m.noteIncoming(actor,'你好',start-2*hour)
  env.m.noteIncoming({...actor,source:{kind:'desktop',sessionId:'desk'}},'这周请不要主动发消息',start-hour)
  env.m.recordActivity(actor.source.sessionId,'Alcor','知道了',start)
  advance(24*hour)
  assert.equal(await consider(),null)
  assert.equal(env.calls.at(-1).lastHuman.message,'这周请不要主动发消息')
  assert.equal(env.calls.at(-1).lastHuman.desktop,true)
})
