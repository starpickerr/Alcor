import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'esbuild'
import { createDeletePageHost } from '../../packages/alcor-secretary/src/delete-page-host.js'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'

test('actual Gateway binds previews to owner peers, then requires separate local confirmation', async t => {
  const { Context } = await import('@deepseek-ai/cordis')
  const { TypertRemoteService } = await import('@deepseek-ai/dsh-typert-protocol')
  const { default: Typert } = await import('@deepseek-ai/dsh-typert-registry')
  const { default: Gateway } = await import('@deepseek-ai/dsh-api-gateway')
  const { default: RemoteAccess } = await import('@deepseek-ai/dsh-api-remote-access')
  const { HostConnectionService } = await import('@deepseek-ai/dsh-client-connection')
  const { remote } = await import('../../packages/alcor-organizer/src/remote.js')
  const output = await mkdtemp(join(process.cwd(), '.alcor-delete-gateway-')), data = await mkdtemp(join(tmpdir(), 'alcor-delete-gateway-'))
  const worker = new BusinessWorker(data, { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) }), ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose(); await worker.close(); await rm(output, { recursive: true, force: true }); await rm(data, { recursive: true, force: true }) })
  const file = join(output, 'organizer.mjs')
  await build({ entryPoints: ['packages/alcor-organizer/src/index.ts'], outfile: file, bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24', logLevel: 'silent' })
  const Organizer = (await import(pathToFileURL(file).href)).default
  let confirmed = false, prompts = 0
  class TestOrganizer extends TypertRemoteService { constructor(ctx) {
    super(ctx, 'alcorOrganizer'); this.active = true; this.queue = Promise.resolve(); this.businessWorker = worker; this.deleteAuthorizers = new WeakMap()
    this.pageDeletes = createDeletePageHost({ prepare: (request, authorize) => this.prepareApprovedDelete(request, authorize),
      commit: (plan, authorize) => this.commitApprovedDelete(plan, authorize), confirm: async () => { prompts++; return confirmed } })
  } }
  for (const name of ['assertActive','serialize','business','prepareLocalDelete','commitLocalDelete','prepareApprovedDelete','commitApprovedDelete','preparePageDelete','commitPageDelete','cancelPageDelete']) Object.defineProperty(TestOrganizer.prototype, name, Object.getOwnPropertyDescriptor(Organizer.prototype, name))
  await ctx.plugin(Typert); await ctx.plugin(RemoteAccess); await ctx.plugin(Gateway, {}); await ctx.plugin(TestOrganizer)
  ctx.typert.register({ package: '@alcor/organizer', face: 'host', schemas: [], invocations: remote.descriptors, model: { services: [], events: [], objects: [] } })
  const auth = { isAuthenticated: () => true, authenticateApi() {} }; await ctx.plugin(inner => { new HostConnectionService(inner, [], auth) })
  const owner = ctx.connection.admit({ headers: { host: 'localhost', origin: 'http://localhost' } }).peer
  const other = ctx.connection.createPeer({ principal: { kind: 'owner' }, interactions: 'answer' })
  const outsider = ctx.connection.createPeer({ principal: { kind: 'synthetic-delegate' }, interactions: 'none' })
  ctx.remoteAccess.registerPolicy('synthetic-delegate', { bindings: () => [{ resource: { kind: 'global' }, role: { name: 'test-delete', resource: 'global', actions: ['alcorOrganizer/preparePageDelete','alcorOrganizer/commitPageDelete'] } }] })
  const call = (method, request, peer = owner) => ctx.typertGateway.invoke({ namespace: 'alcorOrganizer', method, args: { request }, peer })
  const invoke = (domain, method, args) => worker.call('invoke', { domain, method, arguments: args })
  const row = await invoke('tasks', 'create_todo', { payload: { title: 'Synthetic' } })
  const event = await invoke('calendar', 'create_event', { title: 'Synthetic event', start_at: '2026-11-01T09:00:00Z', end_at: '2026-11-01T10:00:00Z' })
  const bill = await invoke('bills', 'create_ledger', { name: 'Synthetic bill', amount_minor: 123, currency: 'CNY', entry_type: 'expense', occurred_at: '2026-11-01T09:00:00Z' })
  for (const [domain, method, args] of [['tasks', 'delete_todo', { todo_id: row.id }], ['calendar', 'delete_event', { id: event.id }], ['bills', 'delete_ledger', { id: bill.id }]]) {
    for (const peer of [owner, other]) await assert.rejects(call('business', { domain, method, arguments: args }, peer), /人工确认/)
  }
  for (const peer of [owner, other]) {
    const preview = await call('preparePageDelete', { domain: 'tasks', kind: 'item', id: row.id }, peer)
    await assert.rejects(call('commitPageDelete', { ticket: preview.ticket }, peer === owner ? other : owner), /失效/)
    await assert.rejects(call('commitPageDelete', { ticket: preview.ticket }, peer), /取消|未获确认/)
  }
  assert.equal(prompts, 2)
  await assert.rejects(call('preparePageDelete', { domain: 'tasks', kind: 'item', id: row.id }, outsider), /所有者/)
  for (const peer of [owner, other, outsider]) await assert.rejects(call('commitPageDelete', { ticket: '00000000-0000-0000-0000-000000000000' }, peer), /失效|所有者|Invalid/)
  await assert.rejects(call('preparePageDelete', { domain: 'tasks', kind: 'item', id: row.id, principal: { kind: 'owner' } }, outsider))
  assert.equal(await call('cancelPageDelete', { ticket: '00000000-0000-0000-0000-000000000000' }), false)
  const current = await call('business', { domain: 'tasks', method: 'update_todo', arguments: { todo_id: row.id, payload: { title: 'Ordinary update' } } })
  assert.equal(current.title, 'Ordinary update')
  assert.equal((await invoke('bills', 'get_ledger', { id: bill.id })).id, bill.id)
  assert.equal((await invoke('calendar', 'get_event', { id: event.id })).id, event.id)
  const series = await invoke('calendar', 'create_event', { title: 'Synthetic repeat', start_at: '2026-11-01T09:00:00Z', end_at: '2026-11-01T10:00:00Z', repeat_frequency: 'daily', repeat_timezone: 'UTC' })
  confirmed = true
  const preview = await call('preparePageDelete', { domain: 'calendar', kind: 'item', id: series.id, scope: 'future', occurrenceOn: '2026-11-03' })
  await call('commitPageDelete', { ticket: preview.ticket })
  assert.equal((await invoke('calendar', 'get_event', { id: series.id })).repeat_until, '2026-11-02')
  await call('business', { domain: 'calendar', method: 'update_event', arguments: { id: series.id, patch: { repeat_until: null } } })
  assert.equal((await invoke('calendar', 'get_event', { id: series.id })).repeat_until, null)
})
