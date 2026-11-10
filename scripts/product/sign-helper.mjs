import { execFileSync } from 'node:child_process'
import { cp, rm } from 'node:fs/promises'

export async function signAndCopyHelper(stagedApp, destination) {
  // Finder can reattach metadata in a synced workspace while codesign is running.
  execFileSync('/usr/bin/xattr', ['-cr', stagedApp], { stdio: 'inherit' })
  execFileSync('/usr/bin/codesign', ['--force', '--sign', '-', stagedApp], { stdio: 'inherit' })
  execFileSync('/usr/bin/codesign', ['--verify', '--strict', stagedApp], { stdio: 'inherit' })
  await rm(destination, { recursive: true, force: true })
  await cp(stagedApp, destination, { recursive: true })
}
