import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const destination = fileURLToPath(new URL('../../.local/artifacts/', import.meta.url))
mkdirSync(destination, { recursive: true })
const { sources } = JSON.parse(readFileSync(new URL('../../product/source-releases.lock.json', import.meta.url), 'utf8'))
for (const { repo, version, sourceSha256 } of sources) {
  const directory = fileURLToPath(new URL(`../../../${repo}/plugin/`, import.meta.url))
  const source = JSON.parse(readFileSync(join(directory, 'source-lock.json'), 'utf8'))
  if (source.repo !== repo || source.version !== version || source.sourceSha256 !== sourceSha256) {
    throw new Error(`${repo} differs from Alcor's verified MCP source.`)
  }
  execFileSync('npm', ['pack', '--pack-destination', destination], { cwd: directory, stdio: 'inherit' })
}
