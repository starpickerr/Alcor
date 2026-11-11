import {execFile} from 'node:child_process'
import {join} from 'node:path'
import {promisify} from 'node:util'

const execute = promisify(execFile)

// Keep paths and process identifiers out of PowerShell source.
export async function windowsQuery(script, variables = {}) {
  const executable = join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe')
  const {stdout} = await execute(executable, ['-NoProfile', '-NonInteractive', '-Command',
    "$ErrorActionPreference='Stop';[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);" + script],
  {windowsHide:true, env:{...process.env, PSModulePath:join(process.env.SystemRoot,'System32/WindowsPowerShell/v1.0/Modules'), ...variables}, maxBuffer:16*1024*1024})
  return JSON.parse(stdout)
}

export function windowsProcesses() {
  return windowsQuery(`$rows = @(Get-CimInstance Win32_Process | Where-Object CreationDate | ForEach-Object {
    @{pid=$_.ProcessId; parent=$_.ParentProcessId; identity=$_.CreationDate.ToUniversalTime().Ticks.ToString()}
  }); ConvertTo-Json -InputObject $rows -Compress`)
}

export function windowsDesktopIdentity(binary, archive) {
  return windowsQuery(`$exe = Get-Item -LiteralPath $env:ALCOR_VERIFY_EXE
    $signature = Get-AuthenticodeSignature -LiteralPath $exe.FullName
    @{version=$exe.VersionInfo.FileVersion; status=$signature.Status.ToString(); signer=$signature.SignerCertificate.Thumbprint;
      binary=(Get-FileHash -LiteralPath $exe.FullName).Hash; archive=(Get-FileHash -LiteralPath $env:ALCOR_VERIFY_ARCHIVE).Hash} | ConvertTo-Json`,
  {ALCOR_VERIFY_EXE:binary, ALCOR_VERIFY_ARCHIVE:archive})
}
