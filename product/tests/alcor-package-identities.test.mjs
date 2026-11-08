import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { tmpdir } from 'node:os'
import { build } from 'esbuild'
import { Service } from '@deepseek-ai/cordis'
import { assertLegacySecretaryDetached } from '../../packages/alcor-agent-hub/src/legacy.js'
import { briefingSourceCards, canonicalPackage, sourceOwner } from '../../packages/alcor-secretary/src/briefing-source-selection.js'
import { discoverBriefingSources, effectiveBriefingSources } from '../../packages/alcor-organizer/src/briefing-sources.js'

const root = resolve(import.meta.dirname, '../..')
const read = path => readFile(join(root, path), 'utf8')
const packages = ['secretary', 'organizer', 'apple-sync', 'elearning', 'mail', 'privacy', 'agent-hub']
async function readyProfile(directory) {
  const home = join(directory, 'home'), dir = join(home, 'profiles', 'fixture')
  await mkdir(join(dir, 'alcor'), { recursive: true })
  await writeFile(join(dir, 'alcor', '.migration.json'), JSON.stringify({ schema: 1, status: 'complete', source: 'fresh', directories: { business: 'absent', appleSync: 'absent', email: 'absent', elearning: 'absent' }, domains: { organizer: 'absent', secretary: 'absent' }, credentials: { elearning: 'absent', email: 'absent' }, schedule: { mapped: 0 } }))
  return { home, dir }
}

test('workspace, lock, patches, remotes and loader identities agree', async () => {
  const lock = JSON.parse(await read('package-lock.json'))
  const rootPackage = JSON.parse(await read('package.json'))
  assert.equal(rootPackage.name, 'alcor-product')
  assert.equal(lock.name, rootPackage.name)
  for (const name of packages) {
    const directory = `packages/alcor-${name}`
    const pkg = JSON.parse(await read(`${directory}/package.json`))
    assert.equal(pkg.name, `@alcor/${name}`)
    assert.equal(lock.packages[directory].name, pkg.name)
    assert.equal(lock.packages[directory].version, pkg.version)
    assert.equal(lock.packages[`node_modules/${pkg.name}`].resolved, directory)
    assert.equal(lock.packages[`node_modules/@stella/${name}`], undefined)
    if (name !== 'organizer') assert.match(await read(`${directory}/cordis.patch.yml`), new RegExp(`@alcor/${name}`))
    if (name !== 'privacy') assert.match(await read(`${directory}/src/remote.js`), new RegExp(`@alcor/${name}`))
    if (['secretary', 'apple-sync', 'agent-hub'].includes(name)) {
      const buildFile = { secretary: 'build-client', 'apple-sync': 'build-apple-sync', 'agent-hub': 'build-agent-hub' }[name]
      assert.ok((await read(`scripts/product/${buildFile}.mjs`)).includes(`id:"${pkg.name}"`))
    }
  }
  assert.match(await read('scripts/product/build-sources.mjs'), /JSON\.parse\(await readFile\(join\(directory, 'package.json'\), 'utf8'\)\)\.name/)
})

