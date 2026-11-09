import { registerBundledSkills } from '../../alcor-organizer/src/skills.js'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { Service } from '@deepseek-ai/cordis'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { dshHomePath } from '@deepseek-ai/dsh-home-paths'
import Elearning, { checkConnection } from '../../alcor-secretary/src/elearning.js'
import { remote, schemas } from './remote.js'
import { initializeProfile } from '../../alcor-organizer/src/profile-migration.js'

class SchoolRemote extends TypertRemoteService {
  static inject = ['typert', 'alcorElearning']
  constructor(ctx) { super(ctx, 'alcorSchool', { namespace: 'alcorSchool' }) }
  async [Service.init]() { this.ctx.typert.register({ package: '@alcor/elearning', face: 'host', schemas: [], invocations: remote.descriptors, model: { services: [], events: [], objects: [] } }) }
  @Remote elearningAccount(request) { schemas.elearningAccount[0].parse(request); return this.ctx.alcorElearning.state({ includeUsername: true }) }
  @Remote saveElearningAccount(request) { return this.ctx.alcorElearning.save(schemas.saveElearningAccount[0].parse(request)) }
  @Remote removeElearningAccount(request) { schemas.removeElearningAccount[0].parse(request); return this.ctx.alcorElearning.remove() }
  @Remote checkElearning(request) { schemas.checkElearning[0].parse(request); return checkConnection(this.ctx) }
}

export const inject = ['credentials', 'authorization', 'tools', 'typert']
export async function apply(ctx) {
  const selected = ctx.get('profileContext')?.startedBundles
  if (!Array.isArray(selected)) throw new Error('无法确认当前 Profile 的插件，已停止 Alcor 启动。')
  if (selected.includes('@stella/elearning')) throw new Error('旧版 Stella 插件与 Alcor 不能同时启用，请先迁移 Profile。')
  await initializeProfile(ctx)
  registerBundledSkills(ctx, 'alcor-elearning', new URL('../skills/', import.meta.url))
  const directory = fileURLToPath(new URL('../server/', import.meta.url))
  const profile = ctx.get('profileContext')?.dir ?? dshHomePath('profiles', 'default')
  if (ctx.get('alcorElearning')) ctx.logger.warn('沿用已有的 eLearning 连接；如需使用随包服务，请停用旧配置后重启。')
  else await ctx.plugin(Elearning, { command: join(directory, 'elearning-mcp'), cwd: directory, dataDir: join(profile, 'alcor', 'elearning'), args: [] }).await()
  await ctx.plugin(SchoolRemote).await()
}
