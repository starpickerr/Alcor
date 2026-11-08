import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { copyFile, cp, lstat, mkdir, mkdtemp, readFile, readdir, realpath, rename, rm, writeFile } from 'node:fs/promises'
import { join, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { renamedEnv } from './env.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const clients = {
  'ui-layout': 'shell.top',
  'ui-workspace': 'workspace.browser',
  'ui-settings-general': 'settings.shell',
  'ui-schedule': 'schedule.manager.view',
  'ui-settings-models': 'WelcomeNoticeStore',
}
const modelsHost = join(root, 'scripts/product/native-ui-models-host.mjs')
const welcomeNamespace = 'const WELCOME_NOTICE_SETTINGS_NAMESPACE = "ui-settings-general";'
const bundledWelcomeNamespace = 'const WELCOME_NOTICE_SETTINGS_NAMESPACE = "alcor-ui-settings-general";'

export function adaptModelsClient(client) {
  if (client.split(welcomeNamespace).length !== 2) throw new Error('Unexpected DSH welcome settings namespace')
  return client.replace(welcomeNamespace, bundledWelcomeNamespace)
}
const patches = ['0002-client-product-entry.patch', '0004-workspace-browser-factory.patch', '0005-settings-shell-factory.patch', '0006-schedule-manager-factory.patch']
const hash = value => createHash('sha256').update(value).digest('hex')
const readJson = async path => JSON.parse(await readFile(path, 'utf8'))

export async function verifyNativeUi(directory = join(root, 'packages/alcor-secretary/vendor')) {
  const lock = await readJson(join(root, 'product/upstream.lock.json'))
  const provenance = await readJson(join(directory, 'NATIVE-UI-PROVENANCE.json'))
  if (provenance.format !== 1 || provenance.repository !== lock.repository || provenance.tag !== lock.tag
    || provenance.commit !== lock.commit || provenance.version !== lock.npmVersion) throw new Error('Native UI provenance does not match the pinned DSH release')
  if (Object.keys(provenance.patches ?? {}).sort().join() !== [...patches].sort().join()
    || Object.keys(provenance.clients ?? {}).sort().join() !== Object.keys(clients).sort().join()) throw new Error('Native UI provenance is missing the required patches or modules')
  for (const name of patches) {
    if (provenance.patches[name] !== hash(await readFile(join(root, 'product/patches', name)))) throw new Error('Native UI patch changed; rebuild native UI: ' + name)
  }
  for (const [name, marker] of Object.entries(clients)) {
    const target = join(directory, name)
    if (!(await lstat(target)).isDirectory() || !(await lstat(join(target, 'lib'))).isDirectory()) throw new Error('Native UI output directories must not be symbolic links')
    for (const file of ['package.json', 'lib/client.js', 'lib/index.js']) {
      if (!(await lstat(join(target, file))).isFile()) throw new Error('Native UI output must contain regular files: ' + name + '/' + file)
    }
    const manifest = await readJson(join(target, 'package.json'))
    const identity = '@deepseek-ai/dsh-client-' + name
    const entry = provenance.clients[name]
    if (manifest.name !== identity || manifest.version !== lock.npmVersion + '-alcor.1'
      || manifest.private !== true || manifest.license !== 'MIT' || !manifest.description?.includes('not an official DSH package release')
      || manifest.exports?.['./client'] !== './lib/client.js' || manifest.dsh?.client?.platform !== 'web'
      || manifest.alcor?.upstream?.commit !== lock.commit || entry.module !== identity) throw new Error('Native UI derivative manifest is invalid: ' + name)
    const client = await readFile(join(target, 'lib/client.js'))
    if (entry.client !== hash(client) || entry.host !== hash(await readFile(join(target, 'lib/index.js')))
      || !client.includes(marker) || !client.includes('id: "' + identity + '"')) throw new Error('Native UI artifact changed; rebuild native UI: ' + name)
    if (name === 'ui-settings-models') {
      const originalHost = join(target, 'lib/upstream-index.js')
      if (!(await lstat(originalHost)).isFile() || entry.upstreamHost !== hash(await readFile(originalHost))
        || entry.host !== hash(await readFile(modelsHost))
        || client.toString().split(bundledWelcomeNamespace).length !== 2
        || entry.upstreamClient !== hash(client.toString().replace(bundledWelcomeNamespace, welcomeNamespace))) {
        throw new Error('Native UI models adaptation changed; rebuild native UI')
      }
    }
  }
  return provenance
}

