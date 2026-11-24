import { build } from 'esbuild'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = fileURLToPath(new URL('../../', import.meta.url))
const sibling = { mail: '../dsh-email/plugin', elearning: '../dsh-fudan-elearning/plugin', 'apple-sync': '../dsh-apple-sync' }
export const packageDirectory = name => sibling[name] ?? `packages/alcor-${name}`
export const sharedSources = {
  name: 'alcor-shared-sources',
  setup(build) {
    build.onResolve({ filter: /^\.\.\/\.\.\/\.\.\/Alcor\// }, ({ path }) => ({
      path: resolve(root, path.slice('../../../Alcor/'.length)),
    }))
  },
}

export async function loadSourceModule(t, name, file) {
  const directory = await mkdtemp(resolve(root, '.source-test-'))
  t.after(() => rm(directory, { recursive: true, force: true }))
  const outfile = resolve(directory, 'lib', 'source.mjs')
  if (file === 'index.ts' && ['elearning', 'mail'].includes(name)) {
    const server = resolve(directory, 'server')
    await mkdir(server)
    await writeFile(resolve(server, 'platform.json'), JSON.stringify({ target: `${process.platform}-${process.arch}` }))
  }
  await build({ entryPoints: [resolve(root, packageDirectory(name), 'src', file)], outfile,
    plugins: [sharedSources], bundle: true, packages: 'external', platform: 'node', format: 'esm', target: 'node24' })
  return import(pathToFileURL(outfile))
}
