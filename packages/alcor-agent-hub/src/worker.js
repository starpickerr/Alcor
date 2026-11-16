import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { fileURLToPath } from 'node:url'
import { isAbsolute, toNamespacedPath } from 'node:path'
import { randomUUID } from 'node:crypto'
import { resolvePrimaryRuntime } from '@deepseek-ai/dsh-tool-workspace-dependencies'

const developmentPython = process.platform === 'win32' ? 'python' : 'python3'

function operationMessage(result) {
  if (['OperationalError', 'DatabaseError', 'IntegrityError', 'ProgrammingError', 'InterfaceError'].includes(result.errorType)) return '[storage_error] 本地数据库操作失败，请检查数据目录权限、磁盘空间和其他实例占用；保留数据库及备份，先核对保存结果，勿重复写入。'
  if (result.errorType === 'FileNotFoundError') return '[local_path_missing] 插件需要的本地文件或程序不存在，请检查插件安装、数据目录及配置路径。'
  if (result.errorType === 'OSError') return '[io_error] 插件读写或外部连接失败，请检查磁盘空间、系统目录权限及网络；先核对保存结果，勿重复执行。'
  return typeof result.error === 'string' && result.error.trim() ? result.error : '[worker_operation_failed] 插件操作失败但未提供原因，请查看插件日志，核对保存结果后再重试。'
}


function pythonOverride(environment) {
  const current = environment.ALCOR_BUSINESS_PYTHON
  const legacy = environment.STELLA_BUSINESS_PYTHON
  if (current && legacy && current !== legacy) throw new Error('Python 运行环境配置冲突，请只保留 Alcor 配置。')
  return current || legacy
}

export async function resolveHubPython(argv = process.argv, environment = process.env) {
  const override = pythonOverride(environment)
  if (override) return override
  if (!(argv[1] ?? '').replaceAll('\\', '/').endsWith('/@deepseek-ai/dsh-desktop-host/lib/index.js')) return developmentPython
  if (!argv[4] || !isAbsolute(argv[4])) throw new Error('DSH 桌面运行环境路径无效。')
  return (await resolvePrimaryRuntime(argv[4])).python
}
export class HubWorker {
  constructor(directory, { python = pythonOverride(process.env) || developmentPython, legacyDirectory = '', diagnostic = () => {} } = {}) {
    this.pending = new Map()
    this.closed = false
    this.process = spawn(python, ['-X', 'utf8', '-B', '-u', toNamespacedPath(fileURLToPath(new URL('../business/worker.py', import.meta.url))), toNamespacedPath(directory), legacyDirectory], { windowsHide:true, stdio: ['pipe', 'pipe', 'pipe'] })
    const failed = error => {
      this.closed = true
      this.failure ??= error
      for (const call of this.pending.values()) { clearTimeout(call.timer); call.reject(this.failure) }
      this.pending.clear()
    }
    this.process.on('error', error => failed(new Error(`[worker_start_failed] AgentHub 无法启动（${error.code === 'ENOENT' ? 'Python 不存在' : error.code === 'EACCES' ? '系统拒绝执行 Python' : '运行环境异常'}），请修复 DSH 运行环境或显式配置的 Python。`)))
    this.process.on('exit', (code, signal) => failed(new Error(`[worker_stopped] AgentHub 服务已停止（${signal || code}），请刷新核对任务结果后重启插件，避免重复执行。`)))
    this.process.stdin.on('error', () => failed(new Error('[worker_write_failed] AgentHub 请求发送失败，请刷新核对任务结果后重启插件，避免重复执行。')))
    this.process.stderr.on('data', () => diagnostic('AgentHub 操作失败，请查看返回的具体错误。'))
    createInterface({ input: this.process.stdout }).on('line', line => {
      try {
        const result = JSON.parse(line), call = this.pending.get(result.id)
        if (!call) return
        clearTimeout(call.timer); this.pending.delete(result.id)
        if (result.ok) call.resolve(result.value)
        else call.reject(new Error(operationMessage(result)))
      } catch { failed(new Error('[worker_protocol_error] AgentHub 返回了无效响应，请核对插件版本和运行环境；先刷新核对任务结果，避免重复执行。')); this.process.kill('SIGTERM') }
    })
  }
  call(action, input = {}) {
    if (this.closed) return Promise.reject(this.failure || new Error('[worker_stopped] AgentHub 服务已停用，请重新启用插件后再操作。'))
    return new Promise((resolve, reject) => {
      const id = randomUUID()
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('[worker_timeout] AgentHub 操作超时，请刷新核对，避免重复执行。')) }, 200000)
      this.pending.set(id, { resolve, reject, timer })
      this.process.stdin.write(JSON.stringify({ id, action, input }) + '\n', error => {
        if (error) { clearTimeout(timer); this.pending.delete(id); reject(new Error('[worker_write_failed] AgentHub 请求发送失败，请刷新核对任务结果后重启插件，避免重复执行。')) }
      })
    })
  }
  async close() {
    this.closed = true
    if (this.process.exitCode !== null || this.process.signalCode !== null) return
    this.process.stdin.end()
    await new Promise(resolve => {
      const timer = setTimeout(() => this.process.kill('SIGKILL'), 5000)
      this.process.once('exit', () => { clearTimeout(timer); resolve() })
    })
  }
}
