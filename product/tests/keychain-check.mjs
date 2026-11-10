import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { mkdtemp, mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import KeychainCredentials from '../../packages/alcor-privacy/src/index.js'
import { keychainRequest } from '../../packages/alcor-privacy/src/keychain.js'

const keychainPath = process.env.STELLA_TEST_KEYCHAIN
const testHome = process.env.STELLA_TEST_HOME
assert.ok(keychainPath && testHome, 'Run through product/tests/keychain_check.py with its isolated Keychain.')
const execute = promisify(execFile)

async function fixture(context, name) {
  const home = name ? join(testHome, name) : await mkdtemp(join(testHome, 'case-'))
  await mkdir(home, { recursive: true })
  const ctx = new Context()
  context.after(() => ctx.fiber.dispose())
  await ctx.plugin(KeychainCredentials, { dshHome: home, keychainPath })
  return { ctx, provider: ctx.credentials, home }
}

test('Keychain references rotate and survive restarts without file or ambient fallback', async context => {
  const { ctx, provider, home } = await fixture(context)
  const events = []
  ctx.on('credentials/reference-updated', ref => { events.push(ref) })
  await writeFile(join(home, '.credentials.yaml'), 'version: 1\nrefs:\n  STELLA_SYNTHETIC_TOKEN: file-fallback-is-forbidden\n')
  const before = await readFile(join(home, '.credentials.yaml'), 'utf8')
  process.env.STELLA_SYNTHETIC_TOKEN = 'environment-fallback-is-forbidden'
  context.after(() => { delete process.env.STELLA_SYNTHETIC_TOKEN })
  assert.equal(await provider.resolve('STELLA_SYNTHETIC_TOKEN'), undefined)
  assert.equal(await provider.resolve('constructor'), undefined)
  assert.deepEqual(await provider.describe('STELLA_SYNTHETIC_TOKEN'), { configured: false, writable: true })
  const secret = 'synthetic-中文-"-\n-$(not-executed)'
  await provider.set('STELLA_SYNTHETIC_TOKEN', secret)
  assert.deepEqual(await provider.resolve('STELLA_SYNTHETIC_TOKEN'), { value: secret, source: 'keychain' })
  assert.deepEqual(await provider.describe('STELLA_SYNTHETIC_TOKEN'), { configured: true, writable: true, source: 'keychain' })
  await provider.set('__proto__', secret)
  assert.equal((await provider.resolve('__proto__')).value, secret)
  const other = await fixture(context, home.split('/').at(-1))
  assert.equal((await other.provider.resolve('STELLA_SYNTHETIC_TOKEN')).value, secret)
  await other.provider.set('STELLA_SYNTHETIC_TOKEN', 'rotated-synthetic-key')
  assert.equal((await provider.resolve('STELLA_SYNTHETIC_TOKEN')).value, 'rotated-synthetic-key')
  await provider.unset('STELLA_SYNTHETIC_TOKEN')
  await provider.unset('STELLA_SYNTHETIC_TOKEN')
  assert.equal(await provider.resolve('STELLA_SYNTHETIC_TOKEN'), undefined)
  assert.deepEqual(events, ['STELLA_SYNTHETIC_TOKEN', '__proto__', 'STELLA_SYNTHETIC_TOKEN'])
  assert.equal(await readFile(join(home, '.credentials.yaml'), 'utf8'), before)
  assert.deepEqual(await readdir(home), ['.credentials.yaml'])
})

test('records preserve opaque grants, metadata, no-op mutations and committed notifications', async context => {
  const { ctx, provider } = await fixture(context)
  const events = []
  ctx.on('credentials/record-updated', key => { events.push(key) })
  const key = 'owner-provider/route'
  assert.equal(await provider.readRecord(key), undefined)
  await provider.modifyRecord(key, async () => ({ kind: 'api-key' }))
  assert.deepEqual(await provider.describeRecord(key), { configured: true, writable: true, kind: 'api-key' })
  const grant = { kind: 'grant', payload: JSON.parse('{"token":"synthetic-grant","nested":{"__proto__":{"owner":true}},"counter":1}') }
  await provider.modifyRecord(key, async () => grant)
  grant.payload.token = 'caller-mutation'
  assert.equal((await provider.readRecord(key)).payload.token, 'synthetic-grant')
  const original = await provider.readRecord(key)
  assert.deepEqual(await provider.modifyRecord(key, async current => {
    current.payload.token = 'discarded'
    return undefined
  }), original)
  assert.deepEqual(await provider.readRecord(key), original)
  assert.deepEqual(await provider.listRecords(), [{ key, kind: 'grant' }])
  assert.equal(JSON.stringify(await provider.describeRecord(key)).includes('synthetic-grant'), false)
  await assert.rejects(provider.modifyRecord(key, async () => { throw new Error('synthetic-token-must-not-escape') }), error => !error.message.includes('synthetic-token-must-not-escape'))
  assert.deepEqual(await provider.readRecord(key), original)
  await provider.deleteRecord(key)
  await provider.deleteRecord(key)
  assert.deepEqual(await provider.listRecords(), [])
  assert.deepEqual(events, [key, key, key])
})

test('invalid records, oversized writes and malformed stored data leave credentials protected', async context => {
  const { provider } = await fixture(context)
  await provider.set('STELLA_SYNTHETIC_TOKEN', 'retained-synthetic-key')
  await assert.rejects(provider.set('INVALID-REF', 'synthetic'), /Invalid credential reference/)
  await assert.rejects(provider.set('STELLA_SYNTHETIC_TOKEN', ''), /non-empty/)
  for (const value of [
    { kind: 'unknown' }, { kind: 'api-key', key: '' }, { kind: 'api-key', env: { 'BAD-NAME': 'synthetic' } },
    { kind: 'grant' }, { kind: 'grant', payload: undefined }, { kind: 'grant', payload: Infinity },
    { kind: 'grant', payload: new Date() }, { kind: 'api-key', extra: 'synthetic' },
  ]) await assert.rejects(provider.modifyRecord('owner-provider/route', async () => value))
  const cyclic = { kind: 'grant', payload: {} }
  cyclic.payload.self = cyclic
  await assert.rejects(provider.modifyRecord('owner-provider/route', async () => cyclic), /JSON/)
  await assert.rejects(provider.set('STELLA_SYNTHETIC_TOKEN', 'x'.repeat(1_048_576)), /size limit/)
  assert.equal((await provider.resolve('STELLA_SYNTHETIC_TOKEN')).value, 'retained-synthetic-key')
  await keychainRequest({ service: provider.service, keychainPath, operation: 'write', value: '{"secret":"synthetic-must-not-appear-in-error"}' })
  for (const operation of [() => provider.resolve('STELLA_SYNTHETIC_TOKEN'), () => provider.set('STELLA_SYNTHETIC_TOKEN', 'replacement')]) {
    await assert.rejects(operation(), error => /invalid/.test(error.message) && !error.message.includes('synthetic-must-not-appear-in-error'))
  }
})

test('cross-process record refreshes serialize the decision and preserve every committed increment', async context => {
  const { provider, home } = await fixture(context)
  await provider.modifyRecord('owner-provider/route', async () => ({ kind: 'grant', payload: { counter: 0 } }))
  await Promise.all(Array.from({ length: 3 }, () => execute(process.execPath, ['product/tests/keychain-worker.mjs', 'increment', home, keychainPath], {
    env: { PATH: process.env.PATH }, timeout: 30_000,
  })))
  assert.equal((await provider.readRecord('owner-provider/route')).payload.counter, 9)
  assert.deepEqual(await readdir(home), [])
})

test('different DSH homes do not read each other, and stopped providers cannot commit pending mutations', async context => {
  const first = await fixture(context)
  const second = await fixture(context)
  await first.provider.set('STELLA_SYNTHETIC_TOKEN', 'namespace-isolated-synthetic-key')
  assert.equal(await second.provider.resolve('STELLA_SYNTHETIC_TOKEN'), undefined)
  const ready = Promise.withResolvers()
  const release = Promise.withResolvers()
  const mutation = first.provider.modifyRecord('owner-provider/route', async () => {
    ready.resolve()
    await release.promise
    return { kind: 'api-key', key: 'uncommitted-synthetic-key' }
  })
  await ready.promise
  await first.ctx.fiber.dispose()
  release.resolve()
  await assert.rejects(mutation, /stopped/)
  await assert.rejects(first.provider.resolve('STELLA_SYNTHETIC_TOKEN'), /stopped/)
  const reopened = await fixture(context, first.home.split('/').at(-1))
  assert.equal(await reopened.provider.readRecord('owner-provider/route'), undefined)
})

test('seeded credentials remain available for the subsequent real Keychain lock/unlock probe', async context => {
  const { provider } = await fixture(context, 'lock-case')
  await provider.set('STELLA_SYNTHETIC_TOKEN', 'retained-after-lock-synthetic-key')
  await provider.modifyRecord('owner-provider/route', async () => ({ kind: 'grant', payload: { counter: 17 } }))
})

test('stopping a provider rejects a Keychain read already in flight', async context => {
  const { ctx, provider } = await fixture(context)
  await provider.set('STELLA_SYNTHETIC_TOKEN', 'stopped-read-must-not-return')
  const pending = provider.resolve('STELLA_SYNTHETIC_TOKEN')
  const rejection = assert.rejects(pending, /stopped/)
  await ctx.fiber.dispose()
  await rejection
})

test('an unavailable explicit Keychain fails without opening a different Keychain', async context => {
  const { provider } = await fixture(context)
  await assert.rejects(keychainRequest({ service: provider.service, keychainPath: join(testHome, 'missing.keychain-db'), operation: 'read' }), /Keychain unavailable/)
})
