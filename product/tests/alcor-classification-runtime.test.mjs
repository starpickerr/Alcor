import test from 'node:test'
import assert from 'node:assert/strict'
import { Context } from '@deepseek-ai/cordis'
import { bindScopeParent, createScope } from '@deepseek-ai/dsh-scope'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Tools from '@deepseek-ai/dsh-tools'
import * as Native from '@deepseek-ai/dsh-tool-executor-native'
import * as Ptc from '@deepseek-ai/dsh-tool-executor-ptc'
import { tmpdir } from 'node:os'
import { apply } from '../../packages/alcor-secretary/src/tools.js'

for (const mode of ['native','ptc']) test(`${mode}: unclassified Alcor operations fail closed without affecting ordinary host tools`, async t => {
  const ctx = new Context(); t.after(() => ctx.fiber.dispose())
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); let name, input
  if (mode === 'ptc') {
    ctx.provide('ptcRuntime', { language:'typescript', programFormat:'function-body', isolation:'synthetic', executionInstructions:'', resolve:x=>x,
      run:async request=>{try{return {logs:[],value:await request.bindings[0].functions[name](input)}}catch(e){return {logs:[],error:{kind:'exception',message:e.message}}}},release:async()=>{} })
    ctx.provide('workingDirectory',{ensure:async()=>tmpdir()});await ctx.plugin(Ptc)
  } else await ctx.plugin(Native)
  let forwarded = 0, ordinary = 0, rogue = 0
  ctx.provide('alcorSecretary', {});ctx.provide('alcorOrganizer',{business:async()=>{forwarded++;return {ok:true}}})
  for (const [tool,run] of [['ordinary_host',()=>{ordinary++;return {}}],['alcor_unclassified',()=>{rogue++;return {}}]]) ctx.tools.register({name:tool,description:'Synthetic',parameters:{},output:{schema:{type:'object',additionalProperties:true},render:()=>[{type:'text',text:'{}'}]},execute:async()=>run()})
  const preset = {}, agent = {ctx,session:{id:'synthetic',header:{},append:()=>1}};bindScopeParent(agent,preset)
  await ctx.plugin(Object.assign(inner=>apply(createScope(inner,preset).ctx),{inject:['tools','systemPrompt','alcorSecretary','alcorOrganizer']}))
  const call=async(tool,args)=>{name=tool;input=args;const block=mode==='ptc'?{type:'tool-call',id:'synthetic',name:'run_code',arguments:JSON.stringify({code:'synthetic',description:'Classify'})}:{type:'tool-call',id:'synthetic',name:tool,arguments:JSON.stringify(args)};return (await ctx.tools.execute({agent,signal:AbortSignal.timeout(5000),calls:[block]})).results[0].result}
  assert.equal((await call('alcor_business',{domain:'tasks',method:'invented_operation'})).isError,true)
  assert.equal((await call('alcor_unclassified',{})).isError,true)
  assert.equal((await call('ordinary_host',{})).isError,false)
  assert.equal(forwarded,0);assert.equal(rogue,0);assert.equal(ordinary,1)
})
