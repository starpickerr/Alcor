import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

for (const name of ['alcor-extension', 'alcor-launcher', 'alcor-secretary', 'alcor-apple-sync', 'alcor-privacy', 'alcor-organizer', 'alcor-elearning', 'alcor-mail', 'alcor-plugin-info']) {
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
