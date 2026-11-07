import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { schemas, jobSchema } from '../../packages/stella-agent-hub/src/remote.js'
import { prepareHubJob, projectHubJob, projectHubActivity } from '../../packages/stella-agent-hub/src/hub-job.js'
import { HubWorker } from '../../packages/stella-agent-hub/src/worker.js'

test('Hub accepts bounded native divisions and preserves single-agent callers', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'stella-hub-workspace-'))
  const worker = new HubWorker(directory); await worker.call('initialize')
  t.after(async () => { await worker.close(); await rm(directory, { recursive: true, force: true }) })
  const invoke = (method, args) => worker.call('invoke', { domain: 'agent_hub', method, arguments: args })
  const base = { id: randomUUID(), title: 'Research', prompt: 'Compare options', parentSessionId: 'parent' }
  const input = schemas.startJob[0].parse({ ...base, agents: [{ name: 'Researcher', task: 'Find evidence' }, { name: 'Reviewer', task: 'Compare risks' }] })
  const task = await prepareHubJob(invoke, input)
  assert.equal(task.steps.length, 3)
  assert.deepEqual(task.steps.at(-1).dependsOn, ['work-1', 'work-2'])
  assert.deepEqual(task.participants.map(person => person.displayName), ['Researcher', 'Reviewer', '汇总助手'])
  assert.equal((await prepareHubJob(invoke, input)).taskId, task.taskId)
  await assert.rejects(prepareHubJob(invoke, { ...input, prompt: 'Changed' }), /request_id reused/)
  const projection = jobSchema.parse(projectHubJob(task))
  assert.equal(projection.status, 'starting')
  assert.equal(projection.collaboration.steps[0].status, 'pending')
  assert.ok(!JSON.stringify(projection).includes('snapshot'))
  const single = await prepareHubJob(invoke, schemas.startJob[0].parse({ ...base, id: randomUUID() }))
  assert.equal(single.steps.length, 1)
  assert.equal(single.participants.length, 1)
  for (const agents of [[], Array(4).fill(input.agents[0]), [{ name: ' ', task: 'Do' }], [{ name: 'User', task: 'x'.repeat(1001) }], [{ name: 'User', task: 'Do', nativeAgentId: 'forged' }]]) {
    assert.equal(schemas.startJob[0].safeParse({ ...base, agents }).success, false)
  }
  task.messages.push({ kind: 'reply', stepId: 'work-1', body: 'Partial' })
  assert.equal(projectHubJob(task).output, '', 'Do not present a partial division as the final summary')
  task.messages.push({ kind: 'reply', stepId: 'summary', body: 'Final' })
  assert.equal(projectHubJob(task).output, 'Final')
  const legacy = { ...projection }; delete legacy.collaboration
  assert.ok(jobSchema.safeParse(legacy).success)
  for (const [stepId, marker] of [['work-1', 'FIRST_EVIDENCE'], ['work-2', 'SECOND_EVIDENCE']]) {
    const attempt = await invoke('claim_step', { task_id: task.taskId, step_id: stepId })
    await invoke('sync_attempt', { task_id: task.taskId, step_id: stepId, execution_id: attempt.executionId, result: { status: 'succeeded', reply: marker + 'x'.repeat(18000) } })
  }
  const summaryClaim = await invoke('claim_step', { task_id: task.taskId, step_id: 'summary' })
  assert.ok(summaryClaim.goal.includes('FIRST_EVIDENCE'), 'Long second output must not erase the first division')
  assert.ok(summaryClaim.goal.includes('SECOND_EVIDENCE'))
  assert.ok(summaryClaim.goal.includes('已截断'))
  await invoke('sync_attempt', { task_id: task.taskId, step_id: 'summary', execution_id: summaryClaim.executionId, result: { status: 'running' } })
  const firstStart = (await invoke('get_task', { task_id: task.taskId })).steps.at(-1).attempts.at(-1).startedAt
  assert.ok(firstStart)
  await invoke('sync_attempt', { task_id: task.taskId, step_id: 'summary', execution_id: summaryClaim.executionId, result: { status: 'running', attemptId: 'native-summary-session' } })
  const latest = await invoke('get_task', { task_id: task.taskId })
  assert.equal(latest.steps.at(-1).attempts.at(-1).startedAt, firstStart, 'Connecting the native session must not rewrite start time')
  assert.ok(schemas.hubActivity[1].safeParse(projectHubActivity(latest)).success)
})

test('Hub activity orders formal messages and attempts without leaking execution snapshots', () => {
  const task = { taskId: 'hub-one', goal: 'User task', createdAt: '2026-11-07T01:00:00Z', steps: [{ stepId: 'research', participantId: 'a', attempts: [
    { executionId: 'exec-old', createdAt: '2026-11-07T01:01:00Z', status: 'failed', snapshot: { persona: 'PRIVATE_SNAPSHOT' }, goal: 'PRIVATE_EXECUTION_GOAL' },
    { executionId: 'exec-new', createdAt: '2026-11-07T01:03:00Z', startedAt: '2026-11-07T01:04:00Z', attemptId: 'child-two', status: 'succeeded' },
  ] }], messages: [
    { messageId: 'reply', kind: 'reply', body: '**Research result**', createdAt: '2026-11-07T01:05:00Z', senderParticipantId: 'a', recipientParticipantIds: [], stepId: 'research', executionId: 'exec-new' },
    { messageId: 'request', kind: 'user', body: 'Additional evidence', createdAt: '2026-11-07T01:02:00Z', recipientParticipantIds: ['a'] },
  ] }
  const entries = schemas.hubActivity[1].parse(projectHubActivity(task))
  assert.deepEqual(entries.map(entry => entry.id), ['hub-one', 'exec-old', 'request', 'exec-new', 'reply'])
  assert.equal(entries[1].body, '第 1 次执行已创建')
  assert.equal(entries[3].body, '第 2 次执行已开始')
  assert.deepEqual(entries[2].recipientIds, ['a'])
  assert.equal(entries.at(-1).sessionId, 'child-two')
  assert.ok(!JSON.stringify(entries).includes('PRIVATE_'))
  assert.ok(schemas.hubActivity[1].safeParse([{ id: 'old-result', kind: 'reply', body: 'Old result' }]).success, 'Legacy output must not require invented timestamps')
})
