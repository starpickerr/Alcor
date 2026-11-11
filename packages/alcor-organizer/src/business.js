import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'
import { isAbsolute, toNamespacedPath } from 'node:path'
import { resolvePrimaryRuntime } from '@deepseek-ai/dsh-tool-workspace-dependencies'

const developmentPython = process.platform === 'win32' ? 'python' : 'python3'

function pythonOverride(environment) {
  const current = environment.ALCOR_BUSINESS_PYTHON
  const legacy = environment.STELLA_BUSINESS_PYTHON
  if (current && legacy && current !== legacy) throw new Error('Python 运行环境配置冲突，请只保留 Alcor 配置。')
  return current || legacy
}

export async function resolveBusinessPython(argv = process.argv, environment = process.env) {
  const override = pythonOverride(environment)
  if (override) return override
  if (!(argv[1] ?? '').replaceAll('\\', '/').endsWith('/@deepseek-ai/dsh-desktop-host/lib/index.js')) return developmentPython
  // The supported official Desktop passes its bundled payload explicitly to this Host.
  if (!argv[4] || !isAbsolute(argv[4])) throw new Error('DSH 桌面运行环境路径无效，请重新安装受支持的官方客户端。')
  return (await resolvePrimaryRuntime(argv[4])).python
}

export class BusinessWorker {
  constructor(directory, { python = pythonOverride(process.env) || developmentPython, worker = new URL('../business/worker.py', import.meta.url), diagnostic = () => {}, onRequest } = {}) {
    this.pending = new Map()
    this.closed = false
    // Bundled Python libraries are signed App resources; imports must not rewrite their bytecode.
    this.process = spawn(python, ['-X', 'utf8', '-B', '-u', toNamespacedPath(fileURLToPath(worker)), toNamespacedPath(directory)], { windowsHide:true, stdio: ['pipe', 'pipe', 'pipe'] })
    const failed = error => {
      this.closed = true
      for (const pending of this.pending.values()) { clearTimeout(pending.timeout); pending.reject(error) }
      this.pending.clear()
    }
    this.process.on('error', error => failed(new Error('业务服务无法启动，请核对 DSH 运行环境。', { cause: error })))
    this.process.on('exit', () => failed(new Error('业务服务已停止，执行结果可能尚未返回，请刷新核对。')))
    this.process.stderr.on('data', () => diagnostic('业务服务操作失败，请查看返回的具体原因。'))
    this.process.stdin.on('error', error => failed(error))
    createInterface({ input: this.process.stdout }).on('line', line => {
      try {
        const result = JSON.parse(line)
        if (result.type === 'peer') {
          Promise.resolve().then(() => {
            if (!onRequest) throw new Error('业务连接不可用。')
            return onRequest(result.input)
          }).then(value => this.replyPeer(result.id, { ok: true, value }), error => this.replyPeer(result.id, { ok: false, error: error.message, errorType: error.name }))
          return
        }
        const pending = this.pending.get(result.id)
        if (!pending) return
        this.pending.delete(result.id)
        clearTimeout(pending.timeout)
        if (result.ok) pending.resolve(result.value)
        else { const error = new Error(result.error); error.name = result.errorType || 'Error'; pending.reject(error) }
      } catch (error) { failed(new Error('业务服务响应无效。', { cause: error })) }
    })
  }

  replyPeer(replyTo, result) {
    if (!this.closed) this.process.stdin.write(JSON.stringify({ replyTo, ...result }) + '\n', () => {})
  }

  call(action, input = {}) {
    if (this.closed) return Promise.reject(new Error('业务服务已停止。'))
    return new Promise((resolve, reject) => {
      const id = randomUUID()
      const timeout = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error('业务操作超时，结果可能已保存，请刷新核对后重试。'))
      }, action === 'generated_briefing' ? 180000 : 200000)
      this.pending.set(id, { resolve, reject, timeout })
      this.process.stdin.write(JSON.stringify({ id, action, input }) + '\n', error => {
        if (error) { clearTimeout(timeout); this.pending.delete(id); reject(error) }
      })
    })
  }

  async close() {
    if (this.process.exitCode !== null || this.process.signalCode !== null) return
    this.closed = true
    this.process.stdin.end()
    await new Promise(resolve => {
      const timeout = setTimeout(() => this.process.kill('SIGKILL'), 5000)
      this.process.once('exit', () => { clearTimeout(timeout); resolve() })
    })
  }
}
