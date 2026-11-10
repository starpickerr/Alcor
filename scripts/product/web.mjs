import { spawn } from 'node:child_process'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { delimiter, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { withFileLock } from '@deepseek-ai/dsh-atomic-write'
import { prepareProductHome, selectPreviewProfile, initializeFreshPreview } from './home.mjs'
import { renamedEnv } from './env.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const dataRoot = resolve(renamedEnv('ALCOR_PRODUCT_HOME', 'STELLA_PRODUCT_HOME') || join(root, '.local/dsh-preview'))
const canonical = await prepareProductHome(dataRoot, root, 'web')
await withFileLock(join(canonical, 'product-instance'), async () => {
await initializeFreshPreview(canonical, 'alcor')
const profileName = await selectPreviewProfile(canonical)
const profile = join(canonical, 'profiles', profileName)
await mkdir(profile, { recursive: true, mode: 0o700 })
let newProfile = false
try {
  await writeFile(join(profile, 'package.json'), JSON.stringify({
    name: 'alcor-preview-profile', private: true,
    dsh: { profile: { bundles: ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app', '@alcor/secretary'] } },
  }, null, 2) + '\n', { flag: 'wx', mode: 0o600 })
  newProfile = true
} catch (error) { if (error.code !== 'EEXIST') throw error }
const profileManifest = JSON.parse(await readFile(join(profile, 'package.json'), 'utf8'))
if (!['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app'].every(name => profileManifest.dsh?.profile?.bundles?.includes(name))) {
  throw new Error('Existing profile is not a compatible DSH preview profile; choose a new data directory')
}
if (profileManifest.dsh.profile.bundles.includes('@stella/secretary')) throw new Error('旧版 Stella Profile 需要先离线迁移，不能与 Alcor 同时启动。')
const workspace = join(canonical, 'workspace')
await mkdir(workspace, { recursive: true, mode: 0o700 })
const environment = Object.fromEntries(Object.entries(process.env).filter(([name]) =>
  /^(PATH|HOME|USER|LOGNAME|SHELL|TMPDIR|LANG|LC_[A-Z_]+|https?_proxy|all_proxy|no_proxy|HTTPS?_PROXY|ALL_PROXY|NO_PROXY|SSL_CERT_FILE|NODE_EXTRA_CA_CERTS)$/.test(name)))
environment.PATH = join(root, 'node_modules/.bin') + delimiter + (environment.PATH || '')
environment.DSH_HOME = canonical
environment.DSH_TELEMETRY_DISABLED = '1'
const cli = join(root, 'node_modules/@deepseek-ai/dsh/lib/bin.js')
const secretary = 'link:' + join(root, 'packages/alcor-secretary')
if (newProfile || (!profileManifest.dependencies?.['@alcor/secretary'] && profileManifest.dsh.profile.bundles.includes('@alcor/secretary'))) {
  await new Promise((done, fail) => {
    const install = spawn(process.execPath, [cli, 'plugin', '--profile', profileName, 'add', secretary, '--ignore-scripts'], { cwd: workspace, env: environment, stdio: 'inherit' })
    install.on('error', fail)
    install.on('exit', code => code === 0 ? done() : fail(new Error('Native bundle installation failed')))
  })
}
const child = spawn(process.execPath, [cli,
  '--profile', profileName, '--patch', join(root, 'product/cordis.patch.yml'), ...process.argv.slice(2)],
{ cwd: workspace, env: environment, stdio: 'inherit' })
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal))
await new Promise((done, fail) => {
  child.on('error', fail)
  child.on('exit', (code, signal) => { process.exitCode = code ?? (signal ? 1 : 0); done() })
})
}, { waitMs: 0 })
