import test from 'node:test'
import assert from 'node:assert/strict'
import { discoverBriefingSources, readBriefingSourcePage, briefingSourceOptions } from '../../packages/stella-organizer/src/briefing-sources.js'
import { schemas } from '../../packages/stella-organizer/src/remote.js'
import { BriefingGenerator } from '../../packages/stella-organizer/src/briefing.js'

const source = { source: 'plugin:notes:inbox', accountId: 'account-1', label: 'Notes', tool: 'notes_briefing_fetch', method: 'briefing_fetch', arguments: { source_id: 'inbox' } }
const options = { since: '2026-11-04T00:00:00Z', until: '2026-11-05T00:00:00Z', offset: 0, cursors: {}, signal: new AbortController().signal }
const output = value => ({ results: [{ result: { isError: false, value } }] })

test('installed sources remain discoverable before account setup without reading content or exposing credentials', async () => {
  const states = new Map([
    ['stellaElearning', { state: async () => ({ installed: true, configured: false, connected: false }), briefingSource: async () => null }],
    ['stellaMailConnection', { state: async () => ({ installed: true, configured: false, connected: false, accounts: [], password: 'must not expose' }) }],
  ])
  const ctx = { get: key => states.get(key), tools: { definitions: () => [], execute: async () => { assert.fail('Unconfigured sources must not execute tools') } } }
  const result = await discoverBriefingSources(ctx)
  assert.deepEqual(result.connections, [
    { package: '@stella/elearning', label: 'eLearning', status: 'unconfigured' },
    { package: '@stella/mail', label: '邮件', status: 'unconfigured' },
  ])
  assert.deepEqual(result.sources.map(row => row.source), ['calendar_agenda', 'stella_tasks', 'stella_bills'])
  assert.deepEqual(result.errors, [])
  states.set('stellaElearning', { state: async () => ({ installed: true, configured: true, connected: false }), briefingSource: async () => null })
  assert.equal((await discoverBriefingSources(ctx)).connections[0].status, 'disconnected')
  states.clear()
  assert.deepEqual((await discoverBriefingSources(ctx)).connections, [])
})

test('connected account discovery replaces setup state and reports missing enabled accounts', async () => {
  const school = { source: 'elearning', accountId: 'school', tool: 'mcp__elearning__fetch', method: 'fetch', arguments: {} }
  const ctx = { get: key => key === 'stellaElearning'
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
  const generator = new BriefingGenerator(ctx, {})
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
