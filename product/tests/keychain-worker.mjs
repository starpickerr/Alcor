import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { Context } from '@deepseek-ai/cordis'
import KeychainCredentials from '../../packages/alcor-privacy/src/index.js'

const [operation, home, keychainPath] = process.argv.slice(2)
assert.ok(home && keychainPath)
await mkdir(home, { recursive: true })
const ctx = new Context()
ctx.provide('profileContext', { startedBundles: ['@alcor/privacy'] })
try {
  await ctx.plugin(KeychainCredentials, { dshHome: home, keychainPath })
  const provider = ctx.credentials
  if (operation === 'increment') {
    for (let index = 0; index < 3; index++) {
      await provider.modifyRecord('owner-provider/route', async current => ({ kind: 'grant', payload: { counter: current.payload.counter + 1 } }))
    }
  } else if (operation === 'locked') {
    await writeFile(home + '/.credentials.yaml', 'version: 1\nrefs:\n  STELLA_SYNTHETIC_TOKEN: forbidden-file-fallback\n')
    process.env.STELLA_SYNTHETIC_TOKEN = 'forbidden-environment-fallback'
    const calls = [
      () => provider.resolve('STELLA_SYNTHETIC_TOKEN'), () => provider.describe('STELLA_SYNTHETIC_TOKEN'),
      () => provider.set('STELLA_SYNTHETIC_TOKEN', 'forbidden-replacement'), () => provider.unset('STELLA_SYNTHETIC_TOKEN'),
      () => provider.readRecord('owner-provider/route'), () => provider.describeRecord('owner-provider/route'),
      () => provider.listRecords(), () => provider.modifyRecord('owner-provider/route', async () => ({ kind: 'api-key', key: 'forbidden-replacement' })),
      () => provider.deleteRecord('owner-provider/route'),
    ]
    for (const call of calls) await assert.rejects(call(), /Keychain unavailable.*-25308/)
  } else if (operation === 'unlocked') {
    assert.equal((await provider.resolve('STELLA_SYNTHETIC_TOKEN')).value, 'retained-after-lock-synthetic-key')
    assert.equal((await provider.readRecord('owner-provider/route')).payload.counter, 17)
  } else {
    throw new Error('Unknown test worker operation.')
  }
} finally {
  await ctx.fiber.dispose()
}
