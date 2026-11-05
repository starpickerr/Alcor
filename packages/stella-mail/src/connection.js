import { randomUUID } from 'node:crypto'
import { Service } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { credentialKey } from '@deepseek-ai/dsh-credentials'
import * as McpClient from '@deepseek-ai/dsh-mcp-client'
import { credential, schemas } from './remote.js'

export const mailKey = credentialKey('stella-email', 'accounts')

export default class MailConnection extends Service {
  static inject = ['credentials', 'authorization', 'tools']
  static Config = z.object({ command: z.string().required(), cwd: z.string().required(), dataDir: z.string().required() })
  constructor(ctx, config) {
    super(ctx, 'stellaMailConnection')
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
      return decision
    })
    this.ctx.tools.guard(execution => execution.name.startsWith('mcp__email__') && !this.active ? '邮箱凭据已变化或连接未就绪。' : undefined)
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
          args: ['--data-dir', this.config.dataDir, '--dsh-credentials'], cwd: this.config.cwd,
          env: { STELLA_EMAIL_CREDENTIAL: JSON.stringify(parsed.data) }, toolCallTimeoutMs: 120000, failOnStartupError: true }))
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
    try {
      const batch = await this.ctx.tools.execute({ signal: AbortSignal.timeout(125000), calls: [{ type: 'tool-call', id: randomUUID(), name: 'mcp__email__test_connection', arguments: JSON.stringify({ payload: { id } }) }] })
      const result = batch.results[0].result
      const value = result.value?.structuredContent?.value
      return { status: !result.isError && value?.connected && value?.folder_accessible ? 'ready' : 'query_failed' }
    } catch { return { status: 'query_failed' } }
  }
}