test('new services reject a selected old secretary before accessing storage', async t => {
  const directory = await mkdtemp(join(root, '.alcor-test-'))
  t.after(() => rm(directory, { recursive: true, force: true }))
  const profile = await readyProfile(directory)
  for (const name of ['secretary', 'organizer']) {
    const output = join(directory, `${name}.mjs`)
    await build({ entryPoints: [join(root, `packages/alcor-${name}/src/index.ts`)], outfile: output, bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24' })
    const { default: Plugin } = await import(pathToFileURL(output))
    let touched = false
    const ctx = { get: () => ({ startedBundles: ['@stella/secretary', '@alcor/secretary'] }), storageDomain: { open: () => { touched = true; throw Error('storage touched') } } }
    await assert.rejects(Plugin.prototype[Service.init].call({ ctx }), /不能同时启用/)
    assert.equal(touched, false)
    ctx.get = () => ({ startedBundles: ['@stella/secretary'] })
    await assert.rejects(Plugin.prototype[Service.init].call({ ctx }), /不能同时启用/)
    assert.equal(touched, false)
    ctx.get = () => undefined
    await assert.rejects(Plugin.prototype[Service.init].call({ ctx }), /无法确认当前 Profile/)
    assert.equal(touched, false)
    ctx.get = () => ({ ...profile, startedBundles: ['@alcor/secretary'] })
    await assert.rejects(Plugin.prototype[Service.init].call({ ctx }), /storage touched/)
    assert.equal(touched, true)
    touched = false
    ctx.get = () => ({ ...profile, startedBundles: [] })
    await assert.rejects(Plugin.prototype[Service.init].call({ ctx }), /storage touched/)
    assert.equal(touched, true)
  }
})

test('AgentHub checks both secretary identities and retains legacy protection', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'alcor-hub-'))
  t.after(() => rm(directory, { recursive: true, force: true }))
  const anchor = join(directory, 'package.json')
  await writeFile(anchor, JSON.stringify({ name: 'fixture', private: true }))
  for (const scope of ['stella', 'alcor']) {
    const name = `@${scope}/secretary`, target = join(directory, 'node_modules', scope === 'stella' ? '@stella' : '@alcor', 'secretary')
    await mkdir(target, { recursive: true })
    const profile = { dir: directory, installAnchor: anchor, startedBundles: [name] }
    await writeFile(join(target, 'package.json'), JSON.stringify({ name }))
    await assert.rejects(assertLegacySecretaryDetached(profile), /支持独立 AgentHub/)
    if (scope === 'alcor') {
      await writeFile(join(target, 'package.json'), JSON.stringify({ name, stella: { agentHubDetached: true } }))
      await assert.rejects(assertLegacySecretaryDetached(profile), /支持独立 AgentHub/)
    }
    await writeFile(join(target, 'package.json'), JSON.stringify({ name, [scope]: { agentHubDetached: true } }))
    await assert.doesNotReject(assertLegacySecretaryDetached(profile))
  }
})

test('old package references and saved source selections remain usable', async () => {
  const saved = { source: 'elearning', accountId: 'student', tool: 'school', arguments: { course_ids: ['a'] } }
  const current = { ...saved, arguments: {} }
  assert.equal(canonicalPackage('@stella/elearning'), '@alcor/elearning')
  assert.equal(sourceOwner(saved), '@alcor/elearning')
  assert.deepEqual(effectiveBriefingSources([saved], [current])[0].arguments, saved.arguments)
  const cards = briefingSourceCards([saved], { sources: [current], connections: [{ package: '@stella/elearning', status: 'ready' }] })
  assert.equal(cards[0].package, '@stella/elearning')
  assert.equal(cards[0].connection, 'ready')
  const ctx = { tools: { definitions: () => [] }, get: name => name === 'profileContext' ? { startedBundles: ['@stella/mail'] } : name === 'alcorMailConnection' ? { state: async () => ({ configured: false }) } : undefined }
  const result = await discoverBriefingSources(ctx)
  assert.equal(result.connections[0].package, '@stella/mail')
})

test('each new plugin rejects an old selected peer before registration', async t => {
  const directory = await mkdtemp(join(root, '.alcor-test-'))
  t.after(() => rm(directory, { recursive: true, force: true }))
  const profile = await readyProfile(directory)
  for (const name of ['secretary', 'organizer', 'apple-sync', 'elearning', 'mail', 'agent-hub', 'privacy']) {
    const extension = name === 'privacy' ? 'js' : 'ts'
    const output = join(directory, `${name}.mjs`)
    await build({ entryPoints: [join(root, `packages/alcor-${name}/src/index.${extension}`)], outfile: output, bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24' })
    const plugin = await import(pathToFileURL(output))
    const oldName = name === 'organizer' ? 'secretary' : name
    let sideEffect = false
    const touched = () => { sideEffect = true; throw Error('registration touched') }
    const ctx = { get: () => ({ startedBundles: [`@stella/${oldName}`, `@alcor/${oldName}`] }), reflect: { provide: touched }, inject: touched }
    const invoke = () => plugin.apply ? plugin.apply(ctx) : new plugin.default(ctx, {})
    await assert.rejects(async () => invoke(), /不能同时启用/, name)
    assert.equal(sideEffect, false, name)
    ctx.get = () => undefined
    await assert.rejects(async () => invoke(), /无法确认当前 Profile/, name)
    assert.equal(sideEffect, false, name)
    if (name !== 'privacy') {
      ctx.get = () => ({ home: directory, dir: join(directory, 'uninitialized'), startedBundles: [`@alcor/${oldName}`] })
      await assert.rejects(async () => invoke(), /迁移尚未完成/, name)
      assert.equal(sideEffect, false, name)
    }
    ctx.get = () => ({ ...profile, startedBundles: [`@alcor/${oldName}`] })
    await assert.rejects(async () => invoke(), /registration touched/, name)
    assert.equal(sideEffect, true, name)
  }
})
