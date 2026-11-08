import test from 'node:test'
import assert from 'node:assert/strict'
import { callAutomation } from '../../packages/alcor-organizer/src/page-tools.js'
import { readDateFile, writeBillCsv } from '../../packages/alcor-secretary/src/page-files.js'

test('automation tool routing uses the page contract and cannot escape Stella ownership', async () => {
  const calls = []
  const owner = { automation: async request => { calls.push(request); return { id: 'job' } } }
  const catalog = await callAutomation(owner, 'catalog', {})
  assert(catalog.some(row => row.name === 'create'))
  assert.equal(catalog.find(row => row.name === 'list').description, '列出 Alcor 自动化及其状态')
  assert.equal(catalog.find(row => row.name === 'deleteInactive').description, '清理暂不可用；不会删除或自动暂停任务')
  await callAutomation(owner, 'list', { scope: 'all' })
  assert.deepEqual(calls.pop(), { scope: 'alcor', action: 'catalog' })
  await callAutomation(owner, 'create', { title: '回顾', prompt: '回顾事务', timing: { kind: 'daily', daily: { time: '08:00', time_zone: 'Asia/Shanghai' } } })
  assert.equal(calls.at(-1).scope, 'alcor')
  await assert.rejects(callAutomation(owner, 'create', { scope: 'all', title: 'bad' }))
  await assert.rejects(callAutomation(owner, 'unknown', {}))
  owner.automation = async () => ({ code: 'conflict' })
  await assert.rejects(callAutomation(owner, 'edit', { id: 'job', action: 'update', patch: { active: false } }), /conflict/)
})

test('date reads are bounded and bill exports preserve the active sandbox policy and existing files', async () => {
  const fs = { sandboxMode: 'workspace-write', resolve: async path => path, stat: async () => ({ type: 'file', size: 50 }), readBytes: async () => Buffer.from('title,date\nBirthday,2026-11-06') }
  assert.equal((await readDateFile(fs, '/test/dates.csv')).format, 'csv')
  await assert.rejects(readDateFile(fs, '/test/secret.json'))
  await assert.rejects(readDateFile({ ...fs, stat: async () => ({ type: 'file', size: 1024 * 1024 + 1 }) }, '/test/dates.csv'), /1 MB/)
  await assert.rejects(readDateFile({ ...fs, resolve: async () => { throw new Error('DSH denied') } }, '/test/dates.csv'), /DSH denied/)
  const ctx = { get: name => name === 'fs' ? fs : undefined }
  await assert.rejects(writeBillCsv(ctx, '/test/export.csv', 'data'), /策略不可用/)
  let write
  const policy = { mode: 'workspace-write', workspaceRoot: '/test' }
  ctx.get = name => name === 'fs' ? fs : { resolve: ({ session }) => { assert.equal(session.id, 'session'); return policy } }
  fs.writeText = async (...args) => { write = args }
  await writeBillCsv(ctx, '/test/export.csv', 'data', undefined, { id: 'session' })
  assert.equal(write[1], '\ufeffdata')
  assert.deepEqual(write[2], { kind: 'createIfAbsent' })
  assert.equal(write[4], policy)
  fs.writeText = async () => { throw new Error('file exists') }
  await assert.rejects(writeBillCsv(ctx, '/test/export.csv', 'data', undefined, { id: 'session' }), /exists/)
})
