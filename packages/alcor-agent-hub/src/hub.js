const status = step => step.attempts.at(-1)?.status ?? 'pending'

export class HubCoordinator {
  constructor(ctx, worker) {
    this.ctx = ctx
    this.worker = worker
    this.tasks = new Map()
    this.closed = false
  }

  call(method, args) { return this.worker.call('invoke', { domain: 'agent_hub', method, arguments: args }) }

  async kick(request) {
    const running = this.tasks.get(request.task_id)
    if (running) { running.wake = request; return }
    return this.start(request)
  }

  async start({ task_id, parentSessionId, workspacePath, step_id, retry = false }) {
    if (this.closed || this.tasks.has(task_id)) throw new Error('任务正在运行或功能已停用。')
    const controller = new AbortController()
    const state = { controller, activations: new Set(), external: new Set(), completion: undefined, wake: undefined }
    this.tasks.set(task_id, state)
    try {
      const task = await this.call('get_task', { task_id })
      if (!task.steps.length) throw new Error('请先添加流程步骤。')
      if (step_id) {
        const step = task.steps.find(item => item.stepId === step_id)
        if (!step || (retry ? !['failed', 'cancelled', 'outcome_unknown'].includes(status(step)) || step.attempts.length >= 3 : status(step) !== 'pending')) throw new Error('只能重试失败、取消或结果未知的步骤，每个步骤最多执行三次。')
        if (step.dependsOn.some(id => status(task.steps.find(item => item.stepId === id)) !== 'succeeded')) throw new Error('请先完成此步骤的前序分工。')
      }
      let parent
      if (task.participants.some(person => person.nativeAgentId)) {
        if (!parentSessionId) throw new Error('请提供当前开发对话。')
        const result = await this.ctx.sessionController.resolveAgent(parentSessionId)
        if ('error' in result) throw new Error('当前对话不可用。')
        parent = result.agent
      }
      state.completion = this.execute(task_id, state, parent, workspacePath, step_id, retry)
        .catch(error => { this.ctx.logger.warn('协作流程执行中断：' + error.message) })
        .finally(async () => {
          this.tasks.delete(task_id)
          if (state.wake && !this.closed && !controller.signal.aborted) {
            try {
              const latest = await this.call('get_task', { task_id })
              if (!latest.cancelled && !this.tasks.has(task_id) && latest.steps.some(step => status(step) === 'pending' && step.dependsOn.every(id => status(latest.steps.find(item => item.stepId === id)) === 'succeeded'))) await this.start(state.wake)
            } catch (error) { this.ctx.logger.warn('新增协作任务未能继续：' + error.message) }
          }
        })
      return task
    } catch (error) {
      this.tasks.delete(task_id)
      throw error
    }
  }

  async execute(task_id, state, parent, workspacePath, step_id, retry) {
    const pending = new Map()
    let requested = step_id
    while (!state.controller.signal.aborted) {
      const task = await this.call('get_task', { task_id })
      if (task.cancelled && !(requested && retry)) break
      const states = new Map(task.steps.map(step => [step.stepId, status(step)]))
      for (const step of task.steps) {
        if (state.controller.signal.aborted) break
        if (pending.size >= 3) break
        if (step_id && !retry && !requested) continue
        if (requested ? step.stepId !== requested : status(step) !== 'pending') continue
        if (step.dependsOn.some(dependency => states.get(dependency) !== 'succeeded')) continue
        const claim = await this.call('claim_step', { task_id, step_id: step.stepId, retry: requested ? retry : false })
        requested = undefined
        const promise = this.runStep(task_id, step.stepId, claim, state, parent, workspacePath)
          .finally(() => pending.delete(step.stepId))
        pending.set(step.stepId, promise)
      }
      if (!pending.size) break
      await Promise.race(pending.values())
    }
    await Promise.allSettled(pending.values())
    if (requested) throw new Error('步骤不可执行，请核对步骤标识和依赖状态。')
  }

