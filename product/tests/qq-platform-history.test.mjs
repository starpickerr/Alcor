import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {adaptQQ} from '../../scripts/product/qq-alcor-adapter.mjs'
import {platformHistory} from '../../scripts/product/qq-platform-history.mjs'
import {ChannelRegistry} from '../../packages/alcor-secretary/src/channels/registry.js'
import {execFileSync} from 'node:child_process'
const archive=process.env.ALCOR_QQ_SOURCE

test('all platform group messages, including skipped chatter and actual bot sends, use stable paginated sequence numbers',()=>{
 const raw=(seq,id,text)=>({message_seq:[0,800,-100,300][seq],real_seq:String(seq),user_id:id,time:10+seq,sender:{nickname:'Member'},message:[{type:'text',data:{text}}]})
 const rows=[raw(1,'1','quiet chatter'),raw(2,'2','new question'),raw(3,'9','Actual reply')]
 assert.deepEqual(platformHistory({messages:rows},undefined,'9').map(r=>r.text),['quiet chatter','new question','Actual reply'])
 assert.equal(platformHistory({messages:rows.slice(0,2)},-100,'9').length,1)
 assert.equal(platformHistory({messages:rows},undefined,'9')[0].cursor,800)
 assert.equal(platformHistory({messages:rows},undefined,'9')[2].role,'Alcor')
})
test('platform transcript cache survives offline without calling the agent',async()=>{
 const table=()=>{const m=new Map();return {get:k=>m.get(k),entries:()=>m.entries(),put:async(k,v)=>m.set(k,v)}}
 const routes=table(),cache=table(),r=new ChannelRegistry(routes,()=>['group'],cache)
 let offline=false
 const stop=r.register({id:'test',account:'one',label:'Test',resolve:()=>({sessionId:'group',kind:'group',peer:'1',audience:'channel:test:one:1',owner:false,label:'Group'}),messages:()=>[],history:async()=>{
  if(offline)throw Error('offline')
  return {messages:[{seq:10,cursor:-99,time:1000,role:'QQ 1',text:'Skipped'}],hasMore:false,beforeSeq:-99}
 }})
 await r.catalog();assert.equal((await r.platformHistory('group')).messages[0].text,'Skipped')
 offline=true;assert.equal((await r.platformHistory('group')).messages[0].text,'Skipped')
 assert.equal((await r.platformHistory('group',-99)).messages.length,0)
 stop();assert.equal((await r.platformHistory('group')).messages[0].text,'Skipped')
})
test('group judgment selects a supported non-reasoning budget before streaming', {skip:!archive},async()=>{
 const original=execFileSync('tar',['-xOf',archive,'package/lib/group-participation.js'],{encoding:'utf8'})
 const adapted=adaptQQ('lib/group-participation.js',original)
 const {shouldJoinGroup}=await import('data:text/javascript;base64,'+Buffer.from(adapted).toString('base64'))
 let configured
 const ctx={llm:{prepareCall:async(config,signal,configure)=>{
  configured=configure(config,{reasoning:{efforts:[{id:'off'},{id:'high'}]}})
  return {config:configured,async *stream(){yield {type:'text-delta',text:'1'}}}
 }}}
 assert.equal(await shouldJoinGroup(ctx,{provider:'test',model:'reasoning'},[{speaker:'A',text:'Open question'}],new AbortController().signal),true)
 assert.equal(configured.reasoningEffort,'off');assert.ok(configured.maxTokens>256)
 const inbound=adaptQQ('lib/inbound.js',execFileSync('tar',['-xOf',archive,'package/lib/inbound.js'],{encoding:'utf8'}))
 assert.ok(inbound.indexOf('await this.ctx.recordGroup')<inbound.indexOf('const admission ='))
 assert.ok(inbound.includes('await admitGroup'))
})
