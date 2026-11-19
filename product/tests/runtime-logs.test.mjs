import test from 'node:test'
import assert from 'node:assert/strict'
import { Context } from '@deepseek-ai/cordis'
import { RuntimeLogs, redactLog, projectLog, recordSourceFailure } from '../../packages/alcor-secretary/src/log-host.js'
import { logSchemas } from '../../packages/alcor-secretary/src/log-contract.js'

async function fixture(t, saved = new Map()) {
  const ctx = new Context()
  let fail = false
  const table = { get: key => structuredClone(saved.get(key)), put: async (key, value) => {
    if (fail) throw new Error('Synthetic disk failure')
    saved.set(key, structuredClone(value))
  } }
  const logs = new RuntimeLogs(ctx, table)
  t.after(async () => { await logs.close(); await ctx.fiber.dispose() })
  return { ctx, logs, saved, fail: value => { fail = value } }
}

test('native exporter retains bounded, redacted history and filters before paginating', async t => {
  const { ctx, logs, saved } = await fixture(t)
  for (let i = 0; i < 1005; i++) ctx.logger('alcor-organizer').info('record %s', i)
  ctx.logger('apple-sync').warn('Bearer synthetic-secret\npassword="other-secret"\nuser@example.org')
  const latest = logSchemas[1].parse(logs.list({}))
  assert.equal(latest.retained, 1000)
  assert.equal(latest.entries.length, 100)
  assert.deepEqual(latest.sources, ['Alcor', 'Alcor：事务', 'Alcor：日程', 'Alcor：简报', 'Alcor：账单', 'Alcor：自动化', 'Apple 同步', 'eLearning', '邮件'])
  assert(!JSON.stringify(latest).includes('synthetic-secret'))
  assert(!JSON.stringify(latest).includes('other-secret'))
  assert(!JSON.stringify(latest).includes('user@example.org'))
  assert.equal(logs.list({ level: 'warn', source: 'Apple 同步' }).entries.length, 1)
  assert.equal(logs.list({ query: 'record 1004' }).entries.length, 1)
  assert.equal(logs.list({ since: '2099-01-01T00:00:00.000Z' }).entries.length, 0)
  const older = logs.list({ before: latest.nextBefore })
  assert.equal(older.entries[0].id, latest.entries.at(-1).id - 1)
  assert(!older.entries.some(row => latest.entries.some(current => row.id === current.id)))
  await logs.flush()
  assert.equal(logs.list({}).storage, 'saved')
  const restored = await fixture(t, saved)
  assert.deepEqual(restored.logs.list({}).entries, latest.entries)
  restored.ctx.logger('secretary').info('after restart')
  assert(restored.logs.list({}).entries[0].id > latest.entries[0].id)
  await assert.rejects(async () => logs.list({ before: -1 }))
  assert.throws(() => logs.list({ query: 'x'.repeat(201) }))
  assert.throws(() => logs.list({ path: '/etc/passwd' }))
})

test('disk failures stay visible, recover on retry and never interrupt a host log call', async t => {
  const { ctx, logs, fail } = await fixture(t)
  fail(true)
  assert.doesNotThrow(() => ctx.logger('alcor-secretary').error('diagnostic'))
  await logs.flush()
  assert.equal(logs.list({}).storage, 'failed')
  assert.equal(logs.list({}).entries.length, 1)
  fail(false)
  await logs.flush()
  assert.equal(logs.list({}).storage, 'saved')
  await logs.close()
  ctx.logger('alcor-secretary').info('ignored after disposal')
  assert.equal(logs.list({}).entries.length, 1)
})