  async runStep(task_id, step_id, claim, state, parent, workspacePath) {
    const sync = result => this.call('sync_attempt', { task_id, step_id, execution_id: claim.executionId, result })
    try {
      if (state.controller.signal.aborted) return sync({ status: 'cancelled' })
      await sync({ status: 'running' })
      if (state.controller.signal.aborted) return sync({ status: 'cancelled' })
      const person = claim.snapshot
      if (person.providerConfigId) {
        if (!workspacePath) throw new Error('外部 Agent 需要选择工作区。')
        const started = await this.call('provider_start', { provider_config_id: person.providerConfigId, task: {
          executionId: claim.executionId, goal: claim.goal, workspacePath, runConfig: person,
        } })
        if (started.status !== 'started') throw new Error(started.error?.message ?? '外部 Agent 启动失败。')
        state.external.add(claim.executionId)
        let cursor = 0
        let reply = ''
        let remainder = ''
        const consumeReply = line => {
          try {
            const value = JSON.parse(line)
            if (value.type === 'item.completed' && value.item?.type === 'agent_message') reply = value.item.text
            if (value.type === 'result' && !value.is_error && typeof value.result === 'string') reply = value.result
          } catch {}
        }
        let cancelAt
        const deadline = Date.now() + 30 * 60 * 1000
        while (Date.now() < deadline) {
          if (state.controller.signal.aborted && !cancelAt) {
            cancelAt = Date.now()
            await this.call('provider_cancel', { execution_id: claim.executionId })
          }
          if (cancelAt && Date.now() - cancelAt > 5000) return sync({ status: 'outcome_unknown', summary: '取消尚未获确认，请核对外部进程。' })
          const events = await this.call('provider_observe', { execution_id: claim.executionId, after_cursor: cursor })
          for (const event of events) {
            if (event.type === 'output') {
              cursor = event.cursor
              const lines = (remainder + event.text).split('\n')
              remainder = lines.pop() ?? ''
              for (const line of lines) consumeReply(line)
            }
            if (event.type === 'terminal') {
              consumeReply(remainder)
              state.external.delete(claim.executionId)
              return sync({ status: event.status, cursor, ...(event.outputPath ? { artifactRefs: [event.outputPath] } : {}), ...(reply && event.status === 'succeeded' ? { reply: reply.slice(0, 65536) } : {}) })
            }
          }
          await new Promise(resolve => setTimeout(resolve, 250))
        }
        await this.call('provider_cancel', { execution_id: claim.executionId })
        return sync({ status: 'outcome_unknown', summary: '执行超过等待期限，请核对外部结果后重试。' })
      }
      const selection = person.modelSelection
      const route = selection.mode === 'explicit' ? { model: selection.modelId, provider: selection.providerId ?? parent?.model?.current()?.provider } : parent?.model?.current()
      const activation = await this.ctx.subagents.startActivation({ provider: 'agent-hub-spawn', label: person.displayName, delivery: 'caller', signal: state.controller.signal,
        request: { parent, agentPreset: ['alcor','stella'].includes(person.nativeAgentId) ? 'alcor' : 'agent-hub-developer', maxDepth: 1,
          ...(route ? { agentOptions: route } : {}),
          prompt: [{ type: 'text', text: `任务内人设：${person.persona}\n\n${claim.goal}` }],
        } })
      state.activations.add(activation)
      await sync({ status: 'running', attemptId: activation.childId, ...(route?.model ? { actualModel: route.model } : {}) })
      try {
        const result = await activation.result
        const reply = result.output.filter(block => block.type === 'text').map(block => block.text).join('\n').slice(0, 65536)
        return sync({ status: state.controller.signal.aborted ? 'cancelled' : result.stopReason === 'completed' ? 'succeeded' : 'failed', reply,
          ...(route?.model ? { actualModel: route.model } : {}) })
      } finally { state.activations.delete(activation); await activation.dispose() }
    } catch (error) {
      return sync({ status: 'outcome_unknown', summary: error.message })
    }
  }

  async cancel(task_id) {
    await this.call('mark_cancelled', { task_id })
    const task = this.tasks.get(task_id)
    if (task) {
      task.controller.abort()
      await Promise.allSettled([...task.activations].map(activation => activation.dispose()))
      await Promise.allSettled([...task.external].map(execution_id => this.call('provider_cancel', { execution_id })))
    }
    return this.call('get_task', { task_id })
  }

  async close() {
    this.closed = true
    await Promise.allSettled([...this.tasks.keys()].map(task_id => this.cancel(task_id)))
    await Promise.allSettled([...this.tasks.values()].map(task => task.completion))
  }
}
