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
    ['packages/alcor-mail/src/client.jsx', 'alcor-mail-connection'],
    ['packages/alcor-mail/src/style.css', '.alcor-mail-connection'],
    ['packages/alcor-elearning/src/settings.css', '.alcor-field'],
    ['packages/alcor-apple-sync/src/style.css', '--alcor-brand'],
    ['packages/alcor-agent-hub/src/agent-hub.jsx', 'alcor-agenthub'],
    ['packages/alcor-agent-hub/src/agent-hub.css', '.alcor-agenthub'],
    ['packages/alcor-secretary/src/notice.jsx', 'alcor-notice'],
    ['packages/alcor-secretary/src/notice.css', '.alcor-notice'],
  ]) assert.ok((await read(file)).includes(token), `${file}: ${token}`)
})

test('affected client entry points bundle', async () => {
  await build({ plugins: [automationUiPlugin()],
    entryPoints: ['alcor-secretary', 'alcor-mail', 'alcor-elearning', 'alcor-apple-sync', 'alcor-agent-hub']
      .map(name => fileURLToPath(new URL(`packages/${name}/src/client.jsx`, root))),
    bundle: true, packages: 'external', format: 'esm', platform: 'browser',
    loader: { '.css': 'text', '.svg': 'dataurl' }, outdir: '/tmp/alcor-css-test-output', write: false, logLevel: 'silent',
  })
})
