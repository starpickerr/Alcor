import test from 'node:test'
import assert from 'node:assert/strict'
import {execFileSync} from 'node:child_process'
import {mkdtemp,readFile,writeFile,mkdir,symlink,rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {fileURLToPath,pathToFileURL} from 'node:url'
import {splitQQReply,replyDelay} from '../../scripts/product/qq-replies.mjs'
import {adaptQQ,adaptedFiles} from '../../scripts/product/qq-alcor-adapter.mjs'

test('QQ keeps a short reply together and sends paragraphs in one bubble',()=>{
  assert.deepEqual(splitQQReply('好呀，等你回来。'),['好呀，等你回来。'])
  assert.deepEqual(splitQQReply('考完了吗？\n\n我还惦记着呢。\n\n想听你讲讲。'),['考完了吗？'+'\n\n我还惦记着呢。\n\n想听你讲讲。'])
  assert.deepEqual(splitQQReply(' \n '),[])
})

test('QQ retains long prose without losing content or bursting into dozens of messages',()=>{
  const sentences=Array.from({length:20},(_,i)=>`第${i+1}件事，${'我想听听你今天的故事'.repeat(5)}。`)
  const parts=splitQQReply(sentences.join(''))
  assert.ok(parts.length===1)
  assert.equal(parts.join('').replace(/\s/g,''),sentences.join(''))
  for(const part of parts)assert.ok(part.endsWith('。'))
  assert.ok(splitQQReply(Array.from({length:15},(_,i)=>'话题'+i).join('\n\n')).length<=6)
  assert.deepEqual(splitQQReply('a|||b|||c|||d'),['a','b','c','d'])
  assert.deepEqual(splitQQReply('用 `a|||b`。|||然后呢？'),['用 `a|||b`。','然后呢？'])
  assert.equal(replyDelay('很短'),450)
  assert.equal(replyDelay('字'.repeat(1000)),1600)
})

test('QQ preserves code, lists, URLs and emoji without splitting a structured block',()=>{
  const code='```js\nconst first = 1;\n\nconsole.log(first);\n```'
  const list='1. 准备材料。\n2. 检查尺寸。'
  const url='https://example.org/'+ 'a'.repeat(250)+'?x=1.2'
  assert.deepEqual(splitQQReply('先看这段。\n\n'+code+'\n\n'+list),[['先看这段。',code,list].join('\n\n')])
  assert.deepEqual(splitQQReply(url),[url])
  assert.deepEqual(splitQQReply('👨‍👩‍👧‍👦'.repeat(50)),['👨‍👩‍👧‍👦'.repeat(50)])
})

test('actual QQ outbound sends paced bubbles in order, quotes once and retains its reconnect and failure behavior', {skip:!process.env.ALCOR_QQ_SOURCE},async t=>{
  const dir=await mkdtemp(join(tmpdir(),'alcor-qq-replies-'))
  t.after(()=>rm(dir,{recursive:true,force:true}))
  execFileSync('tar',['-xf',process.env.ALCOR_QQ_SOURCE,'-C',dir])
  await symlink(fileURLToPath(new URL('../../node_modules',import.meta.url)),join(dir,'node_modules'))
  const canvas=join(dir,'package/node_modules/@napi-rs/canvas')
  await mkdir(canvas,{recursive:true})
  await writeFile(join(canvas,'package.json'),JSON.stringify({type:'module',exports:'./index.js'}))
  await writeFile(join(canvas,'index.js'),"export function createCanvas(){throw Error('unexpected image operation')} export const loadImage=createCanvas; export const GlobalFonts={};")
  await writeFile(join(dir,'package/lib/alcor-space-diary.js'),await readFile(new URL('../../scripts/product/alcor-space-diary.js',import.meta.url)))
  await writeFile(join(dir,'package/lib/alcor-social.js'),await readFile(new URL('../../scripts/product/qq-social.mjs',import.meta.url)))
  for(const file of adaptedFiles){
    const path=join(dir,'package',file)
    await writeFile(path,adaptQQ(file,await readFile(path,'utf8')))
  }
  for(const [source,target] of [
    ['scripts/product/qq-replies.mjs','alcor-replies.js'],
    ['scripts/product/qq-conversation-info.mjs','alcor-conversation-info.js'],
    ['scripts/product/qq-platform-history.mjs','alcor-platform-history.js'],
    ['packages/alcor-secretary/src/qq-history.js','alcor-history.js'],
  ])await writeFile(join(dir,'package/lib',target),await readFile(new URL('../../'+source,import.meta.url)))
  const load=name=>import(pathToFileURL(join(dir,'package/lib',name)))
  const {OutboundPipeline}=await load('outbound.js')
  const {buildPlatformPrompt}=await load('prompt.js')
  assert.match(buildPlatformPrompt(false),/日常聊天/)
  const wire=[],delays=[]
  let online=true,fail=false
  const ctx={connected:()=>online,isStopping:()=>false,selfId:()=>'9',log(){},getChat:()=>undefined,
    config:{sensitivePatterns:[],textImageThreshold:0,actionRatePerMinute:0,actionRatePerDay:0,actionAuditEnabled:false},
    call:async(action,params)=>{if(fail && wire.length===1)throw Error('connection lost');wire.push({action,...params});return {message_id:wire.length}}}
  const pipeline=new OutboundPipeline(ctx,{pause:async ms=>delays.push(ms)})
  const ids=await pipeline.sendToChat('group:10','第一件事。\n|||\n第二件事。\n\n第三件事。',{replyTo:'42'})
  assert.deepEqual(ids,['1','2'])
  assert.deepEqual(wire.map(row=>row.message.filter(s=>s.type==='text').map(s=>s.data.text).join('')),['第一件事。','第二件事。\n第三件事。'])
  assert.equal(wire[0].message[0].type,'reply')
  assert.ok(wire.slice(1).every(row=>row.message.every(s=>s.type!=='reply')))
  assert.equal(delays.length,1)

  wire.length=0;online=false
  assert.deepEqual(await pipeline.sendToChat('private:12','重连第一条。\n|||\n重连第二条。',{queuable:true}),[])
  assert.equal(wire.length,0)
  online=true;pipeline.drainPendingSends();await pipeline.chainTail('private:12')
  assert.equal(wire.length,2)
  assert.equal(pipeline.pendingSends.size,0)
  wire.length=0;fail=true
  await assert.rejects(pipeline.sendToChat('group:10','已发送。\n|||\n发送失败。\n\n不应发送。',{queuable:true}),/connection lost/)
  assert.equal(wire.length,1)
  assert.equal(pipeline.pendingSends.size,0,'A partially sent reply must never queue the whole reply again')
  fail=false
  await pipeline.sendToChat('group:10','后续回复仍可发送。')
  assert.equal(wire.length,2)

  const {installConversationInfo}=await load('alcor-conversation-info.js')
  let adapter,authorized=true
  const bridge={registry:{bySession:new Map(),getSettings:()=>({})},outbound:pipeline,sendMsg:(...args)=>pipeline.sendMsg(...args)}
  installConversationInfo({alcorSecretary:{channels:{register:value=>{adapter=value;return ()=>{}}},productSpaces:{}}},
    {connected:true,selfId:'9'},bridge,{botQQ:'9'},{groupPolicy:'allowlist',groupAllowFrom:['10'],adminUsers:['12'],allowFrom:[]})
  wire.length=0
  assert.equal((await adapter.send({kind:'private',peer:'12'},'问候一。\n|||\n问候二。',()=>authorized)).status,'sent')
  assert.equal(wire.length,2)
  wire.length=0
  pipeline.pause=async()=>{authorized=false}
  assert.equal((await adapter.send({kind:'private',peer:'12'},'只发第一条。\n|||\n撤销后不发。',()=>authorized)).status,'unknown')
  assert.equal(wire.length,1)
  assert.equal((await adapter.send({kind:'private',peer:'12'},'不应发送。',()=>authorized)).status,'deferred')
  assert.equal(wire.length,1)
})
