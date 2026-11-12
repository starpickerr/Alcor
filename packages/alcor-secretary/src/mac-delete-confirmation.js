import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

import { windowsConfirmationCommand } from './windows-confirmation.js'

// The pipe belongs to this one Host request. Its reply is never an API token.
export function confirmMacDeletion(request, signal) {
  if (!['darwin','win32'].includes(process.platform)) return Promise.reject(new Error('当前系统尚未提供页面删除确认窗口。'))
  signal.throwIfAborted()
  return new Promise((resolve, reject) => {
    const [command,args] = process.platform === 'win32' ? windowsConfirmationCommand() :
      [fileURLToPath(new URL('../native/Alcor Confirm.app/Contents/MacOS/AlcorConfirm', import.meta.url)), []]
    const child = spawn(command,args, {
      windowsHide:true,
      stdio: ['pipe', 'pipe', 'ignore'], signal, timeout: 245000,
    })
    let output = ''
    child.stdout.on('data', data => {
      output += data
      if (output.length > 4096) child.kill()
    })
    child.on('error', () => reject(new Error('删除确认窗口不可用或已取消，未执行。')))
    child.on('close', code => {
      if (code !== 0 || signal.aborted) return resolve(false)
      try {
        const value = JSON.parse(output)
        resolve(value.version === 1 && value.operation_id === request.operation_id && value.outcome === 'confirmed')
      } catch { resolve(false) }
    })
    child.stdin.on('error', () => {})
    child.stdin.end(JSON.stringify(request))
  })
}
