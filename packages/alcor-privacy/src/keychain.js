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
      if (error) {
        const message = error.code === 'ENOENT' ? '[keychain_helper_missing] Keychain 组件缺失，请重新安装隐私插件。'
          : error.code === 'EACCES' ? '[keychain_helper_denied] 系统拒绝执行 Keychain 组件，请检查插件安装和系统执行权限。'
          : error.killed ? '[keychain_helper_timeout] Keychain 操作超时，请解锁系统钥匙串并核对保存状态后重试。'
          : '[keychain_helper_failed] Keychain 组件运行失败，请检查插件安装及系统钥匙串状态。'
        reject(new Error(message + ' 不会降级到文件或环境变量保存凭据。')); return
      }
      let result
      try { result = JSON.parse(stdout) }
      catch { reject(new Error('[keychain_protocol_error] Keychain 组件响应无效，请修复或更新隐私插件；不会使用其他凭据存储。')); return }
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
    child.stdin.on('error', () => { reject(new Error('[keychain_write_failed] 无法向 Keychain 组件发送请求，请核对保存状态后修复或重启插件。')) })
    child.stdin.end(JSON.stringify(request))
  })
}
