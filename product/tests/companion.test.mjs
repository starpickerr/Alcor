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
