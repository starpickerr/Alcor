import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { fileURLToPath } from 'node:url'
import { isAbsolute } from 'node:path'
import { randomUUID } from 'node:crypto'
import { resolvePrimaryRuntime } from '@deepseek-ai/dsh-tool-workspace-dependencies'

function pythonOverride(environment) {
  const current = environment.ALCOR_BUSINESS_PYTHON
  const legacy = environment.STELLA_BUSINESS_PYTHON
  if (current && legacy && current !== legacy) throw new Error('Python 运行环境配置冲突，请只保留 Alcor 配置。')
  return current || legacy
}

export async function resolveHubPython(argv = process.argv, environment = process.env) {
  const override = pythonOverride(environment)
  if (override) return override
  if (!(argv[1] ?? '').replaceAll('\\', '/').endsWith('/@deepseek-ai/dsh-desktop-host/lib/index.js')) return 'python3'
  if (!argv[4] || !isAbsolute(argv[4])) throw new Error('DSH 桌面运行环境路径无效。')
  return (await resolvePrimaryRuntime(argv[4])).python
}
export class HubWorker {
  constructor(directory, { python = pythonOverride(process.env) || 'python3', legacyDirectory = '', diagnostic = () => {} } = {}) {
    this.pending = new Map()
    this.closed = false
    this.process = spawn(python, ['-B', '-u', fileURLToPath(new URL('../business/worker.py', import.meta.url)), directory, legacyDirectory], { stdio: ['pipe', 'pipe', 'pipe'] })
    const failed = () => {
      this.closed = true
      for (const call of this.pending.values()) { clearTimeout(call.timer); call.reject(new Error('AgentHub 服务已停止，请刷新核对任务结果。')) }
      this.pending.clear()
    }
    this.process.on('error', failed); this.process.on('exit', failed); this.process.stdin.on('error', failed)
    this.process.stderr.on('data', () => diagnostic('AgentHub 操作失败，请查看返回的具体错误。'))
    createInterface({ input: this.process.stdout }).on('line', line => {
      try {
        const result = JSON.parse(line), call = this.pending.get(result.id)
        if (!call) return
        clearTimeout(call.timer); this.pending.delete(result.id)
        if (result.ok) call.resolve(result.value)
        else call.reject(new Error(result.error))
      } catch { failed(); this.process.kill('SIGTERM') }
    })
  }
  call(action, input = {}) {
    if (this.closed) return Promise.reject(new Error('AgentHub 服务已停用。'))
    return new Promise((resolve, reject) => {
      const id = randomUUID()
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('AgentHub 操作超时，请刷新核对，避免重复执行。')) }, 200000)
      this.pending.set(id, { resolve, reject, timer })
      this.process.stdin.write(JSON.stringify({ id, action, input }) + '\n', error => {
        if (error) { clearTimeout(timer); this.pending.delete(id); reject(error) }
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
