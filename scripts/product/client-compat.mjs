import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { copyFile, mkdir, readFile, rename, writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const clients = { layout: 'shell.top', workspace: 'workspace.browser', 'settings-general': 'settings.shell', schedule: 'schedule.manager.view' }
const digest = value => createHash('sha256').update(value).digest('hex')
const backupDirectory = runtime => join(runtime, 'stella-client-compat')
const targetFile = (runtime, name) => join(runtime, 'node_modules/@deepseek-ai/dsh-client-ui-' + name, 'lib/client.js')
const manifestFile = runtime => join(backupDirectory(runtime), 'manifest.json')

async function atomicWrite(path, value) {
  const temporary = path + '.stella-' + process.pid
  await writeFile(temporary, value, { flag: 'wx', mode: 0o600 })
  await rename(temporary, path)
}

async function checkVersions(runtime, version) {
  for (const name of ['dsh', ...Object.keys(clients).map(name => 'dsh-client-ui-' + name)]) {
    const manifest = JSON.parse(await readFile(join(runtime, 'node_modules/@deepseek-ai', name, 'package.json'), 'utf8'))
    if (manifest.version !== version) throw new Error('DSH compatibility version changed; verify the new release before starting')
  }
}

async function readManifest(runtime) {
  const manifest = JSON.parse(await readFile(manifestFile(runtime), 'utf8'))
  if (manifest.format !== 1 || typeof manifest.version !== 'string'
    || !['installing', 'active', 'restoring', 'restored'].includes(manifest.phase)) throw new Error('Invalid compatibility manifest')
  if (!manifest.files?.layout || !manifest.files?.workspace || Object.keys(manifest.files).some(name => !Object.hasOwn(clients, name))) throw new Error('Invalid compatibility files')
  for (const name of Object.keys(manifest.files)) {
    for (const kind of ['original', 'patched']) {
      if (!/^[a-f0-9]{64}$/.test(manifest.files?.[name]?.[kind])) throw new Error('Invalid compatibility checksum')
    }
  }
  await checkVersions(runtime, manifest.version)
  return manifest
}

export async function verifyCompatibility(runtime) {
  const manifest = await readManifest(runtime)
  if (!['active', 'restored'].includes(manifest.phase)) throw new Error('Compatibility update interrupted; restore before starting')
  const kind = manifest.phase === 'active' ? 'patched' : 'original'
  for (const name of Object.keys(manifest.files)) {
    if (digest(await readFile(targetFile(runtime, name))) !== manifest.files[name][kind]) throw new Error('DSH client changed; refusing to start an unverified combination')
  }
  return manifest
}

async function applySaved(runtime, manifest, kind) {
  const contents = {}
  for (const name of Object.keys(manifest.files)) {
    contents[name] = await readFile(join(backupDirectory(runtime), name + '.' + kind + '.js'))
    if (digest(contents[name]) !== manifest.files[name][kind]) throw new Error('Compatibility backup changed')
    const current = digest(await readFile(targetFile(runtime, name)))
    if (![manifest.files[name].original, manifest.files[name].patched].includes(current)) throw new Error('DSH client changed; preserving unknown changes')
  }
  manifest.phase = kind === 'patched' ? 'installing' : 'restoring'
  await atomicWrite(manifestFile(runtime), JSON.stringify(manifest, null, 2) + '\n')
  for (const name of Object.keys(manifest.files)) await atomicWrite(targetFile(runtime, name), contents[name])
  manifest.phase = kind === 'patched' ? 'active' : 'restored'
  await atomicWrite(manifestFile(runtime), JSON.stringify(manifest, null, 2) + '\n')
  return verifyCompatibility(runtime)
}

export async function restoreCompatibility(runtime) {
  return applySaved(runtime, await readManifest(runtime), 'original')
}

export async function installCompatibility(runtime, source, version) {
  await checkVersions(runtime, version)
  let existing
  try { existing = await readManifest(runtime) } catch (error) { if (error.code !== 'ENOENT') throw error }
  if (existing) {
    await verifyCompatibility(runtime)
    const updates = {}
    const additions = {}
    for (const [name, marker] of Object.entries(clients)) {
      const patched = await readFile(join(source, 'packages/client/ui-' + name, 'lib/client.js'))
      if (!patched.includes(marker)) throw new Error('Unexpected client artifact; build the pinned patch first')
      if (!existing.files[name]) {
        const original = await readFile(targetFile(runtime, name))
        if (original.includes(marker)) throw new Error('Unexpected client artifact; preserving unknown changes')
        additions[name] = { original, patched }
      } else if (digest(patched) !== existing.files[name].patched) updates[name] = patched
    }
    if (!Object.keys(updates).length && !Object.keys(additions).length) return existing.phase === 'active' ? existing : applySaved(runtime, existing, 'patched')
    // Restore first so an interrupted patch update always leaves recoverable originals.
    existing = await restoreCompatibility(runtime)
    for (const [name, patched] of Object.entries(updates)) {
      await atomicWrite(join(backupDirectory(runtime), name + '.patched.js'), patched)
      existing.files[name].patched = digest(patched)
    }
    for (const [name, files] of Object.entries(additions)) {
      for (const [kind, content] of Object.entries(files)) await atomicWrite(join(backupDirectory(runtime), name + '.' + kind + '.js'), content)
      existing.files[name] = { original: digest(files.original), patched: digest(files.patched) }
    }
    await copyFile(fileURLToPath(import.meta.url), join(backupDirectory(runtime), 'client-compat.mjs'))
    await atomicWrite(manifestFile(runtime), JSON.stringify(existing, null, 2) + '\n')
    return applySaved(runtime, existing, 'patched')
  }
  const contents = {}
  const manifest = { format: 1, version, phase: 'installing', files: {} }
  for (const [name, marker] of Object.entries(clients)) {
    const original = await readFile(targetFile(runtime, name))
    const patched = await readFile(join(source, 'packages/client/ui-' + name, 'lib/client.js'))
    if (!patched.includes(marker) || original.includes(marker)) throw new Error('Unexpected client artifact; build the pinned patch first')
    contents[name] = { original, patched }
    manifest.files[name] = { original: digest(original), patched: digest(patched) }
  }
  await mkdir(backupDirectory(runtime), { mode: 0o700 })
  for (const [name, files] of Object.entries(contents)) {
    for (const [kind, content] of Object.entries(files)) await writeFile(join(backupDirectory(runtime), name + '.' + kind + '.js'), content, { flag: 'wx', mode: 0o600 })
  }
  await copyFile(fileURLToPath(import.meta.url), join(backupDirectory(runtime), 'client-compat.mjs'))
  await atomicWrite(manifestFile(runtime), JSON.stringify(manifest, null, 2) + '\n')
  try { return await applySaved(runtime, manifest, 'patched') }
  catch (error) { await restoreCompatibility(runtime); throw error }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const [action, directory] = process.argv.slice(2)
    if (!['--install', '--check', '--restore'].includes(action) || !directory) throw new Error('Usage: client-compat.mjs --install|--check|--restore RUNTIME_DIRECTORY')
    const runtime = resolve(directory)
    if (action === '--check') await verifyCompatibility(runtime)
    else if (action === '--restore') await restoreCompatibility(runtime)
    else {
      const root = fileURLToPath(new URL('../../', import.meta.url))
      const source = join(root, '.local/upstream')
      const lock = JSON.parse(await readFile(join(root, 'product/upstream.lock.json'), 'utf8'))
      const gitEnvironment = { ...process.env, GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_SYSTEM: '/dev/null' }
      const commit = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: source, env: gitEnvironment, encoding: 'utf8' }).trim()
      if (commit !== lock.commit) throw new Error('Unexpected DSH source version')
      for (const name of ['0002-client-product-entry.patch', '0004-workspace-browser-factory.patch', '0005-settings-shell-factory.patch', '0006-schedule-manager-factory.patch']) execFileSync('git', ['apply', '--reverse', '--check', join(root, 'product/patches', name)], { cwd: source, env: gitEnvironment })
      await installCompatibility(runtime, source, lock.npmVersion)
    }
    console.log('PASS: DSH client compatibility ' + action.slice(2))
  } catch (error) {
    console.error(error.message)
    process.exitCode = 1
  }
}
