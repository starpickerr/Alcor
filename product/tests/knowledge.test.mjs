import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'

test('knowledge source, citations, original and archive survive retries and worker restarts', async (t) => {
  const dir = await mkdtemp(join(tmpdir(), 'alcor-knowledge-'))
  const launch = () =>
    new BusinessWorker(dir, {
      worker: new URL('../../packages/alcor-secretary/business/knowledge_worker.py', import.meta.url),
    })
  let worker = launch()
  t.after(async () => {
    await worker.close()
    await rm(dir, { recursive: true, force: true })
  })
  const call = (method, args, key) =>
    worker.call('invoke', { domain: 'knowledge', method, arguments: args, request_id: key })
  const args = { title: '课程讲义', text: '复习提纲', url: 'https://example.org/syllabus' }
  const saved = await call('save', args, 'create')
  assert.equal((await call('save', args, 'create')).id, saved.id)
  await assert.rejects(call('save', { ...args, title: 'changed' }, 'create'))
  await assert.rejects(call('save', { ...args, url: 'javascript:alert(1)' }, 'bad'))
  const content = Buffer.from('学期安排\n不考第五章').toString('base64')
  const file = await call(
    'store_file',
    { title: '考试通知', filename: '通知.txt', kind: 'text', content, pages: ['学期安排\n不考第五章'] },
    'file'
  )
  await worker.close()
  worker = launch()
  const found = await call('search', { query: '第五章' })
  assert.equal(found.items[0].id, file.id)
  assert.match(found.items[0].snippet, /不考第五章/)
  assert.equal(found.items[0].page, 1)
  assert.equal((await call('read', { id: file.id })).text, '学期安排\n不考第五章')
  assert.equal((await call('original', { id: file.id })).content, content)
  const changed = await call('edit', {
    id: saved.id,
    revision: saved.revision,
    title: '考试要求',
    text: '第三章不考',
    url: args.url,
  })
  await assert.rejects(call('edit', { id: saved.id, revision: saved.revision, title: '过期编辑', text: 'x' }))
  assert.equal((await call('search', { query: '复习提纲' })).items.length, 0)
  const archived = await call('archive', { id: saved.id, revision: changed.revision })
  assert.equal(
    (await call('search', { query: '考试要求' })).items.some((x) => x.id === saved.id),
    false
  )
  await call('restore', { id: saved.id, revision: archived.revision })
  assert.equal((await call('read', { id: saved.id })).url, args.url)
})

import { indexKnowledgeFile, readKnowledgeFile } from '../../packages/alcor-organizer/src/knowledge-files.js'
import {
  knowledgeToolAllowed,
  registerKnowledgeTool,
} from '../../packages/alcor-secretary/src/knowledge-tool.js'
import { classifyCapability } from '../../packages/alcor-secretary/src/security-policy.js'

test('folder deletion checks its confirmed scope and preserves shared originals', async (t) => {
  const dir = await mkdtemp(join(tmpdir(), 'alcor-knowledge-folder-'))
  const worker = new BusinessWorker(dir, {
    worker: new URL('../../packages/alcor-secretary/business/knowledge_worker.py', import.meta.url),
  })
  t.after(async () => {
    await worker.close()
    await rm(dir, { recursive: true, force: true })
  })
  const call = (method, args, key) =>
    worker.call('invoke', { domain: 'knowledge', method, arguments: args, request_id: key })
  const path = '课程'
  await call('create_folder', { name: path })
  await call('create_folder', { parent: path, name: '讲义' })
  const sibling = '课程表'
  await call('create_folder', { name: sibling })
  const args = {
    filename: '手册.txt', title: '手册', kind: 'text',
    content: Buffer.from('binary trees').toString('base64'), pages: ['binary trees'],
  }
  const file = await call('store_file', { ...args, collection: path + '/讲义' })
  const shared = await call('store_file', { ...args, collection: sibling })
  const plan = await call('plan_folder_delete', { path })
  assert.deepEqual([plan.documents, plan.folders], [1, 1])
  const draft = { title: '空白文件', collection: path }
  const note = await call('create_note', draft, 'empty-note')
  assert.equal((await call('create_note', draft, 'empty-note')).id, note.id)
  assert.equal((await call('read', { id: note.id })).text, '')
  await assert.rejects(call('delete_folder', { path, fingerprint: plan.fingerprint }), /内容已变化/)
  assert.equal((await call('read', { id: file.id })).text, 'binary trees')
  const updated = await call('plan_folder_delete', { path })
  assert.deepEqual([updated.documents, updated.folders], [2, 1])
  await call('delete_folder', { path, fingerprint: updated.fingerprint })
  assert.deepEqual(await call('collections', {}), [sibling])
  assert.equal((await call('original', { id: shared.id })).content, args.content)
  await assert.rejects(call('read', { id: file.id }), /找不到/)
  await assert.rejects(call('create_note', draft, 'new-empty-note'), /文件夹已不可用/)
})

