import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, chmod, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'
import { HubWorker } from '../../packages/alcor-agent-hub/src/worker.js'
import { readBriefingSourcePage, discoverBriefingSources } from '../../packages/alcor-organizer/src/briefing-sources.js'
import { checkMessage as schoolMessage } from '../../packages/alcor-plugin-info/src/elearning-status.js'
import { checkMessage as mailMessage } from '../../packages/alcor-mail/src/check-status.js'

test('both worker boundaries retain startup cause after process error and reject with a remedy', async t => {
  const root = await mkdtemp(join(tmpdir(), 'alcor-worker-diagnostics-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  for (const Worker of [BusinessWorker, HubWorker]) {
    const worker = new Worker(root, { python: join(root, 'missing-python') })
    await assert.rejects(worker.call('probe'), /worker_start_failed.*Python 不存在/)
    await assert.rejects(worker.call('probe'), /worker_start_failed.*Python 不存在/)
  }
})

test('both worker boundaries explain blank replies, protocol failures and uncertain termination', { skip: process.platform === 'win32' }, async t => {
  const root = await mkdtemp(join(tmpdir(), 'alcor-worker-replies-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  const python = join(root, 'synthetic-python')
  for (const [output, expected] of [
    ['print(json.dumps({"id":request["id"],"ok":False,"error":""}),flush=True)', /worker_operation_failed.*核对/],
    ['print(json.dumps({"id":request["id"],"ok":False,"errorType":"OperationalError","error":"private-server-secret"}),flush=True)', /storage_error.*保留数据库/],
    ['print(json.dumps({"id":request["id"],"ok":False,"errorType":"FileNotFoundError","error":"private-server-secret"}),flush=True)', /local_path_missing.*配置路径/],
    ['print("private-server-secret invalid json",flush=True)', /worker_protocol_error.*避免重复/],
    ['sys.exit(7)', /worker_stopped.*7.*避免重复/],
  ]) {
    await writeFile(python, `#!/usr/bin/env python3\nimport sys,json\nfor line in sys.stdin:\n request=json.loads(line)\n ${output}\n`)
    await chmod(python, 0o700)
    for (const Worker of [BusinessWorker, HubWorker]) {
      const worker = new Worker(root, { python })
      try { await assert.rejects(worker.call('probe'), error => expected.test(error.message) && !error.message.includes('private-server-secret')) }
      finally { await worker.close() }
    }
  }
})

test('native business domains propagate safe storage diagnostics through their real worker', async t => {
  const root = await mkdtemp(join(tmpdir(), 'alcor-business-storage-errors-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  const worker = new BusinessWorker(root, { worker: new URL('./fixtures/business-diagnostics-worker.py', import.meta.url) })
  t.after(() => worker.close())
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
