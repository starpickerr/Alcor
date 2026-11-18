import { workerFailure } from './worker-diagnostic.js'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'
import { isAbsolute, toNamespacedPath } from 'node:path'
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
    const report = (message, level = 'warn', domain) => { try { diagnostic(message, level, domain) } catch { /* Diagnostics cannot fail a business request. */ } }
    this.diagnostic = report
    // Bundled Python libraries are signed App resources; imports must not rewrite their bytecode.
    this.process = spawn(python, ['-X', 'utf8', '-B', '-u', toNamespacedPath(fileURLToPath(worker)), toNamespacedPath(directory)], { windowsHide:true, stdio: ['pipe', 'pipe', 'pipe'] })
    const failed = error => {
      if (!this.closed) report(error.message, 'error')
      this.closed = true
      this.failure ??= error
      for (const pending of this.pending.values()) { clearTimeout(pending.timeout); pending.reject(this.failure) }
      this.pending.clear()
    }
    this.process.on('error', error => failed(new Error(`[worker_start_failed] 业务服务无法启动（${error.code === 'ENOENT' ? 'Python 不存在' : error.code === 'EACCES' ? '系统拒绝执行 Python' : '运行环境异常'}），请修复 DSH 运行环境或显式配置的 Python。`, { cause: error })))
    this.process.on('exit', (code, signal) => failed(new Error(`[worker_stopped] 业务服务已停止（${signal || code}），执行结果可能尚未返回，请刷新核对保存结果后重启插件，避免重复写入。`)))
    this.process.on('spawn', () => report('业务服务已启动。', 'info'))
    this.process.stderr.on('data', () => report('业务服务操作失败，请查看返回的具体原因。'))
    this.process.stdin.on('error', () => failed(new Error('[worker_write_failed] 无法向业务服务发送请求，请刷新核对结果后重启插件，避免重复写入。')))
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
        if (result.ok) {
          report(`[worker_operation_succeeded] ${pending.operation} 执行成功 · 耗时 ${Math.round(performance.now() - pending.startedAt)} ms · 请求 ${result.id}`, pending.logLevel, pending.domain)
          pending.resolve(result.value)
        }
        else {
          const expectedMissing = pending.expectedMissingReason && result.errorType === 'KeyError' && result.reason === pending.expectedMissingReason
          report(workerFailure(result, pending.operation, Math.round(performance.now() - pending.startedAt)), expectedMissing ? 'debug' : 'error', pending.domain)
          const error = new Error(operationMessage(result)); error.name = result.errorType || 'Error'; pending.reject(error)
        }
      } catch (error) { failed(new Error('[worker_protocol_error] 业务服务返回了无效响应，请核对插件版本和运行环境；先刷新核对保存结果，避免重复写入。', { cause: error })); this.process.kill('SIGTERM') }
    })
  }

  replyPeer(replyTo, result) {
    if (!this.closed) this.process.stdin.write(JSON.stringify({ replyTo, ...result }) + '\n', () => {})
  }

  call(action, input = {}) {
    if (this.closed) return Promise.reject(this.failure || new Error('[worker_stopped] 业务服务已停止，请重启插件后再操作。'))
    return new Promise((resolve, reject) => {
      const id = randomUUID()
      const safeName = value => typeof value === 'string' && /^[a-z_]{1,80}$/.test(value) ? value : 'unknown'
      const peer = ['apple_peer', 'bound_conflict_peer', 'item_restore_peer'].includes(action)
      const operation = action === 'invoke' || peer ? `${safeName(input.domain)}.${safeName(input.method)}` : action === 'briefing_run' ? 'briefing.' + safeName(input.method) : safeName(action)
      const domain = ['tasks', 'calendar', 'bills', 'briefing', 'automation', 'apple'].includes(input.domain) ? input.domain
        : ['briefing_run', 'local_briefing', 'generated_briefing', 'source_settings', 'source_group_settings', 'source_windows', 'source_cursors', 'local_source'].includes(action) ? 'briefing'
        : action === 'calendar_agenda' ? 'calendar' : action === 'due_reminders' ? 'tasks' : undefined
      const expectedMissingReason = action !== 'apple_peer' ? null
        : input.domain === 'calendar' && input.method === 'get_event' ? 'event not found'
        : input.domain === 'tasks' && input.method === 'get_todo' ? 'todo not found' : null
      const query = action === 'invoke' && /^(?:get_|list_|find_|has_|catalog$|categories$)/.test(input.method ?? '')
      const routine = query || peer || ['due_reminders', 'records', 'source_settings', 'source_group_settings', 'source_windows', 'source_cursors', 'calendar_agenda', 'local_source'].includes(action)
        || (action === 'briefing_run' && ['status', 'get', 'history', 'result', 'session'].includes(input.method))
      const timeout = setTimeout(() => {
        this.pending.delete(id)
        this.diagnostic(`[worker_timeout] ${operation} · 请求 ${id}`, 'warn', domain)
        reject(new Error('[worker_timeout] 业务操作超时，结果可能已保存，请刷新核对后重试。'))
      }, action === 'generated_briefing' ? 180000 : 200000)
      this.pending.set(id, { resolve, reject, timeout, operation, domain, expectedMissingReason, startedAt: performance.now(), logLevel: routine ? 'debug' : 'info' })
      this.process.stdin.write(JSON.stringify({ id, action, input }) + '\n', error => {
        if (error) { clearTimeout(timeout); this.pending.delete(id); reject(new Error('[worker_write_failed] 业务请求发送失败，请刷新核对结果后重启插件，避免重复写入。')) }
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
