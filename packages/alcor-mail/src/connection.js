import { checkMessages } from './check-status.js'
import { randomUUID } from 'node:crypto'
import { Service } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { credentialKey } from '@deepseek-ai/dsh-credentials'
import * as McpClient from '@deepseek-ai/dsh-mcp-client'
import { credential, schemas } from './remote.js'

export const mailKey = credentialKey('alcor-email', 'accounts')

const accountCalls = new Set(['mcp__email__get_cursors', 'mcp__email__fetch_messages', 'mcp__email__list_folders', 'mcp__email__test_connection'])
function accountDenied(execution, record) {
  if (!accountCalls.has(execution.name)) return
  const args = execution.arguments
  const id = args?.account_id ?? args?.payload?.id
  if (!record?.payload?.accounts?.some(account => account.id === id && account.enabled)) return '邮箱未开启或已移除，请在邮件插件中开启。'
}

export default class MailConnection extends Service {
  static inject = ['credentials', 'authorization', 'tools']
  static Config = z.object({ command: z.string().required(), args: z.array(z.string()).default([]), cwd: z.string().required(), dataDir: z.string().required() })
  constructor(ctx, config) {
    super(ctx, 'alcorMailConnection')
    this.config = config; this.generation = 0; this.active = null; this.pending = Promise.resolve(); this.updates = Promise.resolve(); this.closed = false
  }
  async [Service.init]() {
    this.ctx.authorization.registerFlow({ key: mailKey, label: '邮件账号', methods: [{ id: 'settings', label: '邮箱与授权码' }], async run(session) {
      const value = await session.prompt({ kind: 'secret', message: '邮件账号设置' })
      await session.commit({ kind: 'grant', payload: credential.parse(JSON.parse(value)) })
    } })
    this.ctx.on('tools/pre-execute', async (execution, next) => {
      const decision = await next()
      if (!execution.name.startsWith('mcp__email__')) return decision
      const generation = this.generation
      const record = await this.ctx.credentials.readRecord(mailKey)
      if (generation !== this.generation || !this.active || JSON.stringify(record) !== this.active) return { kind: 'deny', reason: '邮箱凭据已变化或连接未就绪。' }
      const reason = accountDenied(execution, record)
      return reason ? { kind: 'deny', reason } : decision
    })
    this.ctx.tools.guard(execution => !execution.name.startsWith('mcp__email__') ? undefined : !this.active ? '邮箱凭据已变化或连接未就绪。' : accountDenied(execution, JSON.parse(this.active)))
    this.ctx.on('credentials/record-updated', key => { if (key === mailKey) return this.refresh() })
    this.ctx.effect(() => async () => { this.closed = true; this.generation++; this.active = null; await this.pending; await this.connection?.dispose() })
    await this.refresh().catch(() => undefined)
  }
  refresh() {
    const generation = ++this.generation
    this.active = null
    const operation = this.pending.then(async () => {
      await this.connection?.dispose(); this.connection = null
      if (this.closed || generation !== this.generation) return
      const record = await this.ctx.credentials.readRecord(mailKey)
      if (!record) return
      const parsed = record.kind === 'grant' && credential.safeParse(record.payload)
      if (!parsed?.success) throw new Error('邮箱凭据格式无效。')
      this.connection = this.ctx.plugin(async inner => {
        await McpClient.apply(inner, McpClient.Config({ transport: 'stdio', serverName: 'email', command: this.config.command,
          args: [...this.config.args, '--data-dir', this.config.dataDir, '--dsh-credentials'], cwd: this.config.cwd,
          env: { ALCOR_EMAIL_CREDENTIAL: JSON.stringify(parsed.data) }, toolCallTimeoutMs: 120000, failOnStartupError: true }))
      })
      await this.connection.await()
      if (!this.closed && generation === this.generation) this.active = JSON.stringify(record)
    })
    this.pending = operation.catch(() => { this.ctx.logger.warn('邮件连接未就绪，请检查插件设置。') })
    return operation
  }
  async state() {
    const info = await this.ctx.credentials.describeRecord(mailKey)
    const record = await this.ctx.credentials.readRecord(mailKey)
    const parsed = record?.kind === 'grant' && credential.safeParse(record.payload)
    return { installed: true, configured: !!parsed?.success, connected: !!this.active, writable: info.writable, revision: this.generation,
      accounts: parsed?.success ? parsed.data.accounts.map(({ password, ...row }) => row) : [] }
  }
  update(work) {
    const result = this.updates.then(work)
    this.updates = result.catch(() => undefined)
    return result
  }
  async current(expectedRevision) {
    if (this.closed || expectedRevision !== this.generation) throw new Error('邮箱设置已变化，请刷新后重试。')
    const record = await this.ctx.credentials.readRecord(mailKey)
    return record ? credential.parse(record.payload).accounts : []
  }
  save(request) {
    const input = schemas.save[0].parse(request)
    return this.update(async () => {
      const accounts = await this.current(input.expectedRevision)
      const existing = input.account.id && accounts.find(row => row.id === input.account.id)
      if (input.account.id && !existing) throw new Error('邮箱已移除，请刷新后重试。')
      const next = { ...input.account, id: existing?.id ?? randomUUID(), password: input.account.password || existing?.password || '' }
      const value = credential.parse({ accounts: [...accounts.filter(row => row.id !== next.id), next] })
      const identity = row => [row.host.toLowerCase(), row.user.toLowerCase(), row.port, row.folder].join('|')
      if (new Set(value.accounts.map(identity)).size !== value.accounts.length) throw new Error('此邮箱与文件夹已添加。')
      await this.ctx.authorization.begin({ key: mailKey, interaction: { notify() {}, prompt: async () => JSON.stringify(value) } })
      await this.pending
      return this.state()
    })
  }
  remove(request) {
    const input = schemas.removeAccount[0].parse(request)
    return this.update(async () => {
      const accounts = await this.current(input.expectedRevision)
      if (!accounts.some(row => row.id === input.id)) throw new Error('邮箱已移除，请刷新后重试。')
      const remaining = accounts.filter(row => row.id !== input.id)
      this.ctx.authorization.cancel(mailKey)
      if (remaining.length) await this.ctx.authorization.begin({ key: mailKey, interaction: { notify() {}, prompt: async () => JSON.stringify({ accounts: remaining }) } })
      else await this.ctx.credentials.deleteRecord(mailKey)
      await this.pending
      return this.state()
    })
  }
  async check(request) {
    const { id } = schemas.check[0].parse(request)
    if (!this.active) return { status: 'disconnected' }
    const generation = this.generation
    const signal = AbortSignal.timeout(35000)
    try {
      const batch = await this.ctx.tools.execute({ signal, calls: [{ type: 'tool-call', id: randomUUID(), name: 'mcp__email__test_connection', arguments: JSON.stringify({ payload: { id } }) }] })
      if (generation !== this.generation || !this.active) return { status: 'disconnected' }
      if (signal.aborted) return { status: 'query_failed', code: 'timeout' }
      const result = batch.results[0]?.result
      const value = result?.value?.structuredContent?.value
      if (!result?.isError && value?.connected === true && value?.folder_accessible === true) return { status: 'ready' }
      const code = value?.connected === true && value?.folder_accessible === false ? 'folder_denied' : Object.hasOwn(checkMessages, value?.error_code) ? value.error_code : 'query_failed'
      return { status: 'query_failed', code }
    } catch (error) {
      if (generation !== this.generation || !this.active) return { status: 'disconnected' }
      return { status: 'query_failed', code: signal.aborted || error?.name === 'TimeoutError' ? 'timeout' : 'query_failed' }
    }
  }
}
