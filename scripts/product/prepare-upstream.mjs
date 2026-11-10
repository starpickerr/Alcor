import { spawn, execFileSync } from 'node:child_process'
import { access, mkdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../../', import.meta.url))
const source = join(root, '.local/upstream')
const upstream = JSON.parse(await readFile(join(root, 'product/upstream.lock.json'), 'utf8'))
const environment = { ...process.env, GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_SYSTEM: '/dev/null' }
async function run(command, args, cwd = source) {
  await new Promise((done, fail) => {
    const child = spawn(command, args, { cwd, env: environment, stdio: 'inherit' })
    child.on('error', fail)
    child.on('exit', code => code === 0 ? done() : fail(new Error(`Product preparation failed: ${command} (${code})`)))
  })
}
await mkdir(join(root, '.local'), { recursive: true })
try { await access(join(source, '.git')) }
catch (error) {
  if (error.code !== 'ENOENT') throw error
  await run('git', ['clone', '--depth', '1', '--branch', upstream.tag, upstream.repository, source], root)
}
const git = args => execFileSync('git', args, { cwd: source, env: environment, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
if (git(['rev-parse', 'HEAD']).trim() !== upstream.commit) throw new Error('Existing upstream checkout is not the pinned release; it was preserved')
for (const name of upstream.patches) {
  const path = join(root, 'product', name)
  try { git(['apply', '--reverse', '--check', path]); continue }
  catch { /* A clean source needs the patch; git apply refuses conflicting local changes. */ }
  git(['apply', '--check', path])
  git(['apply', path])
}
const pnpm = join(root, 'node_modules/pnpm/bin/pnpm.cjs')
await run(process.execPath, [pnpm, 'install', '--frozen-lockfile', '--ignore-scripts'])
await run(process.execPath, [pnpm, 'run', 'build:artifacts'])
await run(process.execPath, [pnpm, 'exec', 'tsx', 'apps/desktop/scripts/prepare-primary-runtime.ts'])
const require = createRequire(join(source, 'apps/desktop/package.json'))
// Electron's reviewed installer verifies its download against the package checksums.
await run(process.execPath, [require.resolve('electron/install.js')])
await run(process.execPath, [join(root, 'scripts/product/sync-client.mjs')], root)
