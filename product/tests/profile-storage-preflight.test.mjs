import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { assertProfileMigrated, initializeProfile } from '../../packages/alcor-organizer/src/profile-migration.js'

const fresh = () => ({ schema: 1, status: 'complete', source: 'fresh', directories: { business: 'absent', appleSync: 'absent', email: 'absent', elearning: 'absent' }, domains: { organizer: 'absent', secretary: 'absent' }, credentials: { elearning: 'absent', email: 'absent' }, schedule: { mapped: 0 } })
async function fixture(t) {
  const home = await mkdtemp(join(tmpdir(), 'alcor-profile-check-')), dir = join(home, 'profiles', 'fixture')
  t.after(() => rm(home, { recursive: true, force: true }))
  await mkdir(join(dir, 'alcor'), { recursive: true })
  await mkdir(join(home, 'storages'))
  const profile = { dir, home, startedBundles: ['@alcor/secretary'] }
  const ctx = { get: name => name === 'profileContext' ? profile : undefined }
  const marker = value => writeFile(join(dir, 'alcor', '.migration.json'), JSON.stringify(value))
  return { home, dir, profile, ctx, marker }
}

test('fresh profiles require explicit initialization, without modifying old data', async t => {
  const { dir, ctx, marker } = await fixture(t)
  assert.throws(() => assertProfileMigrated(ctx), /迁移/)
  await mkdir(join(dir, 'stella'))
  await writeFile(join(dir, 'stella', 'sentinel'), 'unchanged')
  await marker(fresh())
  assert.throws(() => assertProfileMigrated(ctx), /迁移/)
  assert.equal(await readFile(join(dir, 'stella', 'sentinel'), 'utf8'), 'unchanged')
  await rm(join(dir, 'stella'), { recursive: true })
  assert.doesNotThrow(() => assertProfileMigrated(ctx))
  assert.doesNotThrow(() => assertProfileMigrated(ctx), 'restart must preserve the decision')
})

test('incomplete, contradictory, linked, or conflicting migration states fail closed', async t => {
  const { home, dir, profile, ctx, marker } = await fixture(t)
  const bad = fresh(); bad.directories.business = 'migrated'
  await marker(bad); assert.throws(() => assertProfileMigrated(ctx), /迁移/)
  await marker({ schema: 1, status: 'complete' }); assert.throws(() => assertProfileMigrated(ctx), /迁移/)
  const wrongCredential = fresh(); wrongCredential.credentials.email = 'migrated'
  await marker(wrongCredential); assert.throws(() => assertProfileMigrated(ctx), /迁移/)
  const absentCredentialField = fresh(); delete absentCredentialField.credentials
  await marker(absentCredentialField); assert.throws(() => assertProfileMigrated(ctx), /迁移/)
  const invalidCredential = fresh(); invalidCredential.source = 'stella'; invalidCredential.credentials.email = 'unknown'
  await marker(invalidCredential); assert.throws(() => assertProfileMigrated(ctx), /迁移/)
  await marker({ ...fresh(), extra: true }); assert.throws(() => assertProfileMigrated(ctx), /迁移/)
  await marker(fresh()); profile.startedBundles.push('@stella/privacy')
  assert.throws(() => assertProfileMigrated(ctx), /迁移/); profile.startedBundles.pop()
  await writeFile(join(home, 'storages', 'stella_organizer.json'), '{}')
  assert.throws(() => assertProfileMigrated(ctx), /迁移/)
  await rm(join(home, 'storages', 'stella_organizer.json'))
  await symlink(join(home, 'storages'), join(dir, 'alcor', 'business'))
  assert.throws(() => assertProfileMigrated(ctx), /迁移/)
})

test('migrated marker requires declared directories and correctly named domain', async t => {
  const { home, dir, ctx, marker } = await fixture(t)
  const migrated = fresh(); migrated.source = 'stella'; migrated.directories.business = 'migrated'; migrated.domains.organizer = 'migrated'; migrated.credentials.email = 'migrated'; migrated.schedule.mapped = 1
  await marker(migrated); assert.throws(() => assertProfileMigrated(ctx), /迁移/)
  await mkdir(join(dir, 'alcor', 'business'))
  const domain = join(home, 'storages', 'alcor_organizer.json')
  await writeFile(domain, JSON.stringify({ unit: { name: 'stella_organizer', version: 1 }, tables: {} }))
  assert.throws(() => assertProfileMigrated(ctx), /迁移/)
  await writeFile(domain, JSON.stringify({ unit: { name: 'alcor_organizer', version: 1 }, tables: {} }))
  assert.doesNotThrow(() => assertProfileMigrated(ctx))
  await mkdir(join(home, 'storages', 'stella_secretary'))
  assert.throws(() => assertProfileMigrated(ctx), /迁移/)
})


test('native installation initializes a new Alcor profile without reading credential values', async t => {
  const { home, dir, profile, ctx } = await fixture(t)
  await rm(join(dir, 'alcor'), { recursive: true })
  let checks = 0
  ctx.credentials = { describeRecord: async key => { checks++; assert.match(key, /^(stella|alcor)-(elearning|email)\//); return { configured: false } } }
  await writeFile(join(home, 'storages', 'schedule.json'), 'host-owned schedule')
  await Promise.all([initializeProfile(ctx), initializeProfile(ctx)])
  assertProfileMigrated(ctx)
  assert.deepEqual(JSON.parse(await readFile(join(dir, 'alcor', '.migration.json'), 'utf8')), fresh())
  assert(checks >= 4)
  const before = await readFile(join(dir, 'alcor', '.migration.json'), 'utf8')
  ctx.credentials.describeRecord = async () => { throw Error('existing profile does not need credential inspection') }
  await initializeProfile(ctx)
  assert.equal(await readFile(join(dir, 'alcor', '.migration.json'), 'utf8'), before)
  assert.equal(await readFile(join(home, 'storages', 'schedule.json'), 'utf8'), 'host-owned schedule')
})

test('native initialization refuses existing state, legacy packages and unavailable credential metadata', async t => {
  for (const state of ['stella', 'alcor', 'domain', 'legacy-package', 'credential', 'credentials-unavailable']) {
    const { home, dir, ctx } = await fixture(t)
    await rm(join(dir, 'alcor'), { recursive: true })
    ctx.credentials = { describeRecord: async () => ({ configured: state === 'credential' }) }
    if (state === 'credentials-unavailable') ctx.credentials.describeRecord = async () => { throw Error('locked') }
    if (['stella', 'alcor'].includes(state)) await mkdir(join(dir, state))
    if (state === 'domain') await writeFile(join(home, 'storages', 'alcor_organizer.json'), 'sentinel')
    if (state === 'legacy-package') await writeFile(join(dir, 'package.json'), JSON.stringify({ dependencies: { '@stella/secretary': '0.1.0' } }))
    await assert.rejects(initializeProfile(ctx), /迁移|locked/, state)
    assert.equal(await readFile(join(dir, 'alcor', '.migration.json'), 'utf8').catch(error => error.code), 'ENOENT', state)
  }
})
