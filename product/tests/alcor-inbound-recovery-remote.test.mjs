import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'esbuild'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'

test('inbound recovery remote admits only host-authenticated owner and commits once', async t => {
  const output = await mkdtemp(join(process.cwd(), '.alcor-recovery-build-')), data = await mkdtemp(join(tmpdir(), 'alcor-recovery-remote-'))
  const worker = new BusinessWorker(data, { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) })
  t.after(async () => { await worker.close(); await rm(output, { recursive: true, force: true }); await rm(data, { recursive: true, force: true }) })
  const file = join(output, 'organizer.mjs')
  await build({ entryPoints: ['packages/alcor-organizer/src/index.ts'], outfile: file, bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24', logLevel: 'silent' })
  const Organizer = (await import(pathToFileURL(file).href)).default, organizer = Object.create(Organizer.prototype)
  organizer.active = true; organizer.queue = Promise.resolve(); organizer.businessWorker = worker; organizer.ctx = {}
  const item = await worker.call('invoke', { domain: 'tasks', method: 'create_todo', arguments: { payload: { title: 'Synthetic recovery' } } })
  await worker.call('apple_peer', { domain: 'tasks', method: 'apple_remove_item', args: [item.id] })
  await assert.rejects(organizer.listInboundRecoveries({ domain: 'tasks' }), /所有者/)
  organizer.ctx = { invocation: { signal: AbortSignal.timeout(5000), peer: { principal: { kind: 'guest' }, signal: AbortSignal.timeout(5000) } } }
  await assert.rejects(organizer.listInboundRecoveries({ domain: 'tasks', principal: { kind: 'owner' } }))
  await assert.rejects(organizer.listInboundRecoveries({ domain: 'tasks' }), /所有者/)
  const controller = new AbortController()
  organizer.ctx = { invocation: { signal: controller.signal, peer: { principal: { kind: 'owner' }, signal: controller.signal } } }
  const records = await organizer.listInboundRecoveries({ domain: 'tasks' }); assert.equal(records.length, 1); assert.equal(records[0].title, 'Synthetic recovery')
  controller.abort(); await assert.rejects(organizer.restoreInboundRecovery({ domain: 'tasks', recoveryId: records[0].id }))
  organizer.ctx = { invocation: { signal: AbortSignal.timeout(5000), peer: { principal: { kind: 'owner' }, signal: AbortSignal.timeout(5000) } } }
  const result = await organizer.restoreInboundRecovery({ domain: 'tasks', recoveryId: records[0].id })
  assert.equal(result.sync, 'local_only'); assert.equal(result.ids.length, 1)
  const replay = await organizer.restoreInboundRecovery({ domain: 'tasks', recoveryId: records[0].id }); assert.equal(replay.replayed, true); assert.deepEqual(replay.ids, result.ids)
  const item2 = await worker.call('invoke', { domain: 'tasks', method: 'get_todo', arguments: { todo_id: result.ids[0] } }); assert.equal(item2.source_type, 'apple_recovered')
})

test('actual gateway admits owner recovery while non-owner and forged JSON cannot elevate', async t => {
  const { Context } = await import('@deepseek-ai/cordis')
  const { TypertRemoteService } = await import('@deepseek-ai/dsh-typert-protocol')
  const { default: Typert } = await import('@deepseek-ai/dsh-typert-registry')
  const { default: Gateway } = await import('@deepseek-ai/dsh-api-gateway')
  const { default: RemoteAccess } = await import('@deepseek-ai/dsh-api-remote-access')
  const { HostConnectionService } = await import('@deepseek-ai/dsh-client-connection')
  const { remote } = await import('../../packages/alcor-organizer/src/remote.js')
  const output = await mkdtemp(join(process.cwd(), '.alcor-recovery-gateway-')), data = await mkdtemp(join(tmpdir(), 'alcor-recovery-gateway-'))
  const worker = new BusinessWorker(data, { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) }), ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose(); await worker.close(); await rm(output, { recursive: true, force: true }); await rm(data, { recursive: true, force: true }) })
  const file = join(output, 'organizer.mjs')
  await build({ entryPoints: ['packages/alcor-organizer/src/index.ts'], outfile: file, bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24', logLevel: 'silent' })
  const Organizer = (await import(pathToFileURL(file).href)).default
  class TestOrganizer extends TypertRemoteService { constructor(ctx) { super(ctx, 'alcorOrganizer'); this.active = true; this.queue = Promise.resolve(); this.businessWorker = worker } }
  for (const name of ['assertActive','serialize','listInboundRecoveries','restoreInboundRecovery']) Object.defineProperty(TestOrganizer.prototype, name, Object.getOwnPropertyDescriptor(Organizer.prototype, name))
  await ctx.plugin(Typert); await ctx.plugin(RemoteAccess); await ctx.plugin(Gateway, {}); await ctx.plugin(TestOrganizer)
  ctx.typert.register({ package: '@alcor/organizer', face: 'host', schemas: [], invocations: remote.descriptors, model: { services: [], events: [], objects: [] } })
  const auth = { isAuthenticated: () => true, authenticateApi() {} }; await ctx.plugin(inner => { new HostConnectionService(inner, [], auth) })
  const owner = ctx.connection.admit({ headers: { host: 'localhost', origin: 'http://localhost' } }).peer
  const outsider = ctx.connection.createPeer({ principal: { kind: 'synthetic-delegate' }, interactions: 'none' })
  ctx.remoteAccess.registerPolicy('synthetic-delegate', { bindings: () => [{ resource: { kind: 'global' }, role: { name: 'test-recovery', resource: 'global', actions: ['alcorOrganizer/listInboundRecoveries','alcorOrganizer/restoreInboundRecovery'] } }] })
  const call = (method, request, peer = owner) => ctx.typertGateway.invoke({ namespace: 'alcorOrganizer', method, args: { request }, peer })
  const item = await worker.call('invoke', { domain: 'tasks', method: 'create_todo', arguments: { payload: { title: 'Synthetic gateway' } } })
  await worker.call('apple_peer', { domain: 'tasks', method: 'apple_remove_item', args: [item.id] })
  await assert.rejects(call('listInboundRecoveries', { domain: 'tasks' }, outsider), /所有者/)
  await assert.rejects(call('listInboundRecoveries', { domain: 'tasks', principal: { kind: 'owner' } }, outsider))
  const records = await call('listInboundRecoveries', { domain: 'tasks' }); assert.equal(records.length, 1)
  await assert.rejects(call('restoreInboundRecovery', { domain: 'tasks', recoveryId: records[0].id }, outsider), /所有者/)
  const result = await call('restoreInboundRecovery', { domain: 'tasks', recoveryId: records[0].id }); assert.equal(result.sync, 'local_only')
})