test('knowledge import reads bounded bytes through the active filesystem, and search never exposes the file payload', async () => {
  let tool, saved
  const ctx = {
    tools: {
      register: (value) => {
        tool = value
      },
    },
    alcorOrganizer: {
      business: async (input) => {
        saved = input
        if (input.method === 'read')
          return {
            id: 'id',
            title: 'Lecture',
            filename: 'a.txt',
            text: 'a'.repeat(10010),
            note: 'annotation',
          }
        return { id: 'id', title: 'Lecture' }
      },
    },
  }
  const main = { session: { id: 'owner', header: {} }, ctx: { get: () => fs } }
  const fs = {
    resolve: async (x) => x,
    stat: async () => ({ type: 'file', size: 4 }),
    readBytes: async () => Buffer.from('text'),
  }
  registerKnowledgeTool(ctx, (agent) => agent === main)
  const execute = (agent, args) =>
    tool.execute(args, { agent, callId: 'one', signal: AbortSignal.timeout(1000) })
  await execute(main, { action: 'import', path: '/fixtures/note.txt' })
  assert.equal(saved.arguments.content, Buffer.from('text').toString('base64'))
  const indexed = await indexKnowledgeFile(saved.arguments)
  assert.deepEqual(indexed.pages, ['text'])
  const longText = 'a'.repeat(68000) + ' searchable tail'
  const imported = await indexKnowledgeFile({
    filename: 'long.md',
    content: Buffer.from(longText).toString('base64'),
  })
  assert.equal(imported.pages.join(''), longText)
  let cacheKey
  const cached = await indexKnowledgeFile(saved.arguments, (hash) => {
    cacheKey = hash
    return { pages: ['cached extraction'], index_notice: '' }
  })
  assert.match(cacheKey, /^[0-9a-f]{64}$/)
  assert.deepEqual(cached.pages, ['cached extraction'])
  for (const action of ['graph', 'links']) {
    assert.equal(classifyCapability('alcor_knowledge', { action }).kind, 'read')
    await assert.rejects(execute({ session: { id: 'child', header: { parentSession: 'p' } } }, { action }), /主对话/)
  }
  await execute(main, { action: 'links', id: 'id', offset: 25 })
  assert.equal(saved.arguments.offset, 25)
  const body = await execute(main, { action: 'read', id: 'id' })
  assert.equal(body.next_offset, 10000)
  assert.equal((await execute(main, { action: 'read', id: 'id', offset: 10000 })).text, 'a'.repeat(10))
  for (const session of [
    { id: 'child', header: { parentSession: 'p' } },
    { id: 'child', header: { origin: 'subagent' } },
    { id: 'onebot-group-123', header: {} },
  ]) {
    assert(!knowledgeToolAllowed({ session }, () => true))
  }
  await assert.rejects(execute({ session: { header: {} } }, { action: 'search', query: 'private' }), /主对话/)
  assert(!knowledgeToolAllowed(main, () => false))
  assert.equal(
    classifyCapability('alcor_business', { domain: 'knowledge', method: 'original' }).kind,
    'unclassified'
  )
  assert.equal(classifyCapability('alcor_knowledge', { action: 'original' }).kind, 'unclassified')
  await assert.rejects(
    readKnowledgeFile(
      {
        ...fs,
        resolve: async () => {
          throw Error('denied')
        },
      },
      '/fixtures/x.pdf'
    ),
    /denied/
  )
  await assert.rejects(indexKnowledgeFile({ filename: 'foo.png', content: 'dGV4dA==' }), /格式/)
  await assert.rejects(indexKnowledgeFile({ filename: 'other.html', content: 'dGV4dA==' }))
})

test('hybrid retrieval preserves source versions, storage deduplication and collection boundaries', async () => {
  await promisify(execFile)(
    process.env.ALCOR_BUSINESS_PYTHON || (process.platform === 'win32' ? 'python' : 'python3'),
    ['-B', fileURLToPath(new URL('./knowledge-retrieval.py', import.meta.url))],
    { timeout: 45000 }
  )
})

test('a v1 library acquires durable, navigable cross-references on upgrade', async () => {
  await promisify(execFile)(
    process.env.ALCOR_BUSINESS_PYTHON || (process.platform === 'win32' ? 'python' : 'python3'),
    ['-B', fileURLToPath(new URL('./knowledge-graph.py', import.meta.url))],
  )
})
