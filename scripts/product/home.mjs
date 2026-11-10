import { mkdir, readdir, readFile, writeFile, lstat, chmod } from 'node:fs/promises'
import { homedir } from 'node:os'
import { join, resolve, relative, sep } from 'node:path'
import { canonicalizeWatchPath } from '@deepseek-ai/dsh-home-paths'
import { createMigrationMarker, MIGRATION_CREDENTIALS, MIGRATION_DIRECTORIES, MIGRATION_DOMAINS, MIGRATION_MARKER } from '../../packages/alcor-organizer/src/migration-contract.js'

export async function prepareProductHome(directory, repository, kind) {
  const candidate = resolve(directory)
  const canonical = await canonicalizeWatchPath(candidate)
  const userHome = await canonicalizeWatchPath(homedir())
  const repo = await canonicalizeWatchPath(repository)
  const inside = suffix => suffix === '' || (suffix !== '..' && !suffix.startsWith('..' + sep) && !suffix.startsWith(sep))
  for (const reserved of [userHome, join(userHome, '.dsh'), join(userHome, '.stella'), join(userHome, '.codex'), repo]) {
    const suffix = relative(reserved, canonical)
    const forbidden = reserved === userHome ? suffix === '' : inside(suffix)
    const scratch = reserved === repo && inside(relative(join(repo, '.local'), canonical))
    if (forbidden && !scratch) throw new Error('Use a dedicated Alcor product data directory outside existing personal data')
  }
  try {
    if ((await lstat(candidate)).isSymbolicLink()) throw new Error('Product data directory must not be a symbolic link')
  } catch (error) { if (error.code !== 'ENOENT') throw error }
  await mkdir(canonical, { recursive: true, mode: 0o700 })
  const currentMarker = join(canonical, '.alcor-product.json')
  const legacyMarker = join(canonical, '.stella-product.json')
  const present = async path => {
    try {
      const info = await lstat(path)
      if (!info.isFile() || info.isSymbolicLink()) throw new Error('Invalid product ownership marker')
      return true
    } catch (error) { if (error.code === 'ENOENT') return false; throw error }
  }
  const [current, legacy] = await Promise.all([present(currentMarker), present(legacyMarker)])
  if (current && legacy) throw new Error('Conflicting product ownership markers')
  const marker = legacy ? legacyMarker : currentMarker
  let ownership
  try {
    ownership = JSON.parse(await readFile(marker, 'utf8'))
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
    if ((await readdir(canonical)).length) throw new Error('Existing data directory is not owned by this Alcor preview')
    ownership = { version: 1, kind }
    await writeFile(marker, JSON.stringify(ownership) + '\n', { flag: 'wx', mode: 0o600 })
  }
  if (ownership.version !== 1 || ownership.kind !== kind) throw new Error('Product data directory belongs to a different runtime')
  await chmod(canonical, 0o700)
  return canonical
}

/** Keep an existing legacy preview in place; new previews use the Alcor name. */
export async function selectPreviewProfile(home) {
  const root = join(home, 'profiles')
  try {
    const info = await lstat(root)
    if (!info.isDirectory() || info.isSymbolicLink()) throw new Error('Invalid preview profiles directory')
  } catch (error) { if (error.code === 'ENOENT') return 'alcor'; throw error }
  const exists = async name => {
    try {
      const info = await lstat(join(root, name))
      if (!info.isDirectory() || info.isSymbolicLink()) throw new Error('Invalid preview profile directory')
      return true
    } catch (error) { if (error.code === 'ENOENT') return false; throw error }
  }
  const [current, legacy] = await Promise.all([exists('alcor'), exists('stella')])
  if (current && legacy) throw new Error('Conflicting preview profiles require review')
  return legacy ? 'stella' : 'alcor'
}

/** Initialize only a brand-new, locked preview home, never an existing Profile. */
export async function initializeFreshPreview(home, profileName) {
  if (!['alcor', 'desktop'].includes(profileName)) throw new Error('Invalid fresh preview profile')
  const names = (await readdir(home)).sort()
  if (JSON.stringify(names) !== JSON.stringify(['.alcor-product.json', 'product-instance.lock'])) return false
  if ((await readFile(join(home, 'product-instance.lock'), 'utf8')).trim() !== String(process.pid)) throw new Error('Fresh preview lock is not held by this process')
  const absent = keys => Object.fromEntries(Object.keys(keys).map(key => [key, 'absent']))
  const marker = createMigrationMarker({ source: 'fresh', directories: absent(MIGRATION_DIRECTORIES), domains: absent(MIGRATION_DOMAINS), credentials: absent(MIGRATION_CREDENTIALS), schedule: { mapped: 0 } })
  const target = join(home, 'profiles', profileName, 'alcor')
  await mkdir(target, { recursive: true, mode: 0o700 })
  await writeFile(join(target, MIGRATION_MARKER), JSON.stringify(marker) + '\n', { flag: 'wx', mode: 0o600 })
  return true
}
