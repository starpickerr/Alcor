import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'esbuild'

test('briefing source selection rechecks local settings, external scopes and account availability', async t => {
  const output = await mkdtemp(join(process.cwd(), '.alcor-briefing-build-'))
  t.after(() => rm(output, { recursive: true, force: true }))
  const file = join(output, 'organizer.mjs')
  await build({ entryPoints: ['packages/alcor-organizer/src/index.ts'], outfile: file, bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24', logLevel: 'silent' })
  const Organizer = (await import(pathToFileURL(file).href)).default
  const organizer = Object.create(Organizer.prototype), calls = []
  let groups = { alcor_tasks: true, alcor_bills: false }
  const preferences = [{source:'elearning',accountId:'school'}, ...[1,2,3].map(id=>({source:'email',accountId:`mail-${id}`}))]
  organizer.active = true
  organizer.ctx = { get: () => assert.fail('local selection must not discover external services'), tools: { definitions: () => assert.fail('local selection must not inspect external tools') } }
  organizer.businessWorker = { call: async action => { calls.push(action); if (action === 'source_settings') return preferences; if (action === 'source_group_settings') return groups; assert.fail(`unexpected worker action: ${action}`) } }
  const sources = await organizer.localBriefingSources()
  assert.deepEqual(sources.map(row => row.source), ['alcor_tasks'])
  const authorize = organizer.briefingAuthorization(sources)
  await authorize(); await authorize()
  assert.deepEqual(calls, ['source_settings','source_group_settings','source_settings','source_group_settings','source_settings','source_group_settings'])
  groups = { alcor_tasks: false, alcor_bills: false }
  await assert.rejects(authorize(), /来源已关闭/)
  assert.deepEqual(await organizer.localBriefingSources(), [])

  const school = { source:'elearning', accountId:'school', tool:'mcp__elearning__fetch', method:'fetch', arguments:{} }
  preferences[0] = {...school, arguments:{course_ids:['selected']}}
  groups = {alcor_tasks:false, alcor_bills:false, email:true, elearning:true}
  let connected = true, started
  organizer.ctx = {
    get: key => key === 'alcorElearning' && connected ? {briefingSource:async()=>school, state:async()=>({configured:true,connected:true})} : undefined,
    tools: {
      definitions: () => connected ? ['mcp__elearning__fetch','mcp__email__list_sources','mcp__email__fetch_messages'].map(name=>({name})) : [],
      execute: async ({calls:[call]}) => {
        assert.equal(call.name,'mcp__email__list_sources', 'Discovery must read only metadata')
        return {results:[{result:{isError:false,value:[1,2,3].map(id=>({source:'email',account_id:`mail-${id}`,mailbox_id:`mailbox-${id}`}))}}]}
      },
    },
  }
  organizer.briefingGenerator = {
    start: async input => { started=input; await input.authorize() },
    wait: async id => ({id,body:'Saved external briefing'}),
  }
  const result = await organizer.generateBriefing({date:'2026-11-09',notes:'',requestId:'12345678-1234-5123-8123-123456789abc',trigger:'scheduled'})
  assert.equal(result.body,'Saved external briefing')
  assert.deepEqual(started.sources.map(row=>row.source),['elearning','email','email','email'])
  assert.deepEqual(started.sources[0].arguments,{course_ids:['selected']})
  assert.deepEqual(started.sources.slice(1).map(row=>row.arguments.account_id),['mailbox-1','mailbox-2','mailbox-3'])
  assert.equal(started.trigger,'scheduled')
  groups.email=false
  await assert.rejects(started.authorize(),/来源已关闭/)
  groups.email=true
  await started.authorize()
  preferences[0] = {...school,arguments:{course_ids:['different']}}
  await assert.rejects(started.authorize(),/范围已变化/)
  preferences[0] = {...school,arguments:{course_ids:['selected']}}
  connected=false
  await assert.rejects(started.authorize(),/账号或范围已变化/)
})
