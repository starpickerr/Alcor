import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { legacySession, importHistory } from '../../scripts/product/migrate-legacy-history.mjs'

test('legacy transcripts retain all messages without runnable tools, and retries verify existing history', async () => {
  const root = await mkdtemp(join(tmpdir(), 'stella-history-'))
  try {
    const created_at = '2026-01-01T00:00:00Z'
    const record = { session_id: 'synthetic-old', title: 'Old conversation', created_at, messages: [
      {role:'user',created_at,content:JSON.stringify([{type:'text',text:'Hello'}])},
      {role:'assistant',created_at,content:JSON.stringify([{type:'tool_use',name:'dangerous',input:{}}])},
      {role:'user',created_at,content:JSON.stringify([{type:'tool_result',tool_use_id:'old',content:'Historical result'}])},
    ] }
    const data = legacySession(record, '/tmp')
    assert.equal(data.events.filter(e=>e.type==='user/message').length, 1)
    assert(data.events.filter(e=>e.type==='assistant/message').every(e=>e.data.message.content.every(b=>b.type==='text')))
    assert(!data.events.some(e=>e.type==='tool/call'))
    assert.equal((await importHistory([record], root, '/tmp')).imported, 1)
    assert.equal((await importHistory([record], root, '/tmp')).imported, 0)
    const changed = structuredClone(record)
    changed.messages[0].content = JSON.stringify([{type:'text',text:'Changed'}])
    await assert.rejects(importHistory([changed], root, '/tmp'), /collision/)
    assert.throws(()=>legacySession({...record,messages:[{role:'system',created_at,content:'[]'}]},'/tmp'), /role/)
  } finally { await rm(root, {recursive:true,force:true}) }
})

test('native workspace attachment and paused schedule migration are durable and repeatable', async () => {
  const { importHost } = await import('../../scripts/product/migrate-legacy-host.mjs')
  const { mkdir, readFile } = await import('node:fs/promises')
  const root = await mkdtemp(join(tmpdir(),'stella-host-migration-'))
  try {
    const cwd = join(root,'workspace'); await mkdir(cwd)
    const created_at = '2026-01-01T00:00:00Z'
    const history = [{session_id:'old',title:'Test',created_at,pinned:true,messages:[{role:'user',created_at,content:'[{"type":"text","text":"Test"}]'}]}]
    const schedules = [{id:'old-task',name:'Test schedule',created_at,enabled:false,schedule:{type:'cron',expression:'0 6 * * *'},action:{type:'agent_task',task_description:'Test only'}}]
    const report = await importHost(history,schedules,join(root,'sessions'),join(root,'storages'),cwd)
    assert.equal(report.pinned,1)
    assert.equal(report.schedules,1)
    const read = () => readFile(join(root,'storages/schedule.json'),'utf8').then(JSON.parse)
    const before = await read()
    const task = Object.values(before.tables.tasks)[0]
    assert.equal(task.status,'paused')
    const ownership=JSON.parse(await readFile(join(root,'storages/stella_organizer.json'),'utf8'))
    assert(ownership.tables.schedules[task.record.id])
    assert.equal(task.target.create.agentPreset,'stella')
    await importHost(history,schedules,join(root,'sessions'),join(root,'storages'),cwd)
    assert.deepEqual(await read(),before)
    const workspace = JSON.parse(await readFile(join(root,'storages/workspace.json'),'utf8'))
    assert.equal(Object.values(workspace.tables.workspaces)[0].sessionIds.length,1)
  } finally { await rm(root,{recursive:true,force:true}) }
})
