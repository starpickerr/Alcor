import {join} from 'node:path'

// The displayed detail is untrusted text. Read it from stdin, never as script.
const script = `
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
if ($request.version -ne 1 -or $request.operation_id -cnotmatch '^[a-f0-9]{64}$' -or
    $request.detail -isnot [string] -or !$request.detail.Length -or $request.detail.Length -gt 120000) { exit 1 }
Add-Type -AssemblyName System.Windows.Forms
[Windows.Forms.Application]::EnableVisualStyles()
$form = New-Object Windows.Forms.Form
$form.Text = 'Alcor 本次删除确认'
$form.ClientSize = New-Object Drawing.Size(640,400)
$form.StartPosition = 'CenterScreen'
$form.MinimizeBox = $false
$detail = New-Object Windows.Forms.TextBox
$detail.Multiline = $true
$detail.ReadOnly = $true
$detail.ScrollBars = 'Vertical'
$detail.Dock = 'Fill'
$detail.Text = $request.detail
$actions = New-Object Windows.Forms.FlowLayoutPanel
$actions.Dock = 'Bottom'
$actions.AutoSize = $true
$cancel = New-Object Windows.Forms.Button
$cancel.Text = '取消'
$cancel.DialogResult = 'Cancel'
$confirm = New-Object Windows.Forms.Button
$confirm.Text = '确认执行'
$confirm.DialogResult = 'OK'
$actions.Controls.Add($cancel)
$actions.Controls.Add($confirm)
$form.Controls.Add($detail)
$form.Controls.Add($actions)
$form.CancelButton = $cancel
$form.AcceptButton = $cancel
$form.ActiveControl = $cancel
$outcome = if ($form.ShowDialog() -eq 'OK') { 'confirmed' } else { 'cancelled' }
$form.Dispose()
@{version=1;operation_id=$request.operation_id;outcome=$outcome} | ConvertTo-Json -Compress
`

export function windowsConfirmationCommand() {
  return [join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe'),
    ['-NoProfile','-NonInteractive','-STA','-EncodedCommand',Buffer.from(script,'utf16le').toString('base64')]]
}
