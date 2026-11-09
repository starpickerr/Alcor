import test from 'node:test'
import assert from 'node:assert/strict'
import { discoverBriefingSources, readBriefingSourcePage, briefingSourceOptions, effectiveBriefingSources, localBriefingSources } from '../../packages/alcor-organizer/src/briefing-sources.js'
import { schemas } from '../../packages/alcor-organizer/src/remote.js'
import { BriefingGenerator } from '../../packages/alcor-organizer/src/briefing.js'

const source = { source: 'plugin:notes:inbox', accountId: 'account-1', label: 'Notes', tool: 'notes_briefing_fetch', method: 'briefing_fetch', arguments: { source_id: 'inbox' } }
const options = { since: '2026-11-04T00:00:00Z', until: '2026-11-05T00:00:00Z', offset: 0, cursors: {}, signal: new AbortController().signal }
const output = value => ({ results: [{ result: { isError: false, value } }] })

test('explicit local-only selection with only external preferences fails before worker or cursor changes', async () => {
  const preferences = [{source:'elearning',accountId:'school'}, ...[1,2,3].map(id=>({source:'email',accountId:`mail-${id}`}))]
  const groups = {stella_bills:false, alcor_bills:false}
  const sources = effectiveBriefingSources(preferences, localBriefingSources(), groups)
  assert.deepEqual(sources, [])
  const calls = []
  const generator = new BriefingGenerator({}, {call: async (...args) => {calls.push(args); throw new Error('unexpected worker call')}})
  await assert.rejects(generator.start({record:{date:'2026-11-07',notes:''},requestId:'synthetic',sources}), /请选择简报来源/)
  assert.deepEqual(calls, [])
})

test('installed sources remain discoverable before account setup without reading content or exposing credentials', async () => {
  const states = new Map([
    ['alcorElearning', { state: async () => ({ installed: true, configured: false, connected: false }), briefingSource: async () => null }],
    ['alcorMailConnection', { state: async () => ({ installed: true, configured: false, connected: false, accounts: [], password: 'must not expose' }) }],
  ])
  const ctx = { get: key => states.get(key), tools: { definitions: () => [], execute: async () => { assert.fail('Unconfigured sources must not execute tools') } } }
  const result = await discoverBriefingSources(ctx)
  assert.deepEqual(result.connections, [
    { package: '@alcor/elearning', label: 'eLearning', status: 'unconfigured' },
    { package: '@alcor/mail', label: '邮件', status: 'unconfigured' },
  ])
  assert.deepEqual(result.sources.map(row => row.source), ['calendar_agenda', 'alcor_tasks', 'alcor_bills'])
  assert.deepEqual(result.errors, [])
  states.set('alcorElearning', { state: async () => ({ installed: true, configured: true, connected: false }), briefingSource: async () => null })
  assert.equal((await discoverBriefingSources(ctx)).connections[0].status, 'disconnected')
  states.clear()
  assert.deepEqual((await discoverBriefingSources(ctx)).connections, [])
})

test('connected account discovery replaces setup state and reports missing enabled accounts', async () => {
  const school = { source: 'elearning', accountId: 'school', tool: 'mcp__elearning__fetch', method: 'fetch', arguments: {} }
  const ctx = { get: key => key === 'alcorElearning'
    ? { state: async () => ({ installed: true, configured: true, connected: true }), briefingSource: async () => school }
    : { state: async () => ({ installed: true, configured: true, connected: true }) }, tools: {
      definitions: () => [{ name: school.tool }], execute: async () => { assert.fail('No fetch during discovery') },
    } }
  const result = await discoverBriefingSources(ctx)
  assert.deepEqual(result.sources.at(-1), school)
  assert.equal(result.connections[0].status, 'ready')
  assert.equal(result.connections[1].status, 'unavailable')
})

