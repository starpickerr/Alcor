// Run through the pinned upstream checkout's tsx; the official Desktop owns its Host and transport.
import { execFileSync, spawn } from 'node:child_process'
import { mkdir, readFile, writeFile, access } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { join, resolve, delimiter } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { withFileLock } from '@deepseek-ai/dsh-atomic-write'
import { prepareProductHome, initializeFreshPreview } from './home.mjs'
import { renamedEnv } from './env.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const source = join(root, '.local/upstream')
const upstream = JSON.parse(await readFile(join(root, 'product/upstream.lock.json'), 'utf8'))
const commit = execFileSync('git', ['rev-parse', 'HEAD'], {
  cwd: source, encoding: 'utf8',
  env: { ...process.env, GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_SYSTEM: '/dev/null' },
}).trim()
if (commit !== upstream.commit) throw new Error('Desktop source does not match the pinned release')
execFileSync('git', ['apply', '--reverse', '--check', join(root, 'product/patches/0001-desktop-private-environment.patch')], {
  cwd: source, env: { ...process.env, GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_SYSTEM: '/dev/null' },
})
const home = await prepareProductHome(resolve(renamedEnv('ALCOR_PRODUCT_HOME', 'STELLA_PRODUCT_HOME') || join(root, '.local/desktop-preview')), root, 'desktop')
await withFileLock(join(home, 'product-instance'), async () => {
await initializeFreshPreview(home, 'desktop')
const environment = Object.fromEntries(Object.entries(process.env).filter(([name]) => /^(PATH|HOME|USER|LOGNAME|SHELL|TMPDIR|LANG|LC_[A-Z_]+|https?_proxy|all_proxy|no_proxy|HTTPS?_PROXY|ALL_PROXY|NO_PROXY|SSL_CERT_FILE|NODE_EXTRA_CA_CERTS)$/.test(name)))
environment.DSH_HOME = home
environment.DSH_TELEMETRY_DISABLED = '1'
environment.DSH_DESKTOP_PRIMARY_RUNTIME_DIR = join(source, 'apps/desktop/.desktop-build/targets', process.arch === 'arm64' ? 'mac-arm64' : 'mac-x64', 'runtime/primary-runtime')
environment.DSH_DESKTOP_OPEN_DEVTOOLS = '0'
environment.PATH = join(root, 'node_modules/.bin') + delimiter + (environment.PATH || '')
const require = createRequire(join(source, 'apps/desktop/package.json'))
const electron = require('electron')
const [{ prepareDevelopmentProject }, { createPluginProfile }, { DESKTOP_HOST_PROTOCOL_VERSION }] = await Promise.all([
  import(pathToFileURL(join(source, 'apps/desktop/scripts/development-project.ts'))),
  import(pathToFileURL(join(source, 'apps/desktop/src/project-manager.ts'))),
  import(pathToFileURL(join(source, 'apps/desktop/src/host-protocol.ts'))),
])
const app = join(source, 'apps/desktop')
const projectDir = join(home, 'runtime-project')
prepareDevelopmentProject({
  projectDir, cliDir: join(source, 'apps/cli'), hostDir: join(source, 'apps/desktop-host'),
  dependencyDir: join(source, 'node_modules/.pnpm/node_modules'),
  target: process.arch === 'arm64' ? 'mac-arm64' : 'mac-x64',
  release: { schemaVersion: 1, version: upstream.npmVersion, hostProtocolVersion: DESKTOP_HOST_PROTOCOL_VERSION,
    nodeVersion: execFileSync(electron, ['-p', 'process.versions.node'], { env: { ...environment, ELECTRON_RUN_AS_NODE: '1' }, encoding: 'utf8' }).trim(),
    pnpmVersion: '11.28.5' },
})
const profile = join(home, 'profiles/desktop')
const newProfile = await access(join(profile, 'package.json')).then(() => false, error => {
  if (error.code === 'ENOENT') return true
  throw error
})
createPluginProfile(profile)
const secretary = 'link:' + join(root, 'packages/alcor-secretary')
if (newProfile) {
  // The official CLI exits after plugin operations, so it must own a child process.
  const setup = `import { runCli } from ${JSON.stringify(pathToFileURL(join(root, 'node_modules/@deepseek-ai/dsh/lib/bin.js')).href)};
    process.argv = [process.execPath, 'dsh', ...process.argv.slice(1)];
    await runCli({ manageDesktopProfile: true, packageManager: { command: process.execPath,
      args: ${JSON.stringify([join(root, 'node_modules/pnpm/bin/pnpm.cjs'), '--store-dir', join(home, 'pnpm-store')])} } });`
  await new Promise((done, fail) => {
    const install = spawn(process.execPath, ['--input-type=module', '-e', setup, 'plugin', '--profile', 'desktop', 'add', secretary, '--ignore-scripts'],
      { cwd: home, env: environment, stdio: 'inherit' })
    install.on('error', fail)
    install.on('exit', code => code === 0 ? done() : fail(new Error('Desktop plugin installation failed')))
  })
}
// An invocation overlay is unavailable in the Desktop Host; keep the native profile override owner-visible.
if (newProfile) await writeFile(join(profile, 'cordis.patch.yml'), await readFile(join(root, 'product/cordis.patch.yml')), { mode: 0o600 })
environment.DSH_DESKTOP_DSH_DIR = projectDir
environment.DSH_DESKTOP_PNPM_ENTRY = join(root, 'node_modules/pnpm/bin/pnpm.cjs')
const workspace = join(home, 'workspace')
await mkdir(workspace, { recursive: true, mode: 0o700 })
const child = spawn(electron, [app, '--user-data-dir=' + join(home, 'electron-user-data'), '--remote-debugging-port=0'], { cwd: workspace, env: environment, stdio: 'inherit' })
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal))
await new Promise((done, fail) => {
  child.on('error', fail)
  child.on('exit', code => { process.exitCode = code ?? 1; done() })
})
}, { waitMs: 0 })
