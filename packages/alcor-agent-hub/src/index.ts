import {sessionMode,permits,selectedScope} from '../../alcor-plugin-info/src/assignments.js'
import {modeConfig} from '../../alcor-plugin-info/src/host-policy.js'
import { Service } from '@deepseek-ai/cordis'
import { z } from 'zod'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { defineDomain, domainTable } from '@deepseek-ai/dsh-storage-domain'
import { join } from 'node:path'
import { dshHomePath } from '@deepseek-ai/dsh-home-paths'
import { remote, schemas, jobSchema } from './remote.js'
import { HubWorker, resolveHubPython } from './worker.js'
import { HubCoordinator } from './hub.js'
import { prepareHubJob, projectHubJob, projectHubActivity } from './hub-job.js'
import { assertLegacySecretaryDetached } from './legacy.js'
import { initializeProfile } from '../../alcor-organizer/src/profile-migration.js'

const legacySpec = defineDomain({name:'alcor_organizer',version:1,tables:{records:domainTable(z.record(z.string(),z.json())),jobs:domainTable(jobSchema),schedules:domainTable(z.record(z.string(),z.json()))}})

function assertCompatibleProfile(ctx) {
  const selected = ctx.get('profileContext')?.startedBundles
  if (!Array.isArray(selected)) throw new Error('无法确认当前 Profile 的插件，已停止 Alcor 启动。')
  if (selected.includes('@stella/agent-hub')) throw new Error('旧版 Stella 插件与 Alcor 不能同时启用，请先迁移 Profile。')
}

export default class AgentHub extends TypertRemoteService {
  static Config=modeConfig('dsh')
  static inject = ['credentials', 'typert','subagents','sessionController','agentPresets','storageDomain']
  private worker
  private hub
  private active = true
  private queue = Promise.resolve()
  constructor(ctx) { assertCompatibleProfile(ctx); super(ctx, 'agentHub', {namespace:'agentHub'}) }
  async [Service.init]() {
    assertCompatibleProfile(this.ctx)
    await initializeProfile(this.ctx)
    await assertLegacySecretaryDetached(this.ctx.get('profileContext'))
    const legacy = this.ctx.get('stellaOrganizer')
    if (legacy && !legacy.agentHubDetached) throw new Error('请先将 Stella 更新到支持独立 AgentHub 的版本；协作数据尚未改动。')
    const profile = this.ctx.get('profileContext')?.dir ?? dshHomePath('profiles','default')
    this.worker = new HubWorker(join(profile,'agent-hub'), {python:await resolveHubPython(),legacyDirectory:join(profile,'alcor','business'),diagnostic:message=>this.ctx.logger.warn(message)})
    try {
      await this.worker.call('initialize')
      if (!legacy) {
        try {
          const domain = await this.ctx.storageDomain.open(legacySpec)
          try { await this.importLegacyJobs([...domain.table('jobs').entries()].map(([,value])=>value)) }
          finally { await domain.close() }
        } catch (error) { if (error.code !== 'already-open') throw error }
      }
    } catch (error) { await this.worker.close(); throw error }
    this.hub = new HubCoordinator(this.ctx,this.worker)
    this.ctx.typert.register({package:'@alcor/agent-hub',face:'host',schemas:[],invocations:remote.descriptors,model:{services:[],events:[],objects:[]}})
    this.ctx.effect(()=>async()=>{ this.active=false; await this.queue; await this.hub.close(); await this.worker.close() })
  }
  private assertActive() { if(!this.active) throw new Error('AgentHub 已停用。') }
  private serialize(work) { const result=this.queue.then(()=>{this.assertActive();return work()});this.queue=result.catch(()=>undefined);return result }
  async importLegacyJobs(jobs) { await this.worker.call('import_jobs',{jobs:z.array(jobSchema).parse(jobs)}) }
  @Remote
  async read(request) {
    schemas.read[0].parse(request);this.assertActive()
    const tasks=await this.worker.call('invoke',{domain:'agent_hub',method:'list_tasks'})
    const jobs=await Promise.all(tasks.map(task=>this.worker.call('invoke',{domain:'agent_hub',method:'get_task',arguments:{task_id:task.taskId}}).then(task=>this.hubJob(task))))
    return {jobs:[...await this.worker.call('legacy_jobs'),...jobs]}
  }
  @Remote
  async startJob(request) {
    const input = schemas.startJob[0].parse(request)
    return this.serialize(async () => {
      const found = await this.ctx.sessionController.resolveAgent(input.parentSessionId)
      if ('error' in found || !this.ctx.agentPresets.presetForSession(found.agent.session) || !permits(selectedScope(this.ctx,'@alcor/agent-hub'),sessionMode(this.ctx,found.agent))) throw new Error('请先开始一个开发对话，再发起协作。')
      const invoke = (method, args) => this.worker.call('invoke', { domain: 'agent_hub', method, arguments: args })
      const task = await prepareHubJob(invoke, input, sessionMode(this.ctx,found.agent))
      if (!task.steps.some(step => step.attempts.length)) await this.hub.start({ task_id: task.taskId, parentSessionId: input.parentSessionId })
      return this.hubJob(task)
    })
  }