test('debug bursts cannot displace normal records in storage or after restart', async t => {
  const loggerName = 'alcor-organizer'
  const history = Array.from({ length: 300 }, (_, index) => ({
    id: index + 1, time: '2026-11-17T00:00:00.000Z', level: index === 0 ? 'error' : 'debug',
    source: 'Alcor', message: 'legacy ' + index,
  }))
  const saved = new Map([['history', { entries: history }]])
  const { ctx, logs } = await fixture(t, saved)
  assert.equal(logs.list({ level: 'debug' }).matched, 100)
  assert.equal(logs.list({ level: 'debug' }).entries.at(-1).id, 201)
  for (let i = 0; i < 998; i++) ctx.logger(loggerName).info('operation %s', i)
  for (let i = 0; i < 1100; i++) ctx.logger(loggerName).debug('background %s', i)
  assert.equal(logs.list({ level: 'normal' }).matched, 999)
  assert.equal(logs.list({ level: 'debug' }).matched, 1)
  assert.equal(logs.list({ level: 'error' }).entries[0].message, 'legacy 0')
  ctx.logger(loggerName).info('last operation')
  ctx.logger(loggerName).debug('full buffer')
  await logs.flush()
  const restored = await fixture(t, saved)
  assert.equal(restored.logs.list({ level: 'normal' }).matched, 1000)
  assert.equal(restored.logs.list({ level: 'debug' }).matched, 0)
  assert.equal(restored.logs.list({ level: 'error' }).matched, 1)
})

test('credential formats are removed before storage and before truncating multiline output', async t => {
  const input = 'https://name:pass@host.test/api?token=abc&key=def api_key=xyz Authorization: Bearer secret password: "long secret" {"refresh_token":"abc"} Cookie: "session=abc" /Users/private/Desktop sk-synthetic123'
  const redacted = redactLog(input)
  for (const secret of ['name:pass', '=abc', '=def', '=xyz', 'Bearer secret', 'long secret', '"abc"', 'session=abc', '/Users/private', 'sk-synthetic123']) assert(!redacted.includes(secret), secret)
  const { ctx, logs } = await fixture(t)
  ctx.logger('alcor-secretary').error('prefix\n' + 'x'.repeat(5000) + '\npassword="private"')
  const result = logs.list({}).entries[0]
  assert(result.message.length <= 4000)
  assert(!result.message.includes('private'))
})

test('business sources remain distinct and are selectable even before producing logs', async t => {
  const { ctx, logs } = await fixture(t)
  assert.equal(logs.list({ source: 'Alcor：简报' }).entries.length, 0)
  assert(logs.list({}).sources.includes('Alcor：简报'))
  for (const [domain, source] of [['tasks', 'Alcor：事务'], ['calendar', 'Alcor：日程'], ['briefing', 'Alcor：简报'], ['bills', 'Alcor：账单'], ['automation', 'Alcor：自动化']]) {
    ctx.logger('alcor-' + domain).info('synthetic successful operation')
    assert.equal(logs.list({ source }).entries.length, 1, source)
  }
  const old = new Map([['history', { entries: [{ id: 1, time: '2026-11-17T00:00:00.000Z', level: 'warn', source: '事务与简报', message: 'legacy diagnostic' }] }]])
  const restored = await fixture(t, old)
  assert.equal(restored.logs.list({}).entries[0].source, 'Alcor')
  assert.equal(restored.logs.list({}).entries[0].message, 'legacy diagnostic')
})

test('execution outcome is explicit and independent of severity, with compatible stored history', async t => {
  const { ctx, logs, saved } = await fixture(t)
  const id = '3cc58a30-2693-4fe3-896d-61319e5455e6'
  ctx.logger('alcor-tasks').info('Ordinary information mentioning 执行成功')
  ctx.logger('alcor-tasks').error('A diagnostic without an operation outcome')
  ctx.logger('alcor-tasks').info(`[worker_operation_succeeded] tasks.create_todo 执行成功 · 耗时 42 ms · 请求 ${id}`)
  ctx.logger('alcor-tasks').debug(`[worker_operation_succeeded] tasks.get_todo 执行成功 · 耗时 0 ms · 请求 ${id}`)
  ctx.logger('alcor-tasks').warn(`[worker_timeout] tasks.update_todo · 请求 ${id}`)
  assert.equal(logs.list({ outcome: 'success' }).matched, 2)
  const normal = logSchemas[1].parse(logs.list({ outcome: 'success', level: 'normal', source: 'Alcor：事务' }))
  assert.equal(normal.entries.length, 1)
  assert.deepEqual([normal.entries[0].operation, normal.entries[0].durationMs, normal.entries[0].requestId], ['tasks.create_todo', 42, id])
  assert.equal(logs.list({ outcome: 'failure' }).entries[0].level, 'warn')
  assert.equal(logs.list({ outcome: 'failure' }).entries[0].outcome, 'failure')
  assert.equal(logs.list({ category: 'success' }).matched, 1)
  assert.equal(logs.list({ category: 'debug' }).entries[0].outcome, 'success')
  assert.equal(logs.list({ category: 'failure' }).entries[0].level, 'error')
  assert.equal(logs.list({ category: 'failure' }).matched, 1)
  assert.equal(logs.list({ category: 'warn' }).matched, 1)
  assert.equal(logs.list({ category: 'error' }).matched, 1)
  assert.equal(logs.list({ category: 'info' }).matched, 1)
  assert.throws(() => logs.list({ category: 'invented' }))
  assert.equal(logs.list({ query: 'Ordinary' }).entries[0].outcome, null)
  assert.equal(logs.list({ level: 'error' }).entries[0].outcome, null)
  await logs.flush()
  assert(!('outcome' in saved.get('history').entries[0]), 'Stored records retain the previous schema')
  assert.throws(() => logs.list({ outcome: 'invented' }))
  const overflow = projectLog({ message: `[worker_operation_succeeded] tasks.test 执行成功 · 耗时 ${'9'.repeat(400)} ms · 请求 ${id}` })
  assert.equal(overflow.durationMs, null)
})