test('native discovery calls only explicit metadata adapters, never fetch or unrelated tools', async () => {
  const calls = []
  const ctx = { get: () => undefined, tools: {
    definitions: () => ['notes_briefing_sources', 'notes_briefing_fetch', 'dangerous_run', 'unused_briefing_sources', 'broken_briefing_sources', 'broken_briefing_fetch'].map(name => ({ name })),
    execute: async ({ calls: [call] }) => { calls.push(call.name); if (call.name.startsWith('broken')) throw new Error('offline'); return output({ sources: [{ id: 'inbox', account_id: 'account-1', label: 'Notes', secret: 'must not expose metadata extras' }] }) },
  } }
  const result = await discoverBriefingSources(ctx)
  assert.deepEqual(calls, ['notes_briefing_sources', 'broken_briefing_sources'])
  assert.deepEqual(result.sources.at(-1), source)
  assert.deepEqual(schemas.briefingSources.parse([source]), [source])
  assert.equal(result.errors.length, 1)
  assert.throws(() => schemas.briefingSources.parse([{ ...source, tool: 'dangerous_run' }]))
  assert.throws(() => schemas.briefingSources.parse([{ ...source, arguments: { source_id: 'another' } }]))
})

test('native page conversion validates resource, account and timestamps before accepting content', async () => {
  const value = { source_id: 'inbox', account_id: 'account-1', items: [{ source_id: 'note-1', title: 'Note', occurred_at: options.since, snippet: 'Brief me' }], next_offset: null }
  const ctx = { tools: { execute: async ({ calls: [call] }) => {
    assert.equal(call.name, source.tool)
    assert.deepEqual(JSON.parse(call.arguments), { source_id: 'inbox', account_id: 'account-1', since: options.since, until: options.until, offset: 0, limit: 100 })
    return output(value)
  } } }
  assert.equal((await readBriefingSourcePage(ctx, source, options)).source, source.source)
  value.account_id = 'changed-account'
  await assert.rejects(readBriefingSourcePage(ctx, source, options), /不同账号/)
  value.account_id = source.accountId; value.source_id = 'private'
  await assert.rejects(readBriefingSourcePage(ctx, source, options), /范围/)
  value.source_id = 'inbox'; value.items[0].occurred_at = 'not a date'
  await assert.rejects(readBriefingSourcePage(ctx, source, options))
})

test('native pagination rechecks authorization and handles disabled providers as a failure', async () => {
  let pages = 0
  const ctx = { tools: { execute: async () => { pages++; return output({ source_id: 'inbox', account_id: 'account-1', items: [], next_offset: 1 }) } } }
  const generator = new BriefingGenerator(ctx, { call: async () => ({}) })
  const result = await generator.collect(source, options.since, options.until, options.signal, async () => { if (pages) throw new Error('revoked') })
  assert.equal(pages, 1)
  assert.deepEqual(result.errors, ['revoked'])
  ctx.tools.execute = async () => { throw new Error('plugin disabled') }
  assert.deepEqual((await generator.collect(source, options.since, options.until, options.signal)).errors, ['plugin disabled'])
})

test('course metadata refuses a changed account before calling its plugin', async () => {
  let calls = 0
  const ctx = { get: () => ({ briefingSource: async () => ({ accountId: 'new-account', tool: 'mcp__elearning__fetch' }) }), tools: { execute: async () => { calls++ } } }
  await assert.rejects(briefingSourceOptions(ctx, { source: 'elearning', accountId: 'old-account', tool: 'mcp__elearning__fetch' }), /账号已变化/)
  assert.equal(calls, 0)
})

test('eLearning briefing respects its 50-row limit and collects every subsequent page', async () => {
  const school = { source: 'elearning', accountId: 'school', tool: 'mcp__elearning__fetch', method: 'fetch', arguments: { course_ids: ['synthetic-course'] } }
  const records = Array.from({length:105}, (_,index) => ({source:'elearning',account_id:'school',source_id:'item-'+index,payload:{resource_type:'assignment'}}))
  const offsets = []
  const ctx = { tools: { execute: async ({calls:[call]}) => {
    assert.equal(call.name,school.tool)
    const args=JSON.parse(call.arguments)
    if (args.limit > 50) throw new Error('limit must be between 1 and 50')
    assert.deepEqual(args.course_ids,['synthetic-course'])
    assert.equal(args.since,options.since)
    assert.equal(args.until,options.until)
    offsets.push(args.offset)
    const page=records.slice(args.offset,args.offset+args.limit)
    return output({structuredContent:{value:{source_items:page,next_offset:args.offset+page.length<records.length?args.offset+page.length:null,errors:[]}}})
  } } }
  const result=await new BriefingGenerator(ctx,{call:async()=>({})}).collect(school,options.since,options.until,options.signal)
  assert.deepEqual(result.errors,[])
  assert.deepEqual(result.items,records)
  assert.deepEqual(offsets,[0,50,100])
})


