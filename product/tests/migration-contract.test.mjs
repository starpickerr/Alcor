import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { accountKey } from '../../packages/alcor-secretary/src/elearning.js'
import { mailKey } from '../../packages/alcor-mail/src/connection.js'
import { alcorTarget } from '../../packages/alcor-organizer/src/automation.js'
import { MIGRATION_BUNDLES, MIGRATION_PACKAGE_NAMES, MIGRATION_CREDENTIALS, MIGRATION_DOMAINS, MIGRATION_DIRECTORIES, MIGRATION_PRESETS, MIGRATION_BRIEFING_TOOLS, createMigrationMarker, isMigrationMarker } from '../../packages/alcor-organizer/src/migration-contract.js'

const root = resolve(import.meta.dirname, '../..')
const read = path => readFile(join(root, path), 'utf8')
const absent = keys => Object.fromEntries(Object.keys(keys).map(key => [key, 'absent']))
const fresh = () => ({ source: 'fresh', directories: absent(MIGRATION_DIRECTORIES), domains: absent(MIGRATION_DOMAINS), credentials: absent(MIGRATION_CREDENTIALS), schedule: { mapped: 0 } })

test('marker is exact, versioned, and fresh cannot declare migrated data', () => {
  const marker = createMigrationMarker(fresh())
  assert(isMigrationMarker(marker))
  assert(!isMigrationMarker({ ...marker, extra: true }))
  const noCredentials = structuredClone(marker); delete noCredentials.credentials
  assert(!isMigrationMarker(noCredentials))
  const falseFresh = structuredClone(marker); falseFresh.credentials.email = 'migrated'
  assert(!isMigrationMarker(falseFresh)); assert.throws(() => createMigrationMarker({ ...fresh(), schedule: { mapped: 1 } }))
  const migrated = structuredClone(marker); migrated.source = 'stella'; migrated.credentials.email = 'migrated'; migrated.schedule.mapped = 1
  assert(isMigrationMarker(migrated))
})

test('contract agrees with package manifests, stored names and active callers', async () => {
  for (const name of MIGRATION_PACKAGE_NAMES) {
    const pkg = JSON.parse(await read(`packages/alcor-${name}/package.json`))
    assert.equal(pkg.name, `@alcor/${name}`)
  }
  for (const pair of MIGRATION_BUNDLES) {
    assert.equal(pair.old, `@stella/${pair.name}`); assert.equal(pair.current, `@alcor/${pair.name}`)
    assert((await read(`packages/alcor-${pair.name}/cordis.patch.yml`)).includes(pair.current))
  }
  assert.equal(accountKey, MIGRATION_CREDENTIALS.elearning.current)
  assert.equal(mailKey, MIGRATION_CREDENTIALS.email.current)
  assert.equal(alcorTarget(undefined, undefined).create.agentPreset, MIGRATION_PRESETS.current)
  const patch = await read('packages/alcor-secretary/cordis.patch.yml')
  for (const id of Object.values(MIGRATION_PRESETS)) assert(patch.includes(`id: ${id}\n`), `preset ${id}`)
  assert((await read('packages/alcor-secretary/src/tools.js')).includes(MIGRATION_BRIEFING_TOOLS.current))
  assert((await read('packages/alcor-organizer/src/index.ts')).includes(`name: '${MIGRATION_DOMAINS.organizer.current}'`))
  assert((await read('packages/alcor-secretary/src/index.ts')).includes(`name: '${MIGRATION_DOMAINS.secretary.current}'`))
  for (const [name, path] of Object.entries({ business: 'packages/alcor-organizer/src/index.ts', appleSync: 'packages/alcor-apple-sync/src/index.ts', email: 'packages/alcor-mail/src/index.ts', elearning: 'packages/alcor-elearning/src/index.ts' })) {
    assert((await read(path)).includes(`'alcor', '${MIGRATION_DIRECTORIES[name]}'`), name)
  }
})

test('plugin bundles inline the shared contract instead of requiring a runtime workspace path', async () => {
  const { build } = await import('esbuild')
  for (const name of ['secretary', 'organizer', 'apple-sync', 'elearning', 'mail', 'agent-hub']) {
    const result = await build({ entryPoints: [join(root, `packages/alcor-${name}/src/index.ts`)], bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24', write: false })
    const output = result.outputFiles[0].text
    assert(!/from\s*["'][^"']*migration-contract(?:\.js)?["']/.test(output), name)
    assert(output.includes('stella-elearning/account'), `shared contract missing from ${name}`)
  }
})
