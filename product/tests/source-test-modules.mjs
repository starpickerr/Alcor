import { build } from 'esbuild'
import { mkdtemp, rm } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = fileURLToPath(new URL('../../', import.meta.url))
const sibling = { mail: '../dsh-email/plugin', elearning: '../dsh-fudan-elearning/plugin', 'apple-sync': '../dsh-apple-sync' }
export const packageDirectory = name => sibling[name] ?? `packages/alcor-${name}`

export async function loadSourceModule(t, name, file) {
  const directory = await mkdtemp(resolve(root, '.source-test-'))
  t.after(() => rm(directory, { recursive: true, force: true }))
  const outfile = resolve(directory, 'source.mjs')
  await build({ entryPoints: [resolve(root, packageDirectory(name), 'src', file)], outfile,
    bundle: true, packages: 'external', platform: 'node', format: 'esm', target: 'node24' })
  return import(pathToFileURL(outfile))
}
