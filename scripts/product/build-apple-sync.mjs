import { execFileSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const repository = fileURLToPath(new URL('../../../dsh-apple-sync/', import.meta.url))
const destination = fileURLToPath(new URL('../../.local/artifacts/', import.meta.url))
mkdirSync(destination, { recursive: true })
execFileSync('npm', ['pack', '--pack-destination', destination], { cwd: repository, stdio: 'inherit' })
