// In-memory Keychain transport only. No Security.framework calls or real profile.
import { test, mock } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm, readFile, realpath } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createHash } from 'node:crypto'
import { Service } from '@deepseek-ai/cordis'

const records = new Map()
const calls = []
let fail = false, failOperation = null
mock.module(new URL('../src/keychain.js', import.meta.url), { exports: {
  keychainRequest: async request => {
    calls.push({ service: request.service, operation: request.operation })
    if (fail || request.operation === failOperation) throw new Error('synthetic failure')
    if (request.operation === 'read') return records.get(request.service)
    if (request.operation === 'write') { records.set(request.service, request.value); return }
    throw new Error('unexpected operation')
  },
} })
const { default: Provider } = await import('../src/index.js')

function makeContext(events) {
  return {
    reflect: { provide() {} },
    get(name) { return name === 'profileContext' ? { startedBundles: ['@alcor/privacy'] } : undefined },
    effect() {},
    events: { dispatch(_type, [name]) { return [value => events.push([name, value])] } },
    logger: { warn() {} },
  }
}
async function makeProvider(home, events = []) {
  const provider = new Provider(makeContext(events), { dshHome: home })
  await provider[Service.init]()
  return provider
}

test('new default identity reads and writes only synthetic Alcor records', async t => {
  const home = await mkdtemp(join(tmpdir(), 'alcor-identity-fake-'))
  t.after(() => rm(home, { recursive: true, force: true }))
  records.clear(); calls.length = 0; const events = []
  const provider = await makeProvider(home, events)
  const canonicalHome = await realpath(home)
  const hash = createHash('sha256').update(canonicalHome).digest('hex')
  assert.equal(provider.service, 'com.alcor.dsh.credentials.v1.' + hash)
  assert.equal(provider.lockPath, join(canonicalHome, '.alcor-keychain-credentials'))
  const oldService = 'com.stella.dsh.credentials.v1.' + hash
  const legacy = JSON.stringify({ version: 1, refs: { OLD: 'synthetic-legacy' }, records: {} })
  records.set(oldService, legacy)
  assert.equal(await provider.resolve('OLD'), undefined)
  await provider.set('TEST_TOKEN', 'synthetic-one')
  assert.deepEqual(await provider.resolve('TEST_TOKEN'), { value: 'synthetic-one', source: 'keychain' })
  await provider.modifyRecord('owner/example', async () => ({ kind: 'api-key', key: 'synthetic-key' }))
  const restarted = await makeProvider(home)
  assert.equal((await restarted.resolve('TEST_TOKEN')).value, 'synthetic-one')
  assert.equal((await restarted.readRecord('owner/example')).key, 'synthetic-key')
  await restarted.set('TEST_TOKEN', 'synthetic-two')
  assert.equal((await provider.resolve('TEST_TOKEN')).value, 'synthetic-two')
  await restarted.unset('TEST_TOKEN')
  assert.equal(await provider.resolve('TEST_TOKEN'), undefined)
  assert.equal(records.get(oldService), legacy)
  assert.ok(calls.every(call => call.service === provider.service))
  assert.deepEqual(events, [['credentials/reference-updated', 'TEST_TOKEN'], ['credentials/record-updated', 'owner/example']])
})

test('different homes are isolated and synthetic transport failure does not commit or notify', async t => {
  const a = await mkdtemp(join(tmpdir(), 'alcor-identity-a-'))
  const b = await mkdtemp(join(tmpdir(), 'alcor-identity-b-'))
  t.after(async () => { fail = false; await rm(a, { recursive: true, force: true }); await rm(b, { recursive: true, force: true }) })
  records.clear(); calls.length = 0
  const events = []; const first = await makeProvider(a, events); const second = await makeProvider(b)
  await first.set('TEST_TOKEN', 'original')
  assert.equal(await second.resolve('TEST_TOKEN'), undefined)
  const before = records.get(first.service)
  fail = true
  await assert.rejects(first.set('TEST_TOKEN', 'replacement'), /synthetic failure/)
  fail = false
  assert.equal(records.get(first.service), before)
  assert.equal((await first.resolve('TEST_TOKEN')).value, 'original')
  assert.deepEqual(events, [['credentials/reference-updated', 'TEST_TOKEN']])
})

test('source configuration and helper use the same new identity; old bundle remains blocked', async () => {
  const config = await readFile(new URL('../cordis.patch.yml', import.meta.url), 'utf8')
  const swift = await readFile(new URL('../native/Keychain.swift', import.meta.url), 'utf8')
  assert.match(config, /id: alcor-keychain/)
  assert.match(config, /disabled: true/)
  assert.match(swift, /com\.alcor\.dsh\.credentials\.v1\./)
  assert.match(swift, /request\.service\.count == 93/)
  assert.doesNotMatch(swift, /com\.stella\.dsh\.credentials\.v1\./)
  const ctx = makeContext([]); ctx.get = () => ({ startedBundles: ['@stella/privacy'] })
  assert.throws(() => new Provider(ctx, {}), /不能同时启用/)
})


test('synthetic write failure after successful read preserves old record and emits no notification', async t => {
  const home = await mkdtemp(join(tmpdir(), 'alcor-identity-write-fail-'))
  t.after(async () => { failOperation = null; await rm(home, { recursive: true, force: true }) })
  records.clear(); calls.length = 0
  const events = [], provider = await makeProvider(home, events)
  await provider.set('TEST_TOKEN', 'before')
  const before = records.get(provider.service), notifications = events.length
  calls.length = 0; failOperation = 'write'
  await assert.rejects(provider.set('TEST_TOKEN', 'after'), /synthetic failure/)
  failOperation = null
  assert.deepEqual(calls.map(call => call.operation), ['read', 'write'])
  assert.equal(records.get(provider.service), before)
  assert.equal((await provider.resolve('TEST_TOKEN')).value, 'before')
  assert.equal(events.length, notifications)
})
