import { registerBundledSkills } from '../../alcor-organizer/src/skills.js'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { Service } from '@deepseek-ai/cordis'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { dshHomePath } from '@deepseek-ai/dsh-home-paths'
import MailConnection from './connection.js'
import { remote, schemas } from './remote.js'
import { initializeProfile } from '../../alcor-organizer/src/profile-migration.js'

class MailRemote extends TypertRemoteService {
  static inject = ['typert', 'alcorMailConnection']
  constructor(ctx) { super(ctx, 'alcorMail', { namespace: 'alcorMail' }) }
  async [Service.init]() { this.ctx.typert.register({ package: '@alcor/mail', face: 'host', schemas: [], invocations: remote.descriptors, model: { services: [], events: [], objects: [] } }) }
  @Remote state(request) { schemas.state[0].parse(request); return this.ctx.alcorMailConnection.state() }
  @Remote save(request) { return this.ctx.alcorMailConnection.save(request) }
  @Remote removeAccount(request) { return this.ctx.alcorMailConnection.remove(request) }
  @Remote check(request) { return this.ctx.alcorMailConnection.check(request) }
}
export const inject = ['credentials', 'authorization', 'tools', 'typert']
export async function apply(ctx) {
  const selected = ctx.get('profileContext')?.startedBundles
  if (!Array.isArray(selected)) throw new Error('无法确认当前 Profile 的插件，已停止 Alcor 启动。')
  if (selected.includes('@stella/mail')) throw new Error('旧版 Stella 插件与 Alcor 不能同时启用，请先迁移 Profile。')
  await initializeProfile(ctx)
  registerBundledSkills(ctx, 'alcor-mail', new URL('../skills/', import.meta.url))
  const directory = fileURLToPath(new URL('../server/', import.meta.url))
  const profile = ctx.get('profileContext')?.dir ?? dshHomePath('profiles', 'default')
  await ctx.plugin(MailConnection, { command: join(directory, 'email-mcp'), cwd: directory, dataDir: join(profile, 'alcor', 'email') }).await()
  await ctx.plugin(MailRemote).await()
}
