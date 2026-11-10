import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'esbuild'
import { createDeletePageHost } from '../../packages/alcor-secretary/src/delete-page-host.js'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'

// Real Gateway and owner methods, with synthetic SQLite and an injected confirmation.
// Do not initialize Organizer's Profile, timers, Apple services, or AppKit process.
async function fixture(t) {
  const { Context } = await import('@deepseek-ai/cordis')
  const { TypertRemoteService } = await import('@deepseek-ai/dsh-typert-protocol')
  const { default: Typert } = await import('@deepseek-ai/dsh-typert-registry')
  const { default: Gateway } = await import('@deepseek-ai/dsh-api-gateway')
  const { default: RemoteAccess } = await import('@deepseek-ai/dsh-api-remote-access')
  const { HostConnectionService } = await import('@deepseek-ai/dsh-client-connection')
  const { remote } = await import('../../packages/alcor-organizer/src/remote.js')
  const output = await mkdtemp(join(process.cwd(), '.alcor-bill-page-test-'))
  const data = await mkdtemp(join(tmpdir(), 'alcor-bill-page-test-'))
  const ctx = new Context()
  const worker = new BusinessWorker(data, { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) })
  let organizer
  t.after(async () => {
    organizer?.pageDeletes.close()
    await ctx.fiber.dispose()
    await worker.close()
    await rm(output, { recursive: true, force: true })
    await rm(data, { recursive: true, force: true })
  })
  const file = join(output, 'organizer.mjs')
  await build({ entryPoints: ['packages/alcor-organizer/src/index.ts'], outfile: file,
    bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24', logLevel: 'silent' })
  const Organizer = (await import(pathToFileURL(file).href)).default
  const state = { confirmed: false, prompts: [], prepared: [], commits: [], dispatches: [], beforeConfirm: async () => {} }
  const workerCall = worker.call.bind(worker)
  worker.call = (action, input) => {
    if (action === 'commit_bill_delete') state.dispatches.push(structuredClone(input))
    return workerCall(action, input)
  }
  class TestOrganizer extends TypertRemoteService {
    constructor(inner) {
      super(inner, 'alcorOrganizer')
      organizer = this
      this.active = true
      this.queue = Promise.resolve()
      this.businessWorker = worker
      this.deleteAuthorizers = new WeakMap()
      this.pageDeletes = createDeletePageHost({
        prepare: async (request, authorize) => {
          const prepared = await this.prepareApprovedDelete(request, authorize)
          state.prepared.push(prepared)
          return prepared
        },
        commit: (prepared, authorize) => {
          state.commits.push(prepared)
          for (const value of [prepared, prepared.request, prepared.request.arguments, prepared.plan, prepared.plan.billSnapshot, prepared.plan.targets, prepared.plan.targets[0]]) {
            assert.equal(Object.isFrozen(value), true, 'the owner receives the complete frozen preview')
          }
          return this.commitApprovedDelete(prepared, authorize)
        },
        confirm: async (request, signal) => {
          signal.throwIfAborted()
          state.prompts.push(structuredClone(request))
          await state.beforeConfirm(request)
          return state.confirmed
        },
      })
    }
  }
  for (const name of ['assertActive', 'serialize', 'business', 'approvedBillDelete', 'prepareApprovedDelete', 'commitApprovedDelete', 'preparePageDelete', 'commitPageDelete', 'cancelPageDelete']) {
    Object.defineProperty(TestOrganizer.prototype, name, Object.getOwnPropertyDescriptor(Organizer.prototype, name))
  }
  await ctx.plugin(Typert)
  await ctx.plugin(RemoteAccess)
  await ctx.plugin(Gateway, {})
  await ctx.plugin(TestOrganizer)
  ctx.typert.register({ package: '@alcor/organizer', face: 'host', schemas: [], invocations: remote.descriptors,
    model: { services: [], events: [], objects: [] } })
  await ctx.plugin(inner => { new HostConnectionService(inner, [], { isAuthenticated: () => true, authenticateApi() {} }) })
  const owner = ctx.connection.admit({ headers: { host: 'localhost', origin: 'http://localhost' } }).peer
  const other = ctx.connection.createPeer({ principal: { kind: 'owner' }, interactions: 'answer' })
  const outsider = ctx.connection.createPeer({ principal: { kind: 'synthetic-bill-delegate' }, interactions: 'none' })
  ctx.remoteAccess.registerPolicy('synthetic-bill-delegate', { bindings: () => [{ resource: { kind: 'global' },
    role: { name: 'test-bill-page', resource: 'global', actions: ['alcorOrganizer/preparePageDelete', 'alcorOrganizer/commitPageDelete', 'alcorOrganizer/cancelPageDelete'] } }] })
  const call = (method, request, peer = owner) => ctx.typertGateway.invoke({ namespace: 'alcorOrganizer', method, args: { request }, peer })
  const invoke = (method, args) => worker.call('invoke', { domain: 'bills', method, arguments: args })
  const create = () => invoke('create_ledger', { name: 'Synthetic bill page', amount_minor: 12345, currency: 'CNY',
    entry_type: 'expense', occurred_at: '2026-11-01T09:00:00Z', category: 'Synthetic', note: 'Frozen source note' })
  const preview = row => call('preparePageDelete', { domain: 'bills', kind: 'item', id: row.id, expectedRevision: row.revision })
  return { state, call, invoke, create, preview, owner, other, outsider, remote }
}

test('bill page cancellation writes nothing; confirmation commits the frozen displayed owner snapshot once', async t => {
  const f = await fixture(t), row = await f.create()
  const cancelled = await f.preview(row)
  assert.deepEqual(await f.invoke('get_ledger', { id: row.id }), row)
  assert.equal(f.state.prompts.length, 0)
  assert.equal(f.state.dispatches.length, 0)
  await assert.rejects(f.call('commitPageDelete', { ticket: cancelled.ticket }), /取消|未获确认/)
  const cancelPrompt = f.state.prompts[0]
  assert.deepEqual(await f.invoke('get_ledger', { id: row.id }), row)
  assert.equal(await f.invoke('get_operation', { operationId: cancelPrompt.operation_id }), null)
  assert.equal(f.state.commits.length, 0)
  assert.equal(f.state.dispatches.length, 0)
  await assert.rejects(f.call('commitPageDelete', { ticket: cancelled.ticket }), /失效/)
  assert.equal(f.state.prompts.length, 1)

  const preview = await f.preview(row)
  const source = f.state.prepared.at(-1), expected = structuredClone(source)
  assert.equal(preview.snapshot, createHash('sha256').update(JSON.stringify(row)).digest('hex'))
  assert.deepEqual(expected.plan.billSnapshot, row)
  assert.deepEqual(preview.targets, expected.plan.targets)
  // Mutating the prepare result after preview must not change what was retained.
  source.plan.billSnapshot.note = 'Changed outside retained preview'
  source.plan.targets[0].title = 'Forged replacement title'
  f.state.confirmed = true
  const result = await f.call('commitPageDelete', { ticket: preview.ticket })
  assert.equal(result.deleted, true)
  assert.equal(result.id, row.id)
  assert.deepEqual(f.state.commits[0], expected)
  const prompt = f.state.prompts.at(-1), dispatch = f.state.dispatches[0]
  assert.equal(prompt.operation_id, expected.request.operation_id)
  for (const text of [row.name, row.id, row.currency, row.occurred_at, preview.snapshot]) assert(prompt.detail.includes(text), text)
  assert.match(prompt.detail, /12345|123\.45/)
  assert.match(prompt.detail, /账单/)
  assert.match(prompt.detail, /永久删除|无法撤销|无法恢复/)
  assert.doesNotMatch(prompt.detail, /Forged replacement|Changed outside/)
  assert.deepEqual(dispatch.snapshot, row)
  assert.equal(dispatch.revision, row.revision)
  assert.equal(dispatch.operation_id, prompt.operation_id)
  assert.equal(await f.invoke('get_ledger', { id: row.id }), null)
  const receipt = await f.invoke('get_operation', { operationId: prompt.operation_id })
  assert.equal(receipt.method, 'delete_ledger')
  assert.equal(receipt.status, 'succeeded')
  assert.equal(receipt.result_id, row.id)
  await assert.rejects(f.call('commitPageDelete', { ticket: preview.ticket }), /失效/)
  assert.equal(f.state.dispatches.length, 1)
  assert.equal(f.state.prompts.length, 2)
  assert.deepEqual(await f.invoke('get_operation', { operationId: prompt.operation_id }), receipt)
})

test('bill page confirmed stale snapshot fails owner CAS and consumes its ticket without a receipt', async t => {
  const f = await fixture(t), row = await f.create(), preview = await f.preview(row)
  f.state.confirmed = true
  let changed
  f.state.beforeConfirm = async () => {
    changed = await f.invoke('update_ledger', { id: row.id, expectedRevision: row.revision, patch: { amount_minor: 54321, note: 'Changed during confirmation' } })
  }
  await assert.rejects(f.call('commitPageDelete', { ticket: preview.ticket }), /变化|conflict/)
  assert.deepEqual(f.state.dispatches[0].snapshot, row)
  assert.deepEqual(await f.invoke('get_ledger', { id: row.id }), changed)
  assert.equal(await f.invoke('get_operation', { operationId: f.state.prompts[0].operation_id }), null)
  await assert.rejects(f.call('commitPageDelete', { ticket: preview.ticket }), /失效/)
  assert.equal(f.state.dispatches.length, 1)
  assert.equal(f.state.prompts.length, 1)
  await assert.rejects(f.preview(row), /变化|conflict/)
})

test('bill page Gateway rejects foreign peers, forged commits and generic Remote deletion routes', async t => {
  const f = await fixture(t), row = await f.create(), preview = await f.preview(row)
  f.state.confirmed = true
  await assert.rejects(f.call('commitPageDelete', { ticket: preview.ticket }, f.other), /失效/)
  await assert.rejects(f.call('commitPageDelete', { ticket: preview.ticket }, f.outsider), /所有者/)
  await assert.rejects(f.call('preparePageDelete', { domain: 'bills', kind: 'item', id: row.id }, f.outsider), /所有者/)
  assert.equal(await f.call('cancelPageDelete', { ticket: preview.ticket }, f.other), false)
  await assert.rejects(f.call('commitPageDelete', { ticket: preview.ticket, snapshot: row, approved: true }))
  await assert.rejects(f.call('preparePageDelete', { domain: 'bills', kind: 'item', id: row.id, principal: { kind: 'owner' } }, f.outsider))
  for (const peer of [f.owner, f.other]) {
    await assert.rejects(f.call('business', { domain: 'bills', method: 'delete_ledger', arguments: {
      id: row.id, expectedRevision: row.revision, snapshot: row, approved: true, allowed_once: true,
    } }, peer), /人工确认/)
    await assert.rejects(f.call('business', { domain: 'bills', method: 'commit_bill_delete', arguments: {
      entry_id: row.id, operation_id: 'a'.repeat(64), revision: row.revision, snapshot: row,
    } }, peer), /Unknown business operation/)
    await assert.rejects(f.call('approvedBillDelete', { id: row.id, operation_id: 'a'.repeat(64), revision: row.revision, snapshot: row }, peer))
  }
  assert.doesNotMatch(JSON.stringify(f.remote.descriptors), /approvedBillDelete|prepareApprovedDelete|commitApprovedDelete/)
  assert.equal(f.state.prompts.length, 0)
  assert.equal(f.state.commits.length, 0)
  assert.equal(f.state.dispatches.length, 0)
  assert.deepEqual(await f.invoke('get_ledger', { id: row.id }), row)
  assert.equal(await f.invoke('get_operation', { operationId: 'a'.repeat(64) }), null)
  assert.equal(await f.call('cancelPageDelete', { ticket: preview.ticket }), true)
  await assert.rejects(f.call('commitPageDelete', { ticket: preview.ticket }), /失效/)
  assert.equal(f.state.prompts.length, 0)
})

test('closing a bill view while confirmation is pending invalidates its ticket and does not write', async t => {
  const f = await fixture(t), row = await f.create(), preview = await f.preview(row)
  f.state.confirmed = true
  let release, ready
  const shown = new Promise(resolve => { ready = resolve })
  f.state.beforeConfirm = async () => { ready(); await new Promise(resolve => { release = resolve }) }
  const pending = f.call('commitPageDelete', { ticket: preview.ticket })
  await shown
  assert.equal(await f.call('cancelPageDelete', { ticket: preview.ticket }), true)
  release()
  await assert.rejects(pending, /失效|取消|授权/)
  assert.deepEqual(await f.invoke('get_ledger', { id: row.id }), row)
  assert.equal(f.state.dispatches.length, 0)
  assert.equal(await f.invoke('get_operation', { operationId: f.state.prompts[0].operation_id }), null)
})
