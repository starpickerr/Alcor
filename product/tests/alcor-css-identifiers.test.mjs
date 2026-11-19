import { packageDirectory } from './source-test-modules.mjs'
import { automationUiPlugin } from '../../scripts/product/automation-ui.mjs'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'

const root = new URL('../../', import.meta.url)
const read = path => readFile(new URL(path, root), 'utf8')

test('secretary style identifiers use Alcor while application identifiers stay stable', async () => {
  const directory = new URL('packages/alcor-secretary/src/', root)
  for (const name of await readdir(directory)) {
    if (!/\.(css|jsx)$/.test(name) || name.includes(' 2.')) continue
    const source = await readFile(new URL(name, directory), 'utf8')
    if (name.endsWith('.css')) assert.doesNotMatch(source, /\.stella-[\w-]+|--stella-[\w-]+|@container\s+stella-/g, name)
    else {
      assert.doesNotMatch(source, /(?:className|listClassName)=(?:"[^"]*|\{`[^`]*|\{[^}]*?)\bstella-[\w-]+/g, name)
      assert.doesNotMatch(source, /\.stella-[\w-]+|--stella-[\w-]+/g, name)
    }
  }
  const business = await read('packages/alcor-secretary/src/business.jsx')
  assert.match(business, /id: 'alcor-tasks'/)
  const calendar = await read('packages/alcor-secretary/src/calendar-page.jsx')
  assert.match(calendar, /querySelector\('\.alcor-hour-slot'\)/)
  assert.match(calendar, /--alcor-event-color/)
  assert.match(await read('packages/alcor-secretary/src/presentation.js'), /var\(--alcor-brand\)/)
})

test('shared consumers use the renamed styles', async () => {
  for (const [file, token] of [
    ['../dsh-email/plugin/src/client.jsx', 'alcor-mail-connection'],
    ['../dsh-email/plugin/src/style.css', '.alcor-mail-connection'],
    ['packages/alcor-plugin-info/src/source-settings.css', '.alcor-field'],
    ['../dsh-apple-sync/src/style.css', '--alcor-brand'],
    ['packages/alcor-plugin-info/src/notice.jsx', 'alcor-notice'],
    ['packages/alcor-plugin-info/src/notice.css', '.alcor-notice'],
  ]) assert.ok((await read(file)).includes(token), `${file}: ${token}`)
})

test('affected client entry points bundle', async () => {
  await build({ plugins: [automationUiPlugin()],
    entryPoints: ['alcor-secretary', 'alcor-mail', 'alcor-elearning', 'alcor-apple-sync']
      .map(name => fileURLToPath(new URL(`${packageDirectory(name.slice(6))}/src/client.jsx`, root))),
    bundle: true, packages: 'external', format: 'esm', platform: 'browser',
    loader: { '.css': 'text', '.svg': 'dataurl' }, outdir: '/tmp/alcor-css-test-output', write: false, logLevel: 'silent',
  })
})

test('eLearning source does not depend on secretary source', async () => {
  const directory = new URL('../dsh-fudan-elearning/plugin/src/', root)
  for (const name of await readdir(directory)) {
    if (!/\.(js|jsx|ts)$/.test(name)) continue
    assert.doesNotMatch(await readFile(new URL(name, directory), 'utf8'), /alcor-secretary\/src\//, name)
  }
})
