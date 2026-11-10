import assert from 'node:assert/strict'
import test from 'node:test'
import {registerSocial,qzoneRequest} from '../../scripts/product/qq-social.mjs'

test('publishing requests are explicit, preserve the audience and distinguish supplied copy from a topic',()=>{
  assert.deepEqual(qzoneRequest('发条说说：下雪了'),{text:'下雪了',compose:false,audience:'friends'})
  assert.deepEqual(qzoneRequest('代写并发布说说（仅自己可见）：雪'),{text:'雪',compose:true,audience:'self'})
  for(const input of ['别发条说说：雪','她说“发条说说：雪”','想写个草稿','发条说说（公开）：雪'])assert.equal(qzoneRequest(input),undefined)
})
test('queued posts recheck the originating turn; uncertain delivery cannot be repeated within that turn',async()=>{
  let run,turn=1,calls=0
  const actor={source:{kind:'qq-private',qqUserId:'11'},owner:true},agent={session:{id:'session'}},tools={}
  const rawSource={alcorQzone:qzoneRequest('发条说说：雪')}
  const bridge={chatForSession:()=>'private:11',silentUntil:new Map(),guardedConnectionCall:async()=>{calls++;throw Error('lost receipt')},
    deps:{ctx:{get:()=>({memory:{currentActor:()=>actor,turns:new Map([[agent,{rawSource}]])}})},connection:{connected:true,selfId:'22'},config:{botQQ:'22'},policy:{adminUsers:['11']}},
    outbound:{enqueue:(_,fn)=>{run=fn;return Promise.resolve()},writeGateRejectReason:()=>null,auditWrite(){}}}
  registerSocial({agents:{turnBoundaryOf:()=>({openTurnStartSeq:turn,lastTurn:turn})},tools:{register:t=>tools[t.name]=t}},bridge,'private:11')
  await tools.qq_write_qzone.execute({text:'雪'},{agent});turn++
  await assert.rejects(run(),/回合已结束/)
  assert.equal(calls,0)
  await tools.qq_write_qzone.execute({text:'雪'},{agent})
  await assert.rejects(run(),/未确认/)
  await tools.qq_write_qzone.execute({text:'雪'},{agent})
  await assert.rejects(run(),/已尝试/)
  assert.equal(calls,1)
})
