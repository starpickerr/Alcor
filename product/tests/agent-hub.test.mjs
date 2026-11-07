import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readHubDraft, jobRunning, filterHubJobs, canRetryStep } from '../../packages/stella-agent-hub/src/agent-hub.js'

test('hub drafts survive navigation, reject corrupt browser state, and never become running jobs', () => {
  const draft = { id: 'draft-1', title: 'Research', prompt: '<untrusted material>', injected: 'ignored' }
  const storage = { getItem: () => JSON.stringify(draft) }
  assert.deepEqual(readHubDraft(storage), { id: draft.id, title: draft.title, prompt: draft.prompt })
  assert.equal(jobRunning(draft), false)
  assert.equal(jobRunning({ status: 'running' }), true)
  assert.equal(jobRunning({ status: 'interrupted' }), false)
  for (const value of ['not json', '{}', '{"title":23}', JSON.stringify({ ...draft, prompt: 'x'.repeat(12001) })]) {
    assert.equal(readHubDraft({ getItem: () => value }), null)
  }
  assert.equal(readHubDraft({ getItem: () => { throw new Error('denied') } }), null)
})

test('Hub divisions survive reload, ignore injected data, and reject oversized drafts', () => {
  const draft = { id: 'one', title: '', prompt: 'Keep this while editing the title', agents: [{ name: 'A', task: '', injected: true }] }
  const read = value => readHubDraft({ getItem: () => JSON.stringify(value) })
  assert.deepEqual(read(draft), { ...draft, agents: [{ name: 'A', task: '' }] })
  assert.equal(read({ ...draft, agents: [] }), null)
  assert.equal(read({ ...draft, agents: Array(4).fill(draft.agents[0]) }), null)
  assert.equal(read({ ...draft, agents: [{ name: 'A', task: 23 }] }), null)
})

test('Hub search, status filters and retry availability reflect actual jobs', () => {
  const jobs = [{ id: 'a', title: 'Research', prompt: 'Plan A', status: 'running', createdAt: '2026-11-01' }, { id: 'b', title: 'Review', prompt: 'PLAN B', status: 'failed', createdAt: '2026-11-02' }]
  assert.deepEqual(filterHubJobs(jobs, 'plan', 'all').map(job => job.id), ['b', 'a'])
  assert.deepEqual(filterHubJobs(jobs, '', 'attention').map(job => job.id), ['b'])
  assert.equal(filterHubJobs(jobs, 'Review', 'running').length, 0)
  assert.equal(canRetryStep({ status: 'outcome_unknown', attempts: [{}] }), true)
  assert.equal(canRetryStep({ status: 'failed', attempts: [{}, {}, {}] }), false)
  assert.equal(canRetryStep({ status: 'succeeded', attempts: [{}] }), false)
})