export async function buildNativeUi(source = renamedEnv('ALCOR_UPSTREAM_DIR', 'STELLA_UPSTREAM_DIR') || join(root, '.local/upstream')) {
  const upstream = await realpath(resolve(source))
  if (upstream.split(sep).includes('references')) throw new Error('Native UI builds must not use references/')
  const lock = await readJson(join(root, 'product/upstream.lock.json'))
  const gitEnvironment = { ...process.env, GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_SYSTEM: '/dev/null' }
  const git = args => execFileSync('git', args, { cwd: upstream, env: gitEnvironment, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
  if (git(['rev-parse', 'HEAD']) !== lock.commit || git(['rev-parse', lock.tag]) !== lock.commit) throw new Error('Native UI source must match the pinned official DSH release')
  const patchHashes = {}
  for (const name of patches) {
    const path = join(root, 'product/patches', name)
    git(['apply', '--reverse', '--check', path])
    patchHashes[name] = hash(await readFile(path))
  }

  const destination = join(root, 'packages/alcor-secretary/vendor')
  await mkdir(join(root, 'packages/alcor-secretary'), { recursive: true })
  const temporary = await mkdtemp(join(root, 'packages/alcor-secretary/.native-ui-'))
  const staged = join(temporary, 'vendor')
  const backup = join(temporary, 'previous')
  const provenance = { format: 1, repository: lock.repository, tag: lock.tag, commit: lock.commit, version: lock.npmVersion, patches: patchHashes, clients: {} }
  let saved = false
  try {
    for (const [name, marker] of Object.entries(clients)) {
      const directory = join(upstream, 'packages/client', name)
      const original = await readJson(join(directory, 'package.json'))
      if (original.name !== '@deepseek-ai/dsh-client-' + name || original.version !== lock.npmVersion) throw new Error('Unexpected native UI package: ' + name)
      const client = await readFile(join(directory, 'lib/client.js'))
      if (!client.includes(marker) || !client.includes('id: "' + original.name + '"')) throw new Error('Build the pinned patched client before packaging: ' + name)
      const target = join(staged, name)
      await mkdir(join(target, 'lib'), { recursive: true })
      // Keep the original module identity so existing DSH consumers resolve the same public services.
      const manifest = {
        name: original.name,
        version: lock.npmVersion + '-alcor.1',
        private: true,
        description: 'Alcor-maintained MIT derivative of ' + original.name + '; not an official DSH package release',
        type: 'module', license: 'MIT',
        main: './lib/index.js',
        exports: { '.': './lib/index.js', './client': './lib/client.js', './package.json': './package.json', './locale/*.json': './locale/*.json' },
        dsh: { client: original.dsh.client },
        alcor: { upstream: { repository: lock.repository, tag: lock.tag, commit: lock.commit, version: lock.npmVersion } },
      }
      await writeFile(join(target, 'package.json'), JSON.stringify(manifest, null, 2) + '\n')
      for (const file of await readdir(join(directory, 'lib'))) {
        if (/\.js(?:\.map)?$/.test(file)) await copyFile(join(directory, 'lib', file), join(target, 'lib', file))
      }
      await cp(join(directory, 'locale'), join(target, 'locale'), { recursive: true })
      await copyFile(join(upstream, 'LICENSE'), join(target, 'LICENSE.txt'))
      provenance.clients[name] = { module: original.name, client: hash(client), host: hash(await readFile(join(target, 'lib/index.js'))) }
      if (name === 'ui-settings-models') {
        const adaptedClient = adaptModelsClient(client.toString())
        await writeFile(join(target, 'lib/client.js'), adaptedClient)
        await rename(join(target, 'lib/index.js'), join(target, 'lib/upstream-index.js'))
        await copyFile(modelsHost, join(target, 'lib/index.js'))
        provenance.clients[name] = {
          module: original.name, client: hash(adaptedClient), host: hash(await readFile(modelsHost)),
          upstreamClient: hash(client), upstreamHost: provenance.clients[name].host,
        }
      }
    }
    await writeFile(join(staged, 'NATIVE-UI-PROVENANCE.json'), JSON.stringify(provenance, null, 2) + '\n')
    await verifyNativeUi(staged)
    try {
      if ((await lstat(destination)).isSymbolicLink()) throw new Error('Native UI output must not be a symbolic link')
      if ((await readJson(join(destination, 'NATIVE-UI-PROVENANCE.json'))).format !== 1) throw new Error('Unknown native UI output; preserving it')
      await rename(destination, backup)
      saved = true
    } catch (error) { if (error.code !== 'ENOENT') throw error }
    try { await rename(staged, destination) }
    catch (error) { if (saved) await rename(backup, destination); throw error }
    return provenance
  } finally { await rm(temporary, { recursive: true, force: true }) }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const result = await buildNativeUi()
  console.log(`Built ${Object.keys(result.clients).length} native UI derivatives for DSH ${result.version}; official installation files were not changed.`)
}
