import test from 'node:test'
import assert from 'node:assert/strict'
import { Context } from '@deepseek-ai/cordis'
import SkillRegistry from '@deepseek-ai/dsh-skill'
import * as ToolSkill from '@deepseek-ai/dsh-tool-skill'
import Tools from '@deepseek-ai/dsh-tools'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import * as NativeExecutor from '@deepseek-ai/dsh-tool-executor-native'
import { createScope, bindScopeParent } from '@deepseek-ai/dsh-scope'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { registerBundledSkills } from '../../packages/stella-organizer/src/skills.js'
import { BusinessWorker } from '../../packages/stella-organizer/src/business.js'
import * as StellaTools from '../../packages/stella-secretary/src/tools.js'

const root = new URL('../../packages/', import.meta.url)
const localNames = ['stella-automation','stella-bills','stella-briefing','stella-calendar','stella-collaboration','stella-tasks']

test('bundled skills load through the native skill tool, remain scoped and leave no registrations after disable', async t => {
  const ctx = new Context()
  t.after(()=>ctx.fiber.dispose())
  await ctx.plugin(SkillRegistry)
  await ctx.plugin(SystemPrompt,{})
  await ctx.plugin(Tools)
  await ctx.plugin(NativeExecutor)
  ctx.provide('agents',{})
  ctx.provide('workingDirectory',{ensure:async()=>tmpdir()})
  await ctx.plugin(ToolSkill)
  ctx.skills.register({name:'user-owned',description:'Existing user skill',source:'custom',content:'Keep me'})
  const preset={}, agent={session:{header:{}}},other={session:{header:{}}}
  bindScopeParent(agent,preset)
  let scope
  const bundle=ctx.plugin(inner=>{
    scope=createScope(inner,preset)
    registerBundledSkills(scope.ctx,'stella-secretary',new URL('stella-secretary/skills/',root))
  })
  await bundle.await()
  const list = scope=>ctx.skills.list({scope,cwd:tmpdir()})
  assert.deepEqual((await list(agent)).map(s=>s.name).filter(n=>n.startsWith('stella-')).sort(),localNames)
  assert.deepEqual((await list(other)).map(s=>s.name),['user-owned'])
  assert((await list(agent)).every(s=>!('content' in s)),'Discovery must not inline all skill instructions')
  const loaded=await ctx.tools.execute({agent,signal:AbortSignal.timeout(5000),calls:[{type:'tool-call',id:'read-guide',name:'skill',arguments:JSON.stringify({name:'stella-bills'})}]})
  assert.equal(loaded.results[0].result.isError,false)
  assert(loaded.results[0].result.value.content.includes('amount_minor'))
  await bundle.dispose()
  assert.deepEqual((await list(agent)).map(s=>s.name),['user-owned'])
  const absent=await ctx.tools.execute({agent,signal:AbortSignal.timeout(5000),calls:[{type:'tool-call',id:'read-disabled-guide',name:'skill',arguments:JSON.stringify({name:'stella-bills'})}]})
  assert.equal(absent.results[0].result.isError,true)
})

test('guide examples execute with the actual business wrapper and single-method discovery stays bounded', async t => {
  const directory=await mkdtemp(join(tmpdir(),'stella-skill-examples-'))
  const worker=new BusinessWorker(directory,{worker:new URL('../../packages/stella-secretary/business/worker.py',import.meta.url)})
  const ctx=new Context()
  t.after(async()=>{await ctx.fiber.dispose();await worker.close();await rm(directory,{recursive:true,force:true})})
  await ctx.plugin(SkillRegistry);await ctx.plugin(SystemPrompt,{});await ctx.plugin(Tools);await ctx.plugin(NativeExecutor)
  ctx.provide('stellaSecretary',{})
  ctx.provide('stellaOrganizer',{business:input=>worker.call('invoke',{domain:input.domain,method:input.method,arguments:input.arguments,request_id:input.requestId})})
  await ctx.plugin(StellaTools)
  registerBundledSkills(ctx,'skill-examples',new URL('stella-secretary/skills/',root))
  const invoke=async(input,id)=>{
    const result=(await ctx.tools.execute({signal:AbortSignal.timeout(5000),calls:[{type:'tool-call',id,name:'stella_business',arguments:JSON.stringify(input)}]})).results[0].result
    assert.equal(result.isError,false,JSON.stringify(result));return result.value.result
  }
  for(const [name,method,check] of [
    ['stella-bills','create_ledger',row=>{assert.equal(row.amount_minor,1390);assert.equal(row.currency,'CNY')}],
    ['stella-tasks','create_todo',row=>{assert.equal(row.repeat_frequency,'daily');assert.equal(row.repeat_interval,1)}],
    ['stella-calendar','create_event',row=>{assert.equal(row.title,'项目例会');assert.equal(Date.parse(row.end_at)-Date.parse(row.start_at),3600000)}],
  ]){
    const skill=await ctx.skills.get(name,{cwd:tmpdir()})
    const examples=[...skill.content.matchAll(/```json\n([\s\S]*?)\n```/g)].map(match=>JSON.parse(match[1]))
    const example=examples.find(row=>row.method===method)
    assert(example,'Guide must include an executable example for '+method)
    check(await invoke(example,'skill-'+name))
    const catalog=await invoke({domain:example.domain,method:'catalog',arguments:{method}},'catalog-'+name)
    assert.deepEqual(catalog.map(row=>row.name),[method])
  }
  await assert.rejects(worker.call('invoke',{domain:'tasks',method:'catalog',arguments:{method:'invented_method'}}),/Unknown business operation/)
})

test('optional plugin skills are independently owned and removed without affecting other plugin guides',async t=>{
  const ctx=new Context();t.after(()=>ctx.fiber.dispose());await ctx.plugin(SkillRegistry)
  const bundles=[]
  for(const name of ['elearning','mail','apple-sync']) {
    const handle=ctx.plugin(inner=>registerBundledSkills(inner,'stella-'+name,new URL('stella-'+name+'/skills/',root)))
    await handle.await();bundles.push(handle)
  }
  assert.deepEqual((await ctx.skills.list({cwd:tmpdir()})).map(row=>row.name).sort(),['stella-apple-sync','stella-elearning','stella-mail'])
  await bundles[0].dispose()
  assert.equal(await ctx.skills.get('stella-elearning',{cwd:tmpdir()}),undefined)
  assert(await ctx.skills.get('stella-mail',{cwd:tmpdir()}))
})
