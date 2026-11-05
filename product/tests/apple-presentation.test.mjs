import test from 'node:test'
import assert from 'node:assert/strict'
import { connectionStatus, formatSyncTime } from '../../packages/stella-apple-sync/src/presentation.js'

test('Apple status distinguishes a running operation from enabled background sync, pause and failures', () => {
  const state = { authorization: 'full_access', connected: true, auto_enabled: true }
  assert.equal(connectionStatus(null, false).label, '读取中')
  assert.equal(connectionStatus(null, false, true).tone, 'error')
  assert.equal(connectionStatus(state, true).label, '处理中')
  assert.equal(connectionStatus(state, false).label, '自动同步已开启')
  assert.equal(connectionStatus({ ...state, connected: false }, false).label, '未开启')
  assert.equal(connectionStatus({ ...state, connected: false, last_success: '2026-11-06T00:00:00Z' }, false).label, '已暂停')
  assert.equal(connectionStatus({ ...state, auto_enabled: false, links: [{}] }, false).label, '已暂停')
  assert.equal(connectionStatus({ ...state, authorization: 'not_determined' }, false).label, '待授权')
  assert.equal(connectionStatus({ ...state, authorization: 'denied' }, false).label, '无法同步')
  assert.equal(connectionStatus({ ...state, error: 'Read failed' }, false).tone, 'error')
  assert.equal(connectionStatus({ ...state, conflicts: [{}] }, false).label, '待处理冲突')
})

test('Apple sync times use Chinese dates and a 24-hour local clock, with a safe empty state', () => {
  assert.equal(formatSyncTime(null), null)
  assert.equal(formatSyncTime('invalid'), null)
  assert.equal(formatSyncTime(new Date(2026, 10, 6, 0, 5).toISOString()), '2026/11/06 00:05')
  assert.equal(formatSyncTime(new Date(2026, 10, 6, 17, 9).toISOString()), '2026/11/06 17:09')
})
