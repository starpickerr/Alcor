import { lstatSync, readFileSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'
import { MIGRATION_BUNDLES, MIGRATION_DIRECTORIES, MIGRATION_DOMAINS, MIGRATION_MARKER, isMigrationMarker } from './migration-contract.js'

function stat(path) {
  try { return lstatSync(path) } catch (error) { if (error.code === 'ENOENT') return undefined; throw error }
}

// A completion record is written only after an offline migration or an explicit
// fresh-profile initialization. This check does not replace stopping old writers.
export function assertProfileMigrated(ctx) {
  const fail = () => { throw new Error('Alcor 数据迁移尚未完成或状态不一致，请先离线检查 Profile。') }
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
