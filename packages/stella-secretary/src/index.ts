import { checkConnection } from './elearning.js'
import { Service } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { z as schema } from 'zod'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { defineDomain, domainTable } from '@deepseek-ai/dsh-storage-domain'
import { remote, overviewSchema, accountSchemas } from './remote.js'

const domainSpec = defineDomain({
  name: 'stella_secretary', version: 1,
  tables: { installation: domainTable(schema.object({ installedAt: schema.string().datetime() }).strict()) },
})

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
    const records = this.domain.table('installation')
    if (!records.get('product')) await records.put('product', { installedAt: new Date().toISOString() })
    this.ctx.typert.register({
      package: '@stella/secretary', face: 'host', schemas: [],
      invocations: remote.descriptors, model: { services: [], events: [], objects: [] },
    })
  }


  @Remote
  async elearningAccount(request) {
    accountSchemas.elearningAccount[0].parse(request)
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
    accountSchemas.removeElearningAccount[0].parse(request)
    const service = this.ctx.get('stellaElearning')
    if (!service) throw new Error('eLearning 连接未配置。')
    return service.remove()
  }

  @Remote
  async checkElearning(request) {
    accountSchemas.checkElearning[0].parse(request)
    return checkConnection(this.ctx)
  }

  @Remote
  overview() {
    return overviewSchema.parse({
      displayName: this.config.displayName.get(),
      installedAt: this.domain.table('installation').get('product').installedAt,
      message: '准备好和你一起安排今天。',
    })
  }
}
