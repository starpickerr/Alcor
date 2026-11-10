import test from 'node:test'
import assert from 'node:assert/strict'
import {guardSource} from '../../packages/alcor-plugin-info/src/host-policy.js'
import {sessionMode} from '../../packages/alcor-plugin-info/src/assignments.js'
import {readBriefingSourcePage} from '../../packages/alcor-organizer/src/briefing-sources.js'

test('an existing session loses source execution immediately and regains its catalogue at the next step',()=>{
  let value='both', guard
  const events={},denied=new Set()
  const agent={session:{id:'workbench',header:{agentPreset:'standard'}},ctx:{tools:{restrict:({deny})=>{
    deny.forEach(n=>denied.add(n));return ()=>deny.forEach(n=>denied.delete(n))
  }}}}
  const name='mcp__email__fetch_messages'
  const ctx={get:()=>undefined,on:(n,fn)=>events[n]=fn,effect(){},tools:{guard:fn=>guard=fn,definitions:()=>[{name},{name:'other'}]}}
  guardSource(ctx,{productScope:{get:()=>value}},'mcp__email__')
  const step=()=>events['agent/pre-step']({agent},()=>{})
  step();assert.equal(guard({name,agent}),undefined)
  value='alcor';assert.ok(guard({name,agent}));step();assert.deepEqual([...denied],[name])
  value='dsh';step();assert.equal(denied.size,0);assert.equal(guard({name,agent}),undefined)
  assert.equal(sessionMode(ctx,{session:{id:'workbench-onebot-group-111',header:{}}}),'dsh')
  assert.equal(sessionMode(ctx,{session:{id:'onebot-group-111',header:{}}}),'alcor')
})

test('a scheduled Alcor collection cannot use a DSH-only source or retain a page revoked during collection',async()=>{
  let scope='dsh',calls=0
  const ctx={get:()=>({entries:()=>[{options:{id:'alcor-mail',config:{productScope:scope}}}]}),
    tools:{execute:async()=>{calls++;scope='dsh';return {results:[]}}}}
  const read=()=>readBriefingSourcePage(ctx,{source:'email',tool:'mcp__email__fetch_messages',method:'fetch_messages',arguments:{}},{})
  await assert.rejects(read,/没有分配/);assert.equal(calls,0)
  scope='both';await assert.rejects(read,/分配已变化/);assert.equal(calls,1)
})
