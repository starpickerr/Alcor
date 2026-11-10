import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { readPluginMeta } from '@deepseek-ai/dsh-app-boot'

for (const [name, title] of [
  ['secretary', 'Alcor'],
  ['agent-hub', 'dsh-agent-hub'],
  ['elearning', 'dsh-fudan-elearning'],
  ['mail', 'dsh-email'],
]) {
  test(`${name} displays its GitHub project name through native DSH metadata`, async () => {
    const meta = readPluginMeta(`@alcor/${name}`, new URL('../../package.json', import.meta.url).href)
    assert.equal(meta?.error, undefined)
    assert.equal(typeof meta?.title === 'string' ? meta.title : meta?.title?.en, title)
    const manifest = JSON.parse(await readFile(new URL(`../../packages/alcor-${name}/package.json`, import.meta.url)))
    assert.equal(manifest.name, `@alcor/${name}`, 'Existing installation identities must remain compatible')
    assert.equal(manifest.repository.url, 'git+https://github.com/starpickerr/Alcor.git')
    assert.equal(manifest.repository.directory, `packages/alcor-${name}`)
    assert(manifest.files.includes('locale/*.json'))
  })
}
