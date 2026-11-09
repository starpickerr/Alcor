import test from 'node:test'
import assert from 'node:assert/strict'
import { Context } from '@deepseek-ai/cordis'
import { bindScopeParent, createScope, scopeTarget } from '@deepseek-ai/dsh-scope'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Tools from '@deepseek-ai/dsh-tools'
import * as Native from '@deepseek-ai/dsh-tool-executor-native'
import * as Ptc from '@deepseek-ai/dsh-tool-executor-ptc'
import { tmpdir } from 'node:os'
import { apply } from '../../packages/alcor-secretary/src/tools.js'

for (const mode of ['native','ptc']) test(`${mode}: generic business requires a current main user turn`, async t => {
  const ctx=new Context();t.after(()=>ctx.fiber.dispose());let turn=1, name, input, forwarded=0, reviewCount=0, briefingInput
  ctx.provide('agents',{turnBoundaryOf:()=>({openTurnStartSeq:turn,lastTurn:turn})})
  await ctx.plugin(SystemPrompt,{});await ctx.plugin(Tools)
  if(mode==='ptc'){ctx.provide('ptcRuntime',{language:'typescript',programFormat:'function-body',isolation:'synthetic',executionInstructions:'',resolve:x=>x,run:async request=>{try{return {logs:[],value:await request.bindings[0].functions[name](input)}}catch(e){return {logs:[],error:{kind:'exception',message:e.message}}}},release:async()=>{}});ctx.provide('workingDirectory',{ensure:async()=>tmpdir()});await ctx.plugin(Ptc)}else await ctx.plugin(Native)
  ctx.provide('alcorSecretary',{});ctx.provide('alcorOrganizer',{business:async()=>{forwarded++;return []},localBriefingSources:async()=>[{source:'alcor_tasks',enabled:true},{source:'calendar_agenda',enabled:true}],taskReview:async()=>{reviewCount++;return {}},generateBriefing:async request=>{briefingInput=request;return {id:'synthetic',date:'2026-01-01',body:''}}})
  const preset={},agent={ctx,session:{id:'synthetic',header:{},append:()=>1}};bindScopeParent(agent,preset)
  await ctx.plugin(Object.assign(inner=>apply(createScope(inner,preset).ctx),{inject:['tools','systemPrompt','alcorSecretary','alcorOrganizer']}))
  const step=kind=>ctx.waterfall(scopeTarget(agent,agent),'agent/pre-step',{agent,messages:kind?[{source:{kind}}]:[]},async()=>{})
  const call=async(tool,args)=>{name=tool;input=args;const block=mode==='ptc'?{type:'tool-call',id:'synthetic',name:'run_code',arguments:JSON.stringify({code:'synthetic',description:'Test source'})}:{type:'tool-call',id:'synthetic',name:tool,arguments:JSON.stringify(args)};return (await ctx.tools.execute({agent,signal:AbortSignal.timeout(5000),calls:[block]})).results[0].result}
  const cases=[{domain:'tasks',method:'list_todos'},{domain:'calendar',method:'list_events'},{domain:'bills',method:'list_statements'},{domain:'briefing',method:'list_briefings'},{domain:'apple',method:'status'},{domain:'automation',method:'catalog'}]
  await step('schedule');for(const args of cases)assert.equal((await call('alcor_business',args)).isError,true)
  assert.equal(forwarded,0);assert.equal((await call('alcor_review_tasks',{})).isError,true);assert.equal((await call('alcor_generate_briefing',{})).isError,false)
  assert.equal(Object.hasOwn(briefingInput,'sources'),false);assert.equal(briefingInput.trigger,'scheduled');assert.equal(reviewCount,0);assert.equal(forwarded,0)
  turn++;await step('user');for(const args of cases)assert.equal((await call('alcor_business',args)).isError,false)
  assert.equal((await call('alcor_review_tasks',{})).isError,false);assert.equal(reviewCount,1)
  const before=forwarded;agent.session.header={origin:'subagent',parentSession:'parent'};for(const args of cases)assert.equal((await call('alcor_business',args)).isError,true)
  assert.equal((await call('alcor_review_tasks',{})).isError,true);assert.equal((await call('alcor_generate_briefing',{})).isError,true);assert.equal(reviewCount,1)
  agent.session.header={};turn++;await step(null);for(const args of cases)assert.equal((await call('alcor_business',args)).isError,true)
  assert.equal(forwarded,before)
})
