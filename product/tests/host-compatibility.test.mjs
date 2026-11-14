import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

for (const name of ['alcor-extension', 'alcor-launcher', 'alcor-secretary', 'alcor-apple-sync', 'alcor-privacy', 'alcor-organizer', 'alcor-elearning', 'alcor-mail', 'alcor-agent-hub', 'alcor-plugin-info']) {
  test(`${name} cannot install or shadow host-owned DSH components`, async () => {
    const manifest = JSON.parse(await readFile(new URL(`../../packages/${name}/package.json`, import.meta.url)))
    for (const dependency of Object.keys(manifest.dependencies || {})) assert(!dependency.startsWith('@deepseek-ai/'), `${dependency} must come from the running host`)
    for (const dependency of Object.keys(manifest.peerDependencies || {})) assert.equal(manifest.peerDependenciesMeta?.[dependency]?.optional, true, `${dependency} must not be auto-installed by the package manager`)
    assert(manifest.engines.dsh, 'UI overrides must have an explicit supported host range')
    const release = JSON.parse(await readFile(new URL('../desktop-release.lock.json', import.meta.url)))
    const versions = [release, ...release.compatibleReleases].map(item => item.version)
    for (const version of manifest.engines.dsh.split(' || ')) assert(versions.includes(version), 'Support must name a verified official release; open-ended ranges are unsafe for UI overrides')
  })
}

test('shared secretary and organizer name the verified rc.2 release explicitly', async () => {
  for (const name of ['alcor-secretary','alcor-organizer']) {
    const manifest=JSON.parse(await readFile(new URL(`../../packages/${name}/package.json`,import.meta.url)))
    assert(manifest.engines.dsh.split(' || ').includes('0.2.2-rc.2'))
    assert(!/[~^*><]/.test(manifest.engines.dsh))
  }
})

test('current Alcor packages explicitly support the verified rc.3 host', async () => {
  for (const name of ['secretary', 'organizer', 'apple-sync', 'elearning', 'mail', 'agent-hub']) {
    const manifest = JSON.parse(await readFile(new URL(`../../packages/alcor-${name}/package.json`, import.meta.url)))
    assert(manifest.engines.dsh.split(' || ').includes('0.2.2-rc.3'), `${name} must run after the official host update`)
    assert(!/[~^*><]/.test(manifest.engines.dsh))
  }
})
