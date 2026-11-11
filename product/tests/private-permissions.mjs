import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { stat } from 'node:fs/promises'
import { join } from 'node:path'

export async function assertPrivatePermissions(filename, mode) {
  if (process.platform !== 'win32') {
    assert.equal((await stat(filename)).mode & 0o777, mode)
    return
  }
  const system = join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0')
  const command = `$ErrorActionPreference='Stop'
    $acl=Get-Acl -LiteralPath $env:ALCOR_TEST_PATH
    $readers=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | Where-Object {
      $_.IdentityReference.Value -in @('S-1-1-0','S-1-5-11','S-1-5-32-545') -and
      $_.AccessControlType -eq 'Allow' -and ($_.FileSystemRights -band 1)
    })
    $readers.Count`
  const count = execFileSync(join(system, 'powershell.exe'), ['-NonInteractive', '-NoProfile', '-Command', command], {
    env: { ...process.env, PSModulePath: join(system, 'Modules'), ALCOR_TEST_PATH: filename },
    encoding: 'utf8', windowsHide: true,
  })
  assert.equal(count.trim(), '0', 'Private data must not grant public read access')
}
