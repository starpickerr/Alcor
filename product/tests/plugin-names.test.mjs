import { packageDirectory } from './source-test-modules.mjs'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { readPluginMeta } from '@deepseek-ai/dsh-app-boot'

for (const [name, title] of [
  ['secretary', 'Alcor'],
  ['elearning', 'dsh-fudan-elearning'],
  ['mail', 'dsh-email'],
]) {
  test(`${name} displays its GitHub project name through native DSH metadata`, async () => {
    const path = new URL(`../../${packageDirectory(name)}/package.json`, import.meta.url)
    const meta = readPluginMeta(`@alcor/${name}`, path.href)
    assert.equal(meta?.error, undefined)
    assert.equal(typeof meta?.title === 'string' ? meta.title : meta?.title?.en, title)
    const manifest = JSON.parse(await readFile(path))
    assert.equal(manifest.name, `@alcor/${name}`, 'Existing installation identities must remain compatible')
    assert.equal(manifest.repository.url, `git+https://github.com/starpickerr/${title}.git`)
    assert.equal(manifest.repository.directory, name === 'secretary' ? 'packages/alcor-secretary' : 'plugin')
    assert(manifest.files.includes('locale/*.json'))
  })
}