test('old eLearning adapters fail clearly instead of silently reading without incremental state', async () => {
  const config={source:'elearning',accountId:'school',tool:'mcp__elearning__fetch',method:'fetch',arguments:{}}
  const ctx={tools:{schemas:()=>[{name:config.tool,parameters:{properties:{since:{type:'string'}}}}],execute:async()=>assert.fail('Old full fetch must not run')}}
  await assert.rejects(readBriefingSourcePage(ctx,config,options),/不支持增量读取/)
})


test('no selected sources cannot return a previous implicitly local daily report', async () => {
  const generator=new BriefingGenerator({}, {call:async()=>assert.fail('Empty selection must not load or reuse a previous report')})
  await assert.rejects(generator.generate({record:{date:'2026-11-06',notes:''},requestId:'same-scheduled-id',parent:{},sources:[]}),/请选择简报来源/)
})


test('plugin account availability replaces external briefing subscriptions while preserving saved course scope', async () => {
  const { effectiveBriefingSources } = await import('../../packages/alcor-organizer/src/briefing-sources.js')
  const mail = id => ({ source: 'email', accountId: id, tool: 'mcp__email__fetch_messages', arguments: { account_id: id } })
  const school = { source: 'elearning', accountId: 'school', tool: 'mcp__elearning__fetch', arguments: {} }
  const local = { source: 'alcor_tasks', accountId: 'local' }
  const preferences = [mail('disabled'), { ...school, arguments: { course_ids: ['chosen'] } }]
  const available = [mail('enabled-1'), mail('enabled-2'), school, local]
  const effective = effectiveBriefingSources(preferences, available)
  assert.deepEqual(effective.filter(row => row.source === 'email').map(row => row.accountId), ['enabled-1', 'enabled-2'])
  assert.deepEqual(effective.find(row => row.source === 'elearning').arguments, { course_ids: ['chosen'] })
  assert(!effective.some(row => row.source === 'alcor_tasks'), 'No implicit local sources')
  assert.deepEqual(effectiveBriefingSources(preferences, []), [], 'Disabled integration cannot retain access through an old preference')
  assert.deepEqual(effectiveBriefingSources([], available).find(row => row.source === 'elearning').arguments, {})
})


test('briefing group switches cannot bypass disabled plugins and preserve independent plugin availability', async () => {
  const { effectiveBriefingSources } = await import('../../packages/alcor-organizer/src/briefing-sources.js')
  const mail = { source: 'email', accountId: 'enabled', tool: 'mcp__email__fetch_messages', arguments: { account_id: 'enabled' } }
  const local = { source: 'alcor_tasks', accountId: 'local' }
  assert.deepEqual(effectiveBriefingSources([], [mail], { email: false }), [])
  assert.deepEqual(effectiveBriefingSources([], [], { email: true }), [])
  assert.deepEqual(effectiveBriefingSources([], [mail], { email: true }), [mail])
  assert.deepEqual(effectiveBriefingSources([], [local], { alcor_tasks: true }), [local])
  assert.deepEqual(effectiveBriefingSources([local], [local], { alcor_tasks: false }), [])
  const one = { source: 'plugin:notes:one', accountId: 'notes', tool: 'notes_briefing_fetch' }
  const two = { ...one, source: 'plugin:notes:two' }
  assert.deepEqual(effectiveBriefingSources([one], [one, two], { 'plugin:notes': true }), [one])
})


test('school briefing accepts assignments as individual candidates and discards other resources', async () => {
  const school = {source:'elearning',accountId:'school',method:'fetch',tool:'mcp__elearning__fetch',arguments:{}}
  const rows = ['assignment','announcement','grade','file','assignment'].map((kind,id) => ({source_id:String(id),payload:{resource_type:kind}}))
  const ctx = {tools:{execute:async () => output({source_items:rows,next_offset:null,cursors:{elearning_state:'checkpoint'}})}}
  const page = await readBriefingSourcePage(ctx,school,options)
  assert.deepEqual(page.source_items.map(row => row.source_id),['0','4'])
  assert.deepEqual(page.cursors,{elearning_state:'checkpoint'})
})
