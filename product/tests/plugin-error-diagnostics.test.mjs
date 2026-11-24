import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, rm, symlink } from 'node:fs/promises'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'
import { projectLog } from '../../packages/alcor-secretary/src/log-host.js'
import { readBriefingSourcePage, discoverBriefingSources } from '../../packages/alcor-organizer/src/briefing-sources.js'
import { checkMessage as schoolMessage } from '../../packages/alcor-plugin-info/src/elearning-status.js'
import { checkMessage as mailMessage } from '../../../dsh-email/plugin/src/check-status.js'

test('business worker boundary retain startup cause after process error and reject with a remedy', async t => {
  const root = await mkdtemp(join(tmpdir(), 'alcor-worker-diagnostics-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  for (const Worker of [BusinessWorker]) {
    const worker = new Worker(root, { python: join(root, 'missing-python') })
    await assert.rejects(worker.call('probe'), /worker_start_failed.*Python 不存在/)
    await assert.rejects(worker.call('probe'), /worker_start_failed.*Python 不存在/)
  }
})

test('business worker diagnostics identify failed requests without logging their contents', async t => {
  const root = await mkdtemp(join(tmpdir(), 'alcor-safe-runtime-logs-'))
  const messages = []
  const worker = new BusinessWorker(root, { worker: new URL('./fixtures/business-diagnostics-worker.py', import.meta.url),
    diagnostic: (message, level) => messages.push({ message, level }) })
  t.after(async () => {
    await worker.close()
    await rm(root, { recursive: true, force: true })
  })
  await assert.rejects(worker.call('invoke', { domain: 'bills', method: 'list_ledger', arguments: { private: 'must-not-appear' } }))
  assert(messages.some(row => row.level === 'info' && /已启动/.test(row.message)))
  assert(messages.some(row => row.level === 'error' && /bills.list_ledger.*请求 [a-f0-9-]{36}/.test(row.message)))
  assert(!JSON.stringify(messages).includes('must-not-appear'))
  assert(!JSON.stringify(messages).includes('private-server-secret'))
})

test('successful worker operations log their identity and duration without inputs or results', async t => {
  const root = await mkdtemp(join(tmpdir(), 'alcor-success-logs-'))
  const script = join(root, 'worker.py')
  await writeFile(script, 'import sys,json\nfor line in sys.stdin:\n request=json.loads(line)\n print(json.dumps({"id":request["id"],"ok":True,"value":{"private":"private-result"}}),flush=True)\n')
  const messages = []
  const worker = new BusinessWorker(root, { worker: pathToFileURL(script), diagnostic: (message, level, domain) => messages.push({ message, level, domain }) })
  t.after(async () => {
    await worker.close()
    await rm(root, { recursive: true, force: true })
  })
  assert.deepEqual(await worker.call('invoke', { domain: 'tasks', method: 'create_todo', arguments: { title: 'private-input' } }), { private: 'private-result' })
  await worker.call('apple_peer', { domain: 'calendar', method: 'get_event', args: ['private-input'] })
  await worker.call('due_reminders')
  assert(messages.some(row => row.level === 'info' && /tasks.create_todo 执行成功 · 耗时 \d+ ms · 请求 [a-f0-9-]{36}/.test(row.message)))
  assert(messages.some(row => row.level === 'debug' && /calendar.get_event 执行成功/.test(row.message)))
  assert.equal(messages.find(row => /tasks.create_todo/.test(row.message)).domain, 'tasks')
  assert.equal(messages.find(row => /calendar.get_event/.test(row.message)).domain, 'calendar')
  assert.equal(messages.find(row => /due_reminders/.test(row.message)).level, 'debug')
  assert(!JSON.stringify(messages).includes('private-input'))
  assert(!JSON.stringify(messages).includes('private-result'))
})

test('business worker boundary explain blank replies, protocol failures and uncertain termination', async t => {
  const root = await mkdtemp(join(tmpdir(), 'alcor-worker-replies-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  const script = join(root, 'synthetic-worker.py')
  for (const [output, expected] of [
    ['print(json.dumps({"id":request["id"],"ok":False,"error":""}),flush=True)', /worker_operation_failed.*核对/],
    ['print(json.dumps({"id":request["id"],"ok":False,"errorType":"OperationalError","error":"private-server-secret"}),flush=True)', /storage_error.*保留数据库/],
    ['print(json.dumps({"id":request["id"],"ok":False,"errorType":"FileNotFoundError","error":"private-server-secret"}),flush=True)', /local_path_missing.*配置路径/],
    ['print("private-server-secret invalid json",flush=True)', /worker_protocol_error.*避免重复/],
    ['sys.exit(7)', /worker_stopped.*7.*避免重复/],
  ]) {
    await writeFile(script, `import sys,json\nfor line in sys.stdin:\n request=json.loads(line)\n ${output}\n`)
    for (const Worker of [BusinessWorker]) {
      const worker = new Worker(root, { worker: pathToFileURL(script) })
      try { await assert.rejects(worker.call('probe'), error => expected.test(error.message) && !error.message.includes('private-server-secret')) }
      finally { await worker.close() }
    }
  }
})

test('native business domains propagate safe storage diagnostics through their real worker', async t => {
  const root = await mkdtemp(join(tmpdir(), 'alcor-business-storage-errors-'))
  const worker = new BusinessWorker(root, { worker: new URL('./fixtures/business-diagnostics-worker.py', import.meta.url) })
  t.after(async () => {
    await worker.close()
    await rm(root, { recursive: true, force: true })
  })
  await assert.rejects(worker.call('invoke', { domain: 'briefing', method: 'get_preferences' }), /peer_outcome_unknown.*勿直接重复执行/)
  for (const [domain, read, write] of [
    ['bills', 'list_ledger', 'create_ledger'], ['tasks', 'list_todos', 'create_todo'],
    ['calendar', 'list_events', 'create_event'], ['briefing', 'list_briefings', 'create_rule'],
  ]) {
    for (const method of [read, write]) {
      await assert.rejects(worker.call('invoke', { domain, method, arguments: {} }), error => /storage_error.*检查数据目录/.test(error.message) && !error.message.includes('private-server-secret'))
    }
  }
})

test('MCP error categories survive source reading and source discovery', async () => {
  const ctx = { get: () => undefined, tools: {
    definitions: () => [{ name: 'mcp__email__list_sources' }, { name: 'mcp__email__fetch_messages' }],
    execute: async () => ({ results: [{ result: { isError: true, error: { message: JSON.stringify({ error_code: 'dns_error', message: 'DNS 失败，请检查代理。' }) } } }] }),
  } }
  await assert.rejects(readBriefingSourcePage(ctx, { source: 'email', tool: 'mcp__email__fetch_messages', method: 'fetch_messages', arguments: {} }, {}), /mcp__email__fetch_messages：\[dns_error\].*检查代理/)
  assert.match((await discoverBriefingSources(ctx)).errors[0], /dns_error.*检查代理/)
})

test('connection pages distinguish proxy DNS, local paths and mailbox login stages', () => {
  assert.match(schoolMessage({ status: 'failed', code: 'proxy_dns_fake_ip' }), /fake-ip-filter/)
  assert.match(schoolMessage({ status: 'failed', code: 'local_file_error' }), /父目录/)
  assert.match(schoolMessage({ status: 'failed', code: 'storage_error' }), /数据库/)
  assert.match(mailMessage({ status: 'failed', code: 'storage_error' }), /数据库/)
  assert.match(mailMessage({ status: 'failed', code: 'folder_denied' }), /已登录/)
  assert.match(mailMessage({ status: 'failed', code: 'mail_server_error' }), /不能据此认定授权码错误/)
})

test('a missing task retains its original domain exception and source location across RPC', async t => {
  const root = await mkdtemp(join(tmpdir(), 'alcor-missing-todo-'))
  const messages = []
  const linked = join(root, 'plugin-business')
  await symlink(fileURLToPath(new URL('../../packages/alcor-secretary/business/', import.meta.url)), linked, 'junction')
  const worker = new BusinessWorker(root, { worker: pathToFileURL(join(linked, 'worker.py')),
    diagnostic: message => messages.push(message) })
  t.after(async () => {
    await worker.close()
    await rm(root, { recursive: true, force: true })
  })
  await assert.rejects(worker.call('invoke', { domain: 'tasks', method: 'get_todo', arguments: { todo_id: 'private-item-id' } }), /not_found/)
  const failure = messages.map(message => projectLog({ message })).find(row => row.outcome === 'failure')
  assert.equal(failure.diagnostic.code, 'KeyError')
  assert.equal(failure.diagnostic.businessCode, 'not_found')
  assert.equal(failure.diagnostic.reason, 'todo not found')
  assert.match(failure.diagnostic.locations.at(-1), /^tasks\/domain\/service\.py:\d+ in get_todo$/)
  assert.ok(failure.requestId && failure.durationMs >= 0)
  assert(!JSON.stringify(messages).includes('private-item-id'))
})

test('only missing Apple object probes use debug while genuine query and peer errors retain errors', async t => {
  const root = await mkdtemp(join(tmpdir(), 'alcor-probe-logs-'))
  const rows = []
  const worker = new BusinessWorker(root, {
    worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url),
    diagnostic: (message, level) => rows.push({ message, level }),
  })
  t.after(async () => {
    await worker.close()
    await rm(root, { recursive: true, force: true })
  })
  for (const [domain, method] of [['calendar', 'get_event'], ['tasks', 'get_todo']]) {
    await assert.rejects(worker.call('apple_peer', { domain, method, args: ['synthetic-missing'] }), /not found/)
    assert.equal(rows.at(-1).level, 'debug')
    assert.match(rows.at(-1).message, /KeyError/)
  }
  await assert.rejects(worker.call('invoke', { domain: 'tasks', method: 'get_todo', arguments: { todo_id: 'synthetic-missing' } }))
  assert.equal(rows.at(-1).level, 'error')
  await assert.rejects(worker.call('apple_peer', { domain: 'calendar', method: 'get_event', args: [] }))
  assert.equal(rows.at(-1).level, 'error')
  await assert.rejects(worker.call('apple_peer', { domain: 'calendar', method: 'unknown' }))
  assert.equal(rows.at(-1).level, 'error')
  await worker.call('invoke', { domain: 'tasks', method: 'list_todos', arguments: {} })
  assert.equal(rows.at(-1).level, 'debug')
})
