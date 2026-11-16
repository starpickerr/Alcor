import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile, stat } from 'node:fs/promises'

for (const name of ['secretary', 'apple-sync', 'agent-hub', 'elearning', 'mail']) {
  test(`${name} exposes plugin artwork and a nonempty description to the host`, async () => {
    const root = new URL(`../../packages/alcor-${name}/`, import.meta.url)
    const manifest = JSON.parse(await readFile(new URL('package.json', root)))
    assert(manifest.description?.trim())
    assert.equal(manifest.exports['./package.json'], './package.json')
    assert(manifest.icon && manifest.files.includes(manifest.icon.replace('./', '')))
    const icon = new URL(manifest.icon, root)
    assert((await stat(icon)).size < 256 * 1024)
    assert.match(await readFile(icon, 'utf8'), /<svg[\s>]/)
  })
}
