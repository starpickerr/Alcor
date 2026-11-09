import { lstatSync, readFileSync, mkdtempSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'
import { MIGRATION_BUNDLES, MIGRATION_DIRECTORIES, MIGRATION_DOMAINS, MIGRATION_CREDENTIALS, MIGRATION_PACKAGE_NAMES, MIGRATION_MARKER, createMigrationMarker, isMigrationMarker } from './migration-contract.js'

function stat(path) {
  try { return lstatSync(path) } catch (error) { if (error.code === 'ENOENT') return undefined; throw error }
}

const fail = () => { throw new Error('Alcor 数据迁移尚未完成或状态不一致，请先离线检查 Profile。') }

// Existing state always requires a complete migration record.
export function assertProfileMigrated(ctx) {
  const profile = ctx.get('profileContext')
  if (!profile || !isAbsolute(profile.dir || '') || !isAbsolute(profile.home || '') || !Array.isArray(profile.startedBundles)) fail()
  if (MIGRATION_BUNDLES.some(pair => profile.startedBundles.includes(pair.old))) fail()
  const target = join(profile.dir, 'alcor'), storage = join(profile.home, 'storages')
  const targetStat = stat(target), storageStat = stat(storage)
  if (!targetStat?.isDirectory() || targetStat.isSymbolicLink() || (storageStat && (!storageStat.isDirectory() || storageStat.isSymbolicLink()))) fail()
  if (stat(join(profile.dir, 'stella'))) fail()
  for (const name of Object.values(MIGRATION_DOMAINS).map(pair => pair.old)) if (stat(join(storage, name)) || stat(join(storage, name + '.json'))) fail()
  const markerPath = join(target, MIGRATION_MARKER), markerStat = stat(markerPath)
  if (!markerStat?.isFile() || markerStat.isSymbolicLink() || markerStat.nlink !== 1 || markerStat.size > 8192) fail()
  let marker
  try { marker = JSON.parse(readFileSync(markerPath, 'utf8')) } catch { fail() }
  if (!isMigrationMarker(marker)) fail()
  for (const [key, name] of Object.entries(MIGRATION_DIRECTORIES)) {
    const entry = stat(join(target, name))
    if ((entry && (!entry.isDirectory() || entry.isSymbolicLink())) || (marker.directories[key] === 'migrated' && !entry)) fail()
  }
  for (const [key, pair] of Object.entries(MIGRATION_DOMAINS)) {
    const name = pair.current
    const path = join(storage, name + '.json'), entry = stat(path)
    if (stat(join(storage, name)) || (entry && (!entry.isFile() || entry.isSymbolicLink()))) fail()
    if (marker.domains[key] === 'migrated') {
      if (!entry) fail()
      try { if (JSON.parse(readFileSync(path, 'utf8'))?.unit?.name !== name) fail() } catch { fail() }
    }
  }
}


/** First installation only; never infer a migration from existing Alcor state. */
export async function initializeProfile(ctx) {
  const profile = ctx.get('profileContext')
  if (!profile || !isAbsolute(profile.dir || '') || !isAbsolute(profile.home || '') || !Array.isArray(profile.startedBundles)) fail()
  const target = join(profile.dir, 'alcor'), storage = join(profile.home, 'storages')
  if (stat(target)) return assertProfileMigrated(ctx)
  if (!ctx.credentials?.describeRecord) fail()
  for (const pair of Object.values(MIGRATION_CREDENTIALS)) {
    for (const key of [pair.old, pair.current]) if ((await ctx.credentials.describeRecord(key)).configured) fail()
  }
  // Other bundles can finish initialization while credential metadata is awaited.
  if (stat(target)) return assertProfileMigrated(ctx)
  for (const path of [profile.home, profile.dir]) {
    const info = stat(path)
    if (!info?.isDirectory() || info.isSymbolicLink()) fail()
  }
  if (MIGRATION_BUNDLES.some(pair => profile.startedBundles.includes(pair.old)) || stat(join(profile.dir, 'stella'))) fail()
  const manifest = stat(join(profile.dir, 'package.json'))
  if (manifest) {
    if (!manifest.isFile() || manifest.isSymbolicLink()) fail()
    const dependencies = JSON.parse(readFileSync(join(profile.dir, 'package.json'), 'utf8')).dependencies || {}
    if (MIGRATION_PACKAGE_NAMES.some(name => Object.hasOwn(dependencies, `@stella/${name}`))) fail()
  }
  const stored = stat(storage)
  if (stored && (!stored.isDirectory() || stored.isSymbolicLink())) fail()
  for (const pair of Object.values(MIGRATION_DOMAINS)) {
    for (const name of [pair.old, pair.current]) if (stat(join(storage, name)) || stat(join(storage, name + '.json'))) fail()
  }
  const absent = names => Object.fromEntries(Object.keys(names).map(key => [key, 'absent']))
  const marker = createMigrationMarker({ source: 'fresh', directories: absent(MIGRATION_DIRECTORIES), domains: absent(MIGRATION_DOMAINS), credentials: absent(MIGRATION_CREDENTIALS), schedule: { mapped: 0 } })
  const stage = mkdtempSync(join(profile.dir,'.alcor-first-install-'))
  try {
    writeFileSync(join(stage,MIGRATION_MARKER), JSON.stringify(marker) + '\n', { flag:'wx', mode:0o600 })
    try { renameSync(stage,target) }
    catch (error) { if (!['EEXIST','ENOTEMPTY'].includes(error.code)) throw error }
  } finally { rmSync(stage,{recursive:true,force:true}) }
  assertProfileMigrated(ctx)
}