test('outcome filtering happens before pagination and keeps the full matching count', async t => {
  const { ctx, logs } = await fixture(t)
  for (let i = 0; i < 205; i++) ctx.logger('alcor-tasks').info('[worker_operation_succeeded] tasks.list_todos 执行成功')
  const first = logs.list({ outcome: 'success' })
  const second = logs.list({ outcome: 'success', before: first.nextBefore })
  const third = logs.list({ outcome: 'success', before: second.nextBefore })
  assert.deepEqual([first.matched, second.matched, third.matched], [205, 205, 205])
  assert.deepEqual([first.entries.length, second.entries.length, third.entries.length], [100, 100, 5])
  assert.equal(third.nextBefore, null)
})

test('source tool failures keep actionable codes and exclude remote response contents', async t => {
  const { ctx, logs } = await fixture(t)
  recordSourceFailure(ctx, { name: 'mcp__email__test_connection' }, { value: { structuredContent: { value: { connected: true, folder_accessible: false } } } })
  recordSourceFailure(ctx, { name: 'mcp__elearning__fetch' }, { isError: true, error: { message: JSON.stringify({ error_code: 'login_required', message: 'private-content' }) } })
  recordSourceFailure(ctx, { name: 'mcp__unrelated__fetch' }, { isError: true })
  const page = logSchemas[1].parse(logs.list({ category: 'failure' }))
  assert.deepEqual(page.entries.map(row => [row.source, row.operation, row.diagnostic.code]),
    [['eLearning', 'elearning.fetch', 'login_required'], ['邮件', 'email.test_connection', 'folder_denied']])
  assert.match(page.entries[1].diagnostic.reason, /文件夹/)
  assert(!JSON.stringify(page).includes('private-content'))
})

test('old Apple existence probes become bounded debug history while direct failures remain visible', async t => {
  const message = '[worker_operation_failed] calendar.get_event · KeyError · 请求 3cc58a30-2693-4fe3-896d-61319e5455e6\n原因：event not found\n位置：worker.py:574 in apple_peer'
  const rows = Array.from({ length: 201 }, (_, index) => ({
    id: index + 1, time: '2026-11-18T00:00:00.000Z', level: 'error', source: '日程',
    message: index === 0 ? message.replace(' in apple_peer', ' in _invoke') : message,
  }))
  const { logs } = await fixture(t, new Map([['history', { entries: rows }]]))
  assert.equal(logs.list({ level: 'normal' }).matched, 1)
  assert.equal(logs.list({ category: 'failure' }).matched, 1)
  assert.equal(logs.list({ category: 'debug' }).matched, 100)
  assert.equal(logs.list({ category: 'failure' }).entries[0].id, 1)
})

