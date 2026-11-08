import { execFileSync } from 'node:child_process'
import { mkdir, mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'

if (process.platform !== 'darwin') throw new Error('Building the Keychain provider requires macOS.')
const root = fileURLToPath(new URL('../../', import.meta.url))
const directory = resolve(root, 'packages/alcor-privacy')
const temporary = await mkdtemp(resolve(tmpdir(), 'alcor-keychain-build-'))
try {
  const sdk = execFileSync('/usr/bin/xcrun', ['--sdk', 'macosx', '--show-sdk-path'], { encoding: 'utf8' }).trim()
  for (const architecture of ['arm64', 'x86_64']) {
    execFileSync('/usr/bin/swiftc', ['-O', '-sdk', sdk, '-target', `${architecture}-apple-macosx13.0`,
      resolve(directory, 'native/Keychain.swift'), '-o', resolve(temporary, architecture)], { stdio: 'inherit' })
  }
  execFileSync('/usr/bin/lipo', ['-create', resolve(temporary, 'arm64'), resolve(temporary, 'x86_64'),
    '-output', resolve(directory, 'native/keychain-bridge')], { stdio: 'inherit' })
  execFileSync('/usr/bin/codesign', ['--force', '--sign', '-', resolve(directory, 'native/keychain-bridge')], { stdio: 'inherit' })
  await build({ entryPoints: [resolve(directory, 'src/index.js')], outdir: resolve(directory, 'lib'),
    bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24' })
  await mkdir(resolve(root, '.local/artifacts'), { recursive: true })
} finally {
  await rm(temporary, { recursive: true, force: true })
}
