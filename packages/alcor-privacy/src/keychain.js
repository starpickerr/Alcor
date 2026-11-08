import { execFile } from 'node:child_process'
import { fileURLToPath } from 'node:url'

export function keychainRequest(request) {
  if (process.platform !== 'darwin') return Promise.reject(new Error('macOS Keychain is required.'))
  return new Promise((resolve, reject) => {
    const child = execFile(fileURLToPath(new URL('../native/keychain-bridge', import.meta.url)), [], {
      timeout: 10_000,
      maxBuffer: 2_097_152,
      env: {},
    }, (error, stdout) => {
      if (error) { reject(new Error('Keychain helper failed; no file or environment fallback is used.')); return }
      let result
      try { result = JSON.parse(stdout) }
      catch { reject(new Error('Keychain helper returned an invalid response.')); return }
      if (result.ok !== true) {
        reject(new Error(`Keychain unavailable (status ${Number.isInteger(result.status) ? result.status : 'unknown'}); unlock or repair it through macOS.`))
        return
      }
      if (result.value !== undefined && typeof result.value !== 'string') {
        reject(new Error('Keychain helper returned an invalid value.'))
        return
      }
      resolve(result.value)
    })
    child.stdin.on('error', () => { reject(new Error('Keychain helper input failed.')) })
    child.stdin.end(JSON.stringify(request))
  })
}
