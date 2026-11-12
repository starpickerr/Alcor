import { execFileSync } from 'node:child_process'
import { copyFile, mkdir, mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { signAndCopyHelper } from './sign-helper.mjs'

// This core helper only confirms a concrete operation; Apple remains optional.
if (process.platform === 'darwin') {
  const source = fileURLToPath(new URL('../../packages/alcor-secretary/native/', import.meta.url))
  const temporary = await mkdtemp(join(tmpdir(), 'alcor-confirm-build-'))
  const app = join(temporary, 'Alcor Confirm.app')
  const executable = join(app, 'Contents/MacOS/AlcorConfirm')
  try {
    await mkdir(join(app, 'Contents/MacOS'), { recursive: true })
    await copyFile(join(source, 'Info.plist'), join(app, 'Contents/Info.plist'))
    await copyFile(join(source, 'Confirm.swift'), join(temporary, 'Confirm.swift'))
    const sdk = execFileSync('/usr/bin/xcrun', ['--sdk', 'macosx', '--show-sdk-path'], { encoding: 'utf8' }).trim()
    for (const arch of ['arm64', 'x86_64']) execFileSync('/usr/bin/swiftc', ['-O', '-sdk', sdk, '-target', `${arch}-apple-macosx13.0`,
      join(temporary, 'Confirm.swift'), '-o', join(temporary, arch)], { stdio: 'inherit' })
    execFileSync('/usr/bin/lipo', ['-create', join(temporary, 'arm64'), join(temporary, 'x86_64'), '-output', executable], { stdio: 'inherit' })
    await signAndCopyHelper(app, join(source, 'Alcor Confirm.app'))
  } finally { await rm(temporary, { recursive: true, force: true }) }
}