  private hubJob(task) {
    return jobSchema.parse(projectHubJob(task))
  }

  @Remote
  async hubActivity(request) {
    const { id } = schemas.hubActivity[0].parse(request)
    this.assertActive()
    if (id.startsWith('hub-')) return projectHubActivity(await this.worker.call('invoke', { domain: 'agent_hub', method: 'get_task', arguments: { task_id: id } }))
    const job = (await this.worker.call('legacy_jobs', { id }))[0]
    if (!job) throw new Error('协作任务不存在。')
    return [{ id: id + ':goal', kind: 'created', body: job.prompt, createdAt: job.createdAt },
      ...(job.output ? [{ id: id + ':result', kind: 'reply', body: job.output, ...(job.childId ? { sessionId: job.childId } : {}) }] : []),
      ...(job.diagnostic ? [{ id: id + ':status', kind: 'status', body: job.diagnostic, status: job.status }] : []),
    ]
  }

  @Remote
  async hubSend(request) {
    const input = schemas.hubSend[0].parse(request)
    return this.serialize(async () => {
      if (input.intent === 'task') {
        const found = await this.ctx.sessionController.resolveAgent(input.parentSessionId)
        if ('error' in found || !this.ctx.agentPresets.presetForSession(found.agent.session) || !permits(selectedScope(this.ctx,'@alcor/agent-hub'),sessionMode(this.ctx,found.agent))) throw new Error('请先开始一个开发对话，再分配任务。')
      }
      await this.worker.call('invoke', { domain: 'agent_hub', method: 'post_message', arguments: {
        task_id: input.id, body: input.body, recipient_ids: input.recipientIds, intent: input.intent, expected_revision: null, request_id: input.requestId,
      } })
      if (input.intent === 'task') await this.hub.kick({ task_id: input.id, parentSessionId: input.parentSessionId })
      return { id: input.id, intent: input.intent }
    })
  }

  @Remote
  async retryJob(request) {
    const input = schemas.retryJob[0].parse(request)
    return this.serialize(async () => {
      const found = await this.ctx.sessionController.resolveAgent(input.parentSessionId)
      if ('error' in found || !this.ctx.agentPresets.presetForSession(found.agent.session) || !permits(selectedScope(this.ctx,'@alcor/agent-hub'),sessionMode(this.ctx,found.agent))) throw new Error('请先开始一个开发对话，再重试协作。')
      await this.hub.start({ task_id: input.id, parentSessionId: input.parentSessionId, step_id: input.stepId, retry: true })
      return this.hubJob(await this.worker.call('invoke', { domain: 'agent_hub', method: 'get_task', arguments: { task_id: input.id } }))
    })
  }

  @Remote
  async cancelJob(request) {
    const { id } = schemas.cancelJob[0].parse(request)
    this.assertActive()
    if (id.startsWith('hub-')) return this.hubJob(await this.hub.cancel(id))
    const current = (await this.worker.call('legacy_jobs', { id }))[0]
    if (!current) throw new Error('协作任务不存在。')
    return current
  }

}
