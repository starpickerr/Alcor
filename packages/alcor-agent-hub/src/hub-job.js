const states = { draft: 'starting', ready: 'starting', pending: 'starting', queued: 'starting', running: 'running', waiting_approval: 'running', cancel_requested: 'running', interrupted: 'running', succeeded: 'completed', outcome_unknown: 'interrupted', failed: 'failed', cancelled: 'cancelled' }

export async function prepareHubJob(invoke, input, ownerMode = 'dsh') {
  const agents = input.agents ?? [{ name: '开发助手', task: input.title }]
  const parallel = agents.length > 1
  let task = await invoke('create_task', { payload: { title: input.title, goal: input.prompt,
    participants: input.agents ? [...agents.map(agent => ({ nativeAgentId: ownerMode, displayName: agent.name })), ...(parallel ? [{ nativeAgentId: ownerMode, displayName: '汇总助手' }] : [])] : [{ nativeAgentId: ownerMode }],
  }, request_id: input.id })
  task = await invoke('get_task', { task_id: task.taskId })
  if (!task.steps.length) {
    const steps = agents.map((agent, index) => ({ stepId: input.agents ? `work-${index + 1}` : 'work', title: agent.task, participantId: task.participants[index].participantId, dependsOn: [] }))
    if (parallel) steps.push({ stepId: 'summary', title: '汇总所有前序分工的结果，核对相互矛盾之处，围绕任务目标给出一份完整答复；保留重要依据和未解决的问题。', participantId: task.participants.at(-1).participantId, dependsOn: steps.map(step => step.stepId) })
    task = await invoke('set_workflow', { task_id: task.taskId, steps, expected_revision: task.revision, request_id: input.id + ':workflow' })
  }
  return task
}

export function projectHubJob(task) {
  const steps = task.steps.map(step => ({ stepId: step.stepId, title: step.title, participantId: step.participantId, dependsOn: step.dependsOn,
    status: step.attempts.at(-1)?.status ?? 'pending',
    output: task.messages.filter(message => message.kind === 'reply' && message.stepId === step.stepId).at(-1)?.body ?? '',
    attempts: step.attempts.map(attempt => ({ executionId: attempt.executionId, status: attempt.status, createdAt: attempt.createdAt,
      ...(attempt.attemptId ? { childId: attempt.attemptId } : {}), ...(attempt.actualModel ? { model: attempt.actualModel } : {}),
      ...(attempt.summary || attempt.error ? { diagnostic: typeof attempt.error === 'string' ? attempt.error : attempt.summary || '执行失败，请查看原生会话。' } : {}),
    })),
  }))
  const latest = steps.flatMap(step => step.attempts).filter(attempt => attempt.childId).at(-1)
  const summary = steps.find(step => step.stepId === 'summary')
  return { id: task.taskId, title: task.title, prompt: task.goal, parentSessionId: '', createdAt: new Date(task.createdAt).toISOString(),
    status: states[task.status] ?? 'interrupted', ...(latest ? { childId: latest.childId } : {}),
    output: (summary ? summary.output : task.messages.filter(message => message.kind === 'reply').map(message => message.body).join('\n\n')).slice(0, 100000),
    diagnostic: task.messages.filter(message => message.kind === 'status').map(message => message.body).join('\n').slice(0, 4096),
    collaboration: { participants: task.participants.map(person => ({ id: person.participantId, name: person.displayName })), steps },
  }
}

export function projectHubActivity(task) {
  const entries = [{ id: task.taskId, kind: 'created', body: task.goal, createdAt: task.createdAt }]
  for (const step of task.steps) {
    for (const [index, attempt] of step.attempts.entries()) {
      entries.push({ id: attempt.executionId, kind: 'started', body: `第 ${index + 1} 次执行${attempt.startedAt ? '已开始' : '已创建'}`,
        createdAt: attempt.startedAt ?? attempt.createdAt, senderId: step.participantId, stepId: step.stepId,
        executionId: attempt.executionId, ...(attempt.attemptId ? { sessionId: attempt.attemptId } : {}),
      })
    }
  }
  for (const message of task.messages) {
    const attempt = task.steps.flatMap(step => step.attempts).find(item => item.executionId === message.executionId)
    entries.push({ id: message.messageId, kind: message.kind, body: message.body, createdAt: message.createdAt,
      ...(message.senderParticipantId ? { senderId: message.senderParticipantId } : {}),
      ...(message.recipientParticipantIds?.length ? { recipientIds: message.recipientParticipantIds } : {}),
      ...(message.stepId ? { stepId: message.stepId } : {}), ...(message.executionId ? { executionId: message.executionId } : {}),
      ...(attempt?.attemptId ? { sessionId: attempt.attemptId } : {}), ...(attempt && message.kind === 'status' ? { status: attempt.status } : {}),
    })
  }
  return entries.sort((a, b) => Date.parse(a.createdAt) - Date.parse(b.createdAt))
}
