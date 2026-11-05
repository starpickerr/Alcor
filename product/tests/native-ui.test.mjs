import test from 'node:test'
import assert from 'node:assert/strict'
import { cp, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { adaptModelsClient, verifyNativeUi } from '../../scripts/product/build-native-ui.mjs'

test('welcome namespace adaptation is exact and rejects incompatible upstream builds', () => {
  const original = 'const WELCOME_NOTICE_SETTINGS_NAMESPACE = "ui-settings-general";'
  assert.equal(adaptModelsClient(original), 'const WELCOME_NOTICE_SETTINGS_NAMESPACE = "stella-ui-settings-general";')
  assert.throws(() => adaptModelsClient(''), /Unexpected/)
  assert.throws(() => adaptModelsClient(original + original), /Unexpected/)
})

test('models onboarding preserves original profile override and inherited preferences', async () => {
  const { apply, inject } = await import('../../packages/stella-secretary/vendor/ui-settings-models/lib/index.js')
  assert.deepEqual(inject, ['configEditor'])
  for (const [override, inherited, fallback, expected] of [
    [false, true, true, false], [true, false, false, true],
    [undefined, false, true, false], [undefined, true, false, true],
    [undefined, undefined, false, false], [undefined, undefined, true, true],
  ]) {
    const table = []
    const ctx = {
      configEditor: { configuration: () => [{ entry: { options: { id: 'ui-settings-models' } },
        override: { credentialOnboarding: override }, inherited: { credentialOnboarding: inherited } }] },
      on: (event, listener) => { assert.equal(event, 'webserver/index-inject'); listener(table) },
    }
    apply(ctx, { credentialOnboarding: fallback })
    assert.deepEqual(table, [{ kind: 'global', name: '__DSH_MODELS_ONBOARDING__', value: { credentialOnboarding: expected } }])
  }
})

test('native UI package verification rejects missing, stale and changed runtime artifacts', async context => {
  const temporary = await mkdtemp(join(tmpdir(), 'stella-ui-integrity-'))
  context.after(() => rm(temporary, { recursive: true, force: true }))
  const directory = join(temporary, 'vendor')
  await cp(fileURLToPath(new URL('../../packages/stella-secretary/vendor', import.meta.url)), directory, { recursive: true })
  await verifyNativeUi(directory)
  for (const path of ['ui-layout/lib/client.js', 'ui-settings-general/lib/index.js', 'ui-settings-models/lib/upstream-index.js']) {
    const file = join(directory, path)
    const original = await readFile(file)
    await writeFile(file, Buffer.concat([original, Buffer.from('\n// changed artifact\n')]))
    await assert.rejects(verifyNativeUi(directory), /artifact changed|adaptation changed/)
    await writeFile(file, original)
    await verifyNativeUi(directory)
    await rm(file)
    await assert.rejects(verifyNativeUi(directory), { code: 'ENOENT' })
    await writeFile(file, original)
  }
  const recordFile = join(directory, 'NATIVE-UI-PROVENANCE.json')
  const record = await readFile(recordFile, 'utf8')
  const changed = JSON.parse(record)
  changed.patches['0002-client-product-entry.patch'] = '0'.repeat(64)
  await writeFile(recordFile, JSON.stringify(changed))
  await assert.rejects(verifyNativeUi(directory), /patch changed/)
  await writeFile(recordFile, record)
  const manifestFile = join(directory, 'ui-workspace/package.json')
  const originalManifest = await readFile(manifestFile, 'utf8')
  await writeFile(manifestFile, originalManifest.replace('"private": true', '"private": false'))
  await assert.rejects(verifyNativeUi(directory), /derivative manifest is invalid/)
  await writeFile(manifestFile, originalManifest)
  await verifyNativeUi(directory)
})
