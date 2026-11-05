import { checkConnection } from './elearning.js'
import { Service } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { z as schema } from 'zod'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { defineDomain, domainTable } from '@deepseek-ai/dsh-storage-domain'
import { remote, overviewSchema, workspaceSchemas, mcpGrantSchema } from './remote.js'
import { workspaceStateSchema, initialWorkspaceState } from './workspace-state.js'
import { previewLegacyWorkspace, workspacePersona } from './workspace-import.js'

const domainSpec = defineDomain({
  name: 'stella_secretary', version: 1,
  tables: { installation: domainTable(schema.object({ installedAt: schema.string().datetime() }).strict()) },
})
const workspaceSpec = defineDomain({ name: 'stella_workspace', version: 1, tables: { settings: domainTable(workspaceStateSchema) } })
const mcpSpec = defineDomain({ name: 'stella_mcp', version: 1, tables: { grants: domainTable(mcpGrantSchema) } })

export default class Secretary extends TypertRemoteService {
  static inject = ['storageDomain', 'typert', 'tools']
  static Config = z.object({ displayName: z.string().min(1).max(80).default('Stella').volatile() })

  constructor(ctx, config) {
    super(ctx, 'stellaSecretary', { namespace: 'stellaSecretary' })
    this.config = config
  }

  async [Service.init]() {
    this.domain = await this.ctx.storageDomain.open(domainSpec)
    this.ctx.effect(() => () => this.domain.close())
    this.workspaceDomain = await this.ctx.storageDomain.open(workspaceSpec)
    if (!this.workspaceDomain.table('settings').get('current')) await this.workspaceDomain.table('settings').put('current', initialWorkspaceState)
    this.ctx.effect(() => () => this.workspaceDomain.close())
    this.mcpDomain = await this.ctx.storageDomain.open(mcpSpec)
    if (!this.mcpDomain.table('grants').get('current')) await this.mcpDomain.table('grants').put('current', { revision: 0, allowedTools: [] })
    this.ctx.effect(() => () => this.mcpDomain.close())
    const records = this.domain.table('installation')
    if (!records.get('product')) await records.put('product', { installedAt: new Date().toISOString() })
    this.ctx.typert.register({
      package: '@stella/secretary', face: 'host', schemas: [],
      invocations: remote.descriptors, model: { services: [], events: [], objects: [] },
    })
  }

  mcpAllowed(name) { return this.mcpDomain.table('grants').get('current').allowedTools.includes(name) }

  @Remote
  async elearningAccount(request) {
    workspaceSchemas.elearningAccount[0].parse(request)
    return this.ctx.get('stellaElearning')?.state({ includeUsername: true }) ?? { installed: false, configured: false, connected: false, writable: false }
  }

  @Remote
  async saveElearningAccount(request) {
    const service = this.ctx.get('stellaElearning')
    if (!service) throw new Error('请先在 DSH 中配置 eLearning 连接。')
    return service.save(request)
  }

  @Remote
  async removeElearningAccount(request) {
    workspaceSchemas.removeElearningAccount[0].parse(request)
    const service = this.ctx.get('stellaElearning')
    if (!service) throw new Error('eLearning 连接未配置。')
    return service.remove()
  }

  @Remote
  async checkElearning(request) {
    workspaceSchemas.checkElearning[0].parse(request)
    return checkConnection(this.ctx)
  }

  @Remote
  mcpState(request) {
    workspaceSchemas.mcpState[0].parse(request)
    return { ...this.mcpDomain.table('grants').get('current'), availableTools: this.ctx.tools.definitions().map(tool => tool.name).filter(name => /^mcp__[A-Za-z0-9_-]{1,32}__[A-Za-z0-9_-]+$/.test(name)).sort() }
  }

  @Remote
  async setMcpTools(request) {
    const input = workspaceSchemas.setMcpTools[0].parse(request)
    const current = this.mcpState({})
    if (input.allowedTools.some(name => !current.availableTools.includes(name) && !current.allowedTools.includes(name))) throw new Error('工具已断开，请刷新后重新选择。')
    const value = await this.mcpDomain.table('grants').update('current', saved => {
      if (saved.revision !== input.expectedRevision) throw new Error('授权已变化，请刷新后重试。')
      return { revision: saved.revision + 1, allowedTools: [...new Set(input.allowedTools)].sort() }
    })
    this.ctx.emit('stella/mcp-change')
    return value
  }

  @Remote
  workspaceState(request) {
    workspaceSchemas.workspaceState[0].parse(request)
    return this.workspaceDomain.table('settings').get('current')
  }

  private updateWorkspace(expectedRevision, transform) {
    return this.workspaceDomain.table('settings').update('current', current => {
      if (current.revision !== expectedRevision) throw new Error('设置已变化，请刷新后重试。')
      return workspaceStateSchema.parse({ ...transform(current), revision: current.revision + 1 })
    })
  }

  @Remote
  async previewWorkspace(request) {
    try { return await previewLegacyWorkspace(workspaceSchemas.previewWorkspace[0].parse(request)) }
    catch { throw new Error('无法预览：请核对目录、文件大小和读取权限；来源不能包含链接。') }
  }

  @Remote
  async importWorkspace(request) {
    const input = workspaceSchemas.importWorkspace[0].parse(request)
    const preview = await this.previewWorkspace(input.source)
    if (preview.fingerprint !== input.fingerprint) throw new Error('来源配置已变化，请重新预览后导入。')
    const files = preview.files.filter(file => input.names.includes(file.name))
    if (files.length !== new Set(input.names).size) throw new Error('选择的配置不存在，请重新预览。')
    return this.updateWorkspace(input.expectedRevision, current => {
      if (current.identity?.fingerprint === preview.fingerprint && JSON.stringify(current.identity.files) === JSON.stringify(files)) return current
      return { ...current, previousIdentity: current.identity, canRestoreIdentity: true, identity: { source: preview.source, fingerprint: preview.fingerprint, files, importedAt: new Date().toISOString() } }
    })
  }

  @Remote
  restoreIdentity(request) {
    const input = workspaceSchemas.restoreIdentity[0].parse(request)
    return this.updateWorkspace(input.expectedRevision, current => {
      if (!current.canRestoreIdentity) throw new Error('没有可恢复的上一版配置。')
      return { ...current, identity: current.previousIdentity, previousIdentity: null, canRestoreIdentity: false }
    })
  }

  identityPrompt(header, personalTurn) { return workspacePersona(this.workspaceDomain.table('settings').get('current'), header, personalTurn) }

  @Remote
  overview() {
    return overviewSchema.parse({
      displayName: this.config.displayName.get(),
      installedAt: this.domain.table('installation').get('product').installedAt,
      message: '准备好和你一起安排今天。',
    })
  }
}
