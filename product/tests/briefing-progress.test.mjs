import test from 'node:test'
import assert from 'node:assert/strict'
import { briefingRunning, briefingProgressVisible } from '../../packages/alcor-secretary/src/briefing-progress.js'

test('only active and recoverable briefing states occupy progress space', () => {
  for (const phase of ['starting', 'collecting', 'model', 'failed', 'cancelled', 'interrupted']) {
    assert.equal(briefingProgressVisible({ phase, date: '2026-11-16' }, '2026-11-16', 0), true, phase)
  }
  for (const phase of ['idle', 'completed', undefined]) assert.equal(briefingProgressVisible({ phase }, '', 0), false, phase)
  assert.equal(briefingRunning({ phase: 'model' }), true)
  assert.equal(briefingRunning({ phase: 'interrupted' }), false)
})

test('progress belongs to its report and never appears under another date or revision', () => {
  const run = { phase: 'collecting', date: '2026-11-16' }
  assert.equal(briefingProgressVisible(run, '', 0), true, 'First generation has no saved report')
  assert.equal(briefingProgressVisible(run, '2026-11-15', 0), false)
  assert.equal(briefingProgressVisible(run, '2026-11-16', 1), false)
})
