import { Service } from '@deepseek-ai/cordis'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { dshHomePath } from '@deepseek-ai/dsh-home-paths'
import { join } from 'node:path'
import { BusinessWorker, resolveBusinessPython } from '../../stella-organizer/src/business.js'
import { remote, requestSchema } from './remote.js'

export default class AppleSync extends TypertRemoteService {
  static inject = ['typert', 'stellaOrganizer']
  private worker

  constructor(ctx) { super(ctx, 'stellaAppleSync', { namespace: 'stellaAppleSync' }) }

  async [Service.init]() {
    const profile = this.ctx.get('profileContext')?.dir ?? dshHomePath('profiles', 'default')
    this.worker = new BusinessWorker(join(profile, 'stella', 'apple-sync'), {
      worker: new URL('../python/worker.py', import.meta.url),
      python: await resolveBusinessPython(),
      onRequest: request => this.ctx.stellaOrganizer.applePeer(request),
      diagnostic: message => this.ctx.logger.warn(message),
    })
    this.ctx.effect(() => () => this.worker.close())
    await this.worker.call('initialize', { legacy_directory: join(profile, 'stella', 'business') })
    this.ctx.typert.register({ package: '@stella/apple-sync', face: 'host', schemas: [], invocations: remote.descriptors, model: { services: [], events: [], objects: [] } })
  }

  @Remote
  invoke(request) { return this.worker.call('invoke', requestSchema.parse(request)) }
}
