import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { Service } from '@deepseek-ai/cordis'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { dshHomePath } from '@deepseek-ai/dsh-home-paths'
import Elearning, { checkConnection } from '../../stella-secretary/src/elearning.js'
import { remote, schemas } from './remote.js'

class SchoolRemote extends TypertRemoteService {
  static inject = ['typert', 'stellaElearning']
  constructor(ctx) { super(ctx, 'stellaSchool', { namespace: 'stellaSchool' }) }
  async [Service.init]() { this.ctx.typert.register({ package: '@stella/elearning', face: 'host', schemas: [], invocations: remote.descriptors, model: { services: [], events: [], objects: [] } }) }
  @Remote elearningAccount(request) { schemas.elearningAccount[0].parse(request); return this.ctx.stellaElearning.state({ includeUsername: true }) }
  @Remote saveElearningAccount(request) { return this.ctx.stellaElearning.save(schemas.saveElearningAccount[0].parse(request)) }
  @Remote removeElearningAccount(request) { schemas.removeElearningAccount[0].parse(request); return this.ctx.stellaElearning.remove() }
  @Remote checkElearning(request) { schemas.checkElearning[0].parse(request); return checkConnection(this.ctx) }
}

export const inject = ['credentials', 'authorization', 'tools', 'typert']
export async function apply(ctx) {
  const directory = fileURLToPath(new URL('../server/', import.meta.url))
  const profile = ctx.get('profileContext')?.dir ?? dshHomePath('profiles', 'default')
  if (ctx.get('stellaElearning')) ctx.logger.warn('沿用已有的 eLearning 连接；如需使用随包服务，请停用旧配置后重启。')
  else await ctx.plugin(Elearning, { command: join(directory, 'elearning-mcp'), cwd: directory, dataDir: join(profile, 'stella', 'elearning'), args: [] }).await()
  await ctx.plugin(SchoolRemote).await()
}