test('unmodified third-party loggers share host collection, filtering, redaction and restart', async t => {
  const { ctx, logs, saved } = await fixture(t)
  const plugin = ctx.extend()
  plugin.logger('@vendor/weather').info('Forecast ready token=private-weather')
  plugin.logger('@vendor/weather').warn(new Error('Provider unavailable'))
  plugin.logger('@vendor/notes').debug('Background lookup')
  const otherHost = new Context()
  t.after(() => otherHost.fiber.dispose())
  otherHost.logger('@vendor/private').error('Another profile')
  const page = logSchemas[1].parse(logs.list({ source: '@vendor/weather' }))
  assert.equal(page.matched, 2)
  assert(page.sources.includes('@vendor/weather'))
  assert(page.sources.includes('@vendor/notes'))
  assert(!page.sources.includes('@vendor/private'))
  assert.deepEqual(page.entries.map(row => row.level), ['warn', 'info'])
  assert(page.entries.every(row => row.outcome === null && row.durationMs === null))
  assert(page.entries[0].message.includes('Provider unavailable'))
  assert(!JSON.stringify(page).includes('private-weather'))
  assert.equal(logs.list({ source: '@vendor/notes', level: 'normal' }).matched, 0)
  assert.equal(logs.list({ query: 'no-match' }).sources.filter(name => name === '@vendor/weather').length, 1)
  await logs.flush()
  assert(!JSON.stringify(saved.get('history')).includes('private-weather'))
  const restored = await fixture(t, saved)
  assert.deepEqual(restored.logs.list({ source: '@vendor/weather' }).entries, page.entries)
  assert(restored.logs.list({}).sources.includes('@vendor/weather'))
})

test('source names are bounded and redacted before storage and remain selectable', async t => {
  const { ctx, logs, saved } = await fixture(t)
  ctx.logger('token=private-source /Users/private/' + 'x'.repeat(150)).info('diagnostic')
  ctx.logger('').info('unnamed host diagnostic')
  const page = logSchemas[1].parse(logs.list({}))
  const source = page.entries.find(row => row.message === 'diagnostic').source
  assert(source.length <= 100)
  assert(!source.includes('private-source'))
  assert(!source.includes('/Users/private'))
  assert.equal(logs.list({ source }).matched, 1)
  assert.equal(logs.list({ source: 'DSH' }).matched, 1)
  await logs.flush()
  assert(!JSON.stringify(saved.get('history')).includes('private-source'))
})

test('source labels follow plugin ownership across old histories and restart', async t => {
  const independent = ['Apple 同步', 'eLearning', '邮件', 'AgentHub']
  const labels = ['事务', '日程', '简报', '账单', '自动化', ...independent, ...independent.map(name => `Alcor：${name}`), 'Alcor：事务', '@vendor/weather', 'Alcor：custom-plugin']
  const history = labels.map((source, index) => ({ id: index + 1, source, level: 'info', time: '2026-11-18T00:00:00.000Z', message: 'historical diagnostic' }))
  const { logs, saved } = await fixture(t, new Map([['history', { entries: history }]]))
  const page = logSchemas[1].parse(logs.list({}))
  assert.equal(page.matched, labels.length)
  assert.equal(logs.list({ source: 'Alcor：事务' }).matched, 2)
  for (const source of independent) {
    assert.equal(logs.list({ source }).matched, 2)
    assert(!page.sources.includes(`Alcor：${source}`))
  }
  assert.equal(logs.list({ source: '@vendor/weather' }).matched, 1)
  assert.equal(logs.list({ source: 'Alcor：custom-plugin' }).matched, 1)
  assert(!page.sources.includes('事务'))
  assert(!page.sources.some(name => name.startsWith('Alcor：Alcor：')))
  await logs.flush()
  const restored = await fixture(t, saved)
  assert.deepEqual(restored.logs.list({}).entries, page.entries)
})

test('independent Alcor ecosystem plugins retain their own source names', async t => {
  const { ctx, logs } = await fixture(t)
  for (const [logger, source] of [
    ['@alcor/apple-sync', 'Apple 同步'], ['apple-sync', 'Apple 同步'], ['apple', 'Apple 同步'],
    ['alcor-elearning', 'eLearning'], ['school-remote', 'eLearning'],
    ['mail-remote', '邮件'], ['mail-connection', '邮件'], ['@alcor/mail', '邮件'],
    ['alcor-agent-hub', 'AgentHub'],
  ]) {
    ctx.logger(logger).info('plugin diagnostic')
    assert.equal(logSchemas[1].parse(logs.list({})).entries[0].source, source, logger)
  }
  for (const source of ['Apple 同步', 'eLearning', '邮件', 'AgentHub']) {
    assert(logs.list({ source }).matched > 0, source)
    assert.equal(logs.list({ source: `Alcor：${source}` }).matched, 0)
  }
})
