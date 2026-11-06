import { registerBundledSkills } from '../../stella-organizer/src/skills.js'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { Service } from '@deepseek-ai/cordis'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { dshHomePath } from '@deepseek-ai/dsh-home-paths'
import MailConnection from './connection.js'
import { remote, schemas } from './remote.js'

class MailRemote extends TypertRemoteService {
  static inject = ['typert', 'stellaMailConnection']
  constructor(ctx) { super(ctx, 'stellaMail', { namespace: 'stellaMail' }) }
  async [Service.init]() { this.ctx.typert.register({ package: '@stella/mail', face: 'host', schemas: [], invocations: remote.descriptors, model: { services: [], events: [], objects: [] } }) }
  @Remote state(request) { schemas.state[0].parse(request); return this.ctx.stellaMailConnection.state() }
  @Remote save(request) { return this.ctx.stellaMailConnection.save(request) }
  @Remote removeAccount(request) { return this.ctx.stellaMailConnection.remove(request) }
  @Remote check(request) { return this.ctx.stellaMailConnection.check(request) }
}
export const inject = ['credentials', 'authorization', 'tools', 'typert']
export async function apply(ctx) {
  registerBundledSkills(ctx, 'stella-mail', new URL('../skills/', import.meta.url))
  const directory = fileURLToPath(new URL('../server/', import.meta.url))
  const profile = ctx.get('profileContext')?.dir ?? dshHomePath('profiles', 'default')
  await ctx.plugin(MailConnection, { command: join(directory, 'email-mcp'), cwd: directory, dataDir: join(profile, 'stella', 'email') }).await()
  await ctx.plugin(MailRemote).await()
}
