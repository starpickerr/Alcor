import { execFileSync } from 'node:child_process'
import { copyFile, readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../../', import.meta.url))
const source = join(root, '.local/upstream')
const upstream = JSON.parse(await readFile(join(root, 'product/upstream.lock.json'), 'utf8'))
const gitEnvironment = { ...process.env, GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_SYSTEM: '/dev/null' }
const commit = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: source, env: gitEnvironment, encoding: 'utf8' }).trim()
if (commit !== upstream.commit) throw new Error('Unexpected DSH source version')
for (const name of ['0002-client-product-entry.patch', '0004-workspace-browser-factory.patch', '0005-settings-shell-factory.patch', '0006-schedule-manager-factory.patch']) execFileSync('git', ['apply', '--reverse', '--check', join(root, 'product/patches', name)], { cwd: source, env: gitEnvironment })
for (const [name, marker] of [['ui-layout', 'shell.top'], ['ui-workspace', 'workspace.browser'], ['ui-settings-general', 'settings.shell'], ['ui-schedule', 'schedule.manager.view']]) {
  const artifact = join(source, 'packages/client', name, 'lib/client.js')
  if (!(await readFile(artifact, 'utf8')).includes(marker)) throw new Error('Build the patched DSH Client before assembling Alcor')
  const destination = join(root, 'node_modules/@deepseek-ai/dsh-client-' + name)
  const manifest = JSON.parse(await readFile(join(destination, 'package.json'), 'utf8'))
  if (manifest.version !== upstream.npmVersion) throw new Error('Unexpected installed Client version')
  await copyFile(artifact, join(destination, 'lib/client.js'))
}
