import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { installCompatibility, verifyCompatibility, restoreCompatibility } from '../../scripts/product/client-compat.mjs'

async function fixture(context) {
  const root = await mkdtemp(join(tmpdir(), 'stella-client-compat-'))
  context.after(() => rm(root, { recursive: true, force: true }))
  const runtime = join(root, 'runtime')
  const source = join(root, 'source')
  for (const name of ['layout', 'workspace', 'settings-general', 'schedule']) {
    const directory = join(runtime, 'node_modules/@deepseek-ai/dsh-client-ui-' + name)
    await mkdir(join(directory, 'lib'), { recursive: true })
    await writeFile(join(directory, 'package.json'), JSON.stringify({ version: '0.2.2-alpha.3' }))
    await writeFile(join(directory, 'lib/client.js'), 'original-' + name)
    await mkdir(join(source, 'packages/client/ui-' + name, 'lib'), { recursive: true })
    await writeFile(join(source, 'packages/client/ui-' + name, 'lib/client.js'), name === 'layout' ? 'patched shell.top' : name === 'workspace' ? 'patched ui-workspace/default-preset workspace.browser' : name === 'settings-general' ? 'patched settings.shell' : 'patched schedule.manager.view')
  }
  await mkdir(join(runtime, 'node_modules/@deepseek-ai/dsh'), { recursive: true })
  await writeFile(join(runtime, 'node_modules/@deepseek-ai/dsh/package.json'), '{"version":"0.2.2-alpha.3"}')
  const target = name => join(runtime, 'node_modules/@deepseek-ai/dsh-client-ui-' + name, 'lib/client.js')
  return { runtime, source, target }
}

test('compatibility install is repeatable, detects altered assets, and restores originals', async context => {
  const { runtime, source, target } = await fixture(context)
  await installCompatibility(runtime, source, '0.2.2-alpha.3')
  await verifyCompatibility(runtime)
  await installCompatibility(runtime, source, '0.2.2-alpha.3')
  await restoreCompatibility(runtime)
  await verifyCompatibility(runtime)
  assert.equal(await readFile(target('layout'), 'utf8'), 'original-layout')
  assert.equal(await readFile(target('workspace'), 'utf8'), 'original-workspace')
  await installCompatibility(runtime, source, '0.2.2-alpha.3')
  await writeFile(target('layout'), 'unknown-owner-change')
  await assert.rejects(verifyCompatibility(runtime), /changed/)
  await assert.rejects(restoreCompatibility(runtime), /changed/)
  assert.equal(await readFile(target('layout'), 'utf8'), 'unknown-owner-change')
})

test('version or artifact mismatch fails before changing either client', async context => {
  const { runtime, source, target } = await fixture(context)
  await writeFile(join(runtime, 'node_modules/@deepseek-ai/dsh-client-ui-workspace/package.json'), '{"version":"0.2.2-rc.1"}')
  await assert.rejects(installCompatibility(runtime, source, '0.2.2-alpha.3'), /version/)
  assert.equal(await readFile(target('layout'), 'utf8'), 'original-layout')
  await writeFile(join(runtime, 'node_modules/@deepseek-ai/dsh-client-ui-workspace/package.json'), '{"version":"0.2.2-alpha.3"}')
  await writeFile(join(source, 'packages/client/ui-workspace/lib/client.js'), 'unpatched')
  await assert.rejects(installCompatibility(runtime, source, '0.2.2-alpha.3'), /artifact/)
  assert.equal(await readFile(target('layout'), 'utf8'), 'original-layout')
})

test('an interrupted installation blocks startup and can restore both originals', async context => {
  const { runtime, source, target } = await fixture(context)
  await installCompatibility(runtime, source, '0.2.2-alpha.3')
  const path = join(runtime, 'stella-client-compat/manifest.json')
  const manifest = JSON.parse(await readFile(path, 'utf8'))
  manifest.phase = 'installing'
  await writeFile(path, JSON.stringify(manifest))
  await writeFile(target('layout'), 'original-layout')
  await assert.rejects(verifyCompatibility(runtime), /interrupted/)
  await restoreCompatibility(runtime)
  assert.equal(await readFile(target('workspace'), 'utf8'), 'original-workspace')
  await writeFile(join(runtime, 'node_modules/@deepseek-ai/dsh/package.json'), '{"version":"0.2.2-rc.1"}')
  await assert.rejects(verifyCompatibility(runtime), /version/)
})

test('a reviewed client update replaces the patch and still restores original assets', async context => {
  const { runtime, source, target } = await fixture(context)
  await installCompatibility(runtime, source, '0.2.2-alpha.3')
  await writeFile(join(source, 'packages/client/ui-workspace/lib/client.js'), 'updated ui-workspace/default-preset workspace.browser')
  await installCompatibility(runtime, source, '0.2.2-alpha.3')
  assert.equal(await readFile(target('workspace'), 'utf8'), 'updated ui-workspace/default-preset workspace.browser')
  await verifyCompatibility(runtime)
  await restoreCompatibility(runtime)
  assert.equal(await readFile(target('workspace'), 'utf8'), 'original-workspace')
})


test('an older two-client installation gains native settings and automation and restores all originals', async context => {
  const { runtime, source, target } = await fixture(context)
  await installCompatibility(runtime, source, '0.2.2-alpha.3')
  const path = join(runtime, 'stella-client-compat/manifest.json')
  const manifest = JSON.parse(await readFile(path, 'utf8'))
  delete manifest.files['settings-general']
  delete manifest.files.schedule
  await writeFile(path, JSON.stringify(manifest))
  await writeFile(target('settings-general'), 'original-settings-general')
  await writeFile(target('schedule'), 'original-schedule')
  await verifyCompatibility(runtime)
  await installCompatibility(runtime, source, '0.2.2-alpha.3')
  assert.equal(await readFile(target('settings-general'), 'utf8'), 'patched settings.shell')
  await restoreCompatibility(runtime)
  for (const name of ['layout', 'workspace', 'settings-general', 'schedule']) assert.equal(await readFile(target(name), 'utf8'), 'original-' + name)
})
