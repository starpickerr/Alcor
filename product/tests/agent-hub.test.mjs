import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readHubDraft, jobRunning } from '../../packages/stella-secretary/src/agent-hub.js'

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
