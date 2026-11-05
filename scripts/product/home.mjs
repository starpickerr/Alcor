import { mkdir, readdir, readFile, writeFile, lstat, chmod } from 'node:fs/promises'
import { homedir } from 'node:os'
import { join, resolve, relative, sep } from 'node:path'
import { canonicalizeWatchPath } from '@deepseek-ai/dsh-home-paths'

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
    if (forbidden && !scratch) throw new Error('Use a dedicated Stella product data directory outside existing personal data')
  }
  try {
    if ((await lstat(candidate)).isSymbolicLink()) throw new Error('Product data directory must not be a symbolic link')
  } catch (error) { if (error.code !== 'ENOENT') throw error }
  await mkdir(canonical, { recursive: true, mode: 0o700 })
  const marker = join(canonical, '.stella-product.json')
  let ownership
  try {
    if ((await lstat(marker)).isSymbolicLink()) throw new Error('Invalid product ownership marker')
    ownership = JSON.parse(await readFile(marker, 'utf8'))
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
    if ((await readdir(canonical)).length) throw new Error('Existing data directory is not owned by this Stella preview')
    ownership = { version: 1, kind }
    await writeFile(marker, JSON.stringify(ownership) + '\n', { flag: 'wx', mode: 0o600 })
  }
  if (ownership.version !== 1 || ownership.kind !== kind) throw new Error('Product data directory belongs to a different runtime')
  await chmod(canonical, 0o700)
  return canonical
}
