import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, readFile, writeFile, stat, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import Credentials from '@deepseek-ai/dsh-credentials-local'
import { credentialRef } from '@deepseek-ai/dsh-credentials'
import Authorization from '@deepseek-ai/dsh-authorization'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Tools from '@deepseek-ai/dsh-tools'
import * as Executor from '@deepseek-ai/dsh-tool-executor-native'
import Elearning, { accountKey } from '../../packages/alcor-secretary/src/elearning.js'

test('eLearning uses native DSH credentials, rotates and revokes the live MCP, and survives restart', async t => {
  const folder = await mkdtemp(join(tmpdir(), 'stella-native-credentials-'))
  t.after(() => rm(folder, { recursive: true, force: true }))
  const filename = join(folder, '.credentials.yaml')
  const command = join(folder, 'synthetic-mcp')
  const fixture = await readFile(new URL('./fixtures/mcp-server.mjs', import.meta.url), 'utf8')
  await writeFile(command, '#!' + process.execPath + '\n' + fixture.replace("'synthetic-course'", "JSON.parse(process.env.ALCOR_ELEARNING_CREDENTIAL).username"), { mode: 0o700 })
  const config = { command, cwd: folder, dataDir: folder }
  const makeContext = async () => {
    const ctx = new Context()
    ctx.provide('logger', { warn() {} })
    await ctx.plugin(Credentials, { path: filename, watch: false }).await()
    await ctx.plugin(Authorization).await()
    await ctx.plugin(SystemPrompt, {}).await()
    await ctx.plugin(Tools).await()
    await ctx.plugin(Executor).await()
    const fiber = await ctx.plugin(Elearning, config).await()
    return { ctx, fiber }
  }
  let { ctx } = await makeContext()
  t.after(() => ctx.fiber.dispose())
  const call = async () => (await ctx.tools.execute({ signal: AbortSignal.timeout(5000), calls: [{ type: 'tool-call', id: 'test', name: 'mcp__elearning__list_courses', arguments: '{}' }] })).results[0].result
  assert.deepEqual(await ctx.alcorElearning.state(), { installed: true, configured: false, connected: false, writable: true })
  await assert.rejects(ctx.alcorElearning.save({ username: '', password: 'synthetic' }))
  assert.equal(ctx.authorization.describe(accountKey).label, '复旦 eLearning')
  await ctx.credentials.set(credentialRef('UNRELATED_MODEL_API_KEY'), 'synthetic-model-key')
  await ctx.alcorElearning.save({ username: 'first-account', password: 'first-synthetic-secret' })
  assert((await ctx.alcorElearning.state()).connected)
  assert.deepEqual(await ctx.alcorElearning.state(), { installed: true, configured: true, connected: true, writable: true })
  assert.equal((await ctx.alcorElearning.state({ includeUsername: true })).username, 'first-account')
  const source = await ctx.alcorElearning.briefingSource()
  assert.equal(source.source, 'elearning')
  assert(!JSON.stringify(source).includes('first-synthetic-secret'))
  const firstResult = await call(); assert(JSON.stringify(firstResult).includes('first-account'), JSON.stringify(firstResult))
  assert.equal((await stat(filename)).mode & 0o777, 0o600)
  assert.equal((await ctx.credentials.readRecord(accountKey)).payload.password, 'first-synthetic-secret')
  assert(!JSON.stringify(await ctx.alcorElearning.state()).includes('synthetic-secret'))
  assert(!JSON.stringify(config).includes('synthetic-secret'))
  // A mutation between admission and execution must reject the old connection.
  const removeGate = ctx.on('tools/pre-execute', async (_execution, next) => {
    await ctx.credentials.deleteRecord(accountKey)
    return next()
  })
  assert.equal((await call()).isError, true)
  removeGate()
  await ctx.alcorElearning.save({ username: 'second-account', password: 'second-synthetic-secret' })
  assert.notEqual((await ctx.alcorElearning.briefingSource()).accountId, source.accountId)
  assert(JSON.stringify(await call()).includes('second-account'))
  assert(!(await readFile(filename, 'utf8')).includes('first-synthetic-secret'))
  await ctx.fiber.dispose()
  ;({ ctx } = await makeContext())
  assert((await ctx.alcorElearning.state()).connected)
  assert.equal((await ctx.alcorElearning.state({ includeUsername: true })).username, 'second-account')
  assert(JSON.stringify(await call()).includes('second-account'))
  const state = await ctx.alcorElearning.remove()
  assert.equal(state.configured, false)
  assert.equal(state.connected, false)
  assert.equal(state.username, undefined)
  assert.equal(await ctx.alcorElearning.briefingSource(), null)
  assert.equal(ctx.tools.executable('mcp__elearning__list_courses'), undefined)
  assert(!(await readFile(filename, 'utf8')).includes('second-synthetic-secret'))
  assert.equal((await ctx.credentials.resolve(credentialRef('UNRELATED_MODEL_API_KEY'))).value, 'synthetic-model-key')
})

test('school connection checks distinguish safe failure categories without showing raw errors', async () => {
  const { checkConnection } = await import('../../packages/alcor-secretary/src/elearning.js')
  const { checkMessage } = await import('../../packages/alcor-secretary/src/elearning-status.js')
  const check = result => checkConnection({ tools: { executable: () => true, execute: async () => ({ results: [{ result }] }) } })
  for (const code of ['browser_unavailable', 'network_error', 'login_form_error', 'login_timeout', 'login_required', 'permission_denied']) {
    const result = await check({ isError: true, error: { message: JSON.stringify({ error_code: code, message: 'synthetic-secret' }) } })
    assert.equal(result.code, code)
    assert.equal(result.status, code === 'login_required' ? 'login_required' : 'query_failed')
    assert(!checkMessage(result).includes('synthetic-secret'))
  }
  assert.deepEqual(await check({ isError: true, error: { message: 'Login: private URL and credentials' } }), { status: 'query_failed', code: 'query_failed' })
  assert.deepEqual(await check({ value: { structuredContent: { value: [] } } }), { status: 'ready', courseCount: 0 })
  assert.equal((await checkConnection({ tools: { executable: () => false } })).status, 'disconnected')
})
