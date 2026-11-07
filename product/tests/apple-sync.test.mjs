import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm, writeFile, readFile, chmod } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL, fileURLToPath } from 'node:url'
import { BusinessWorker } from '../../packages/stella-organizer/src/business.js'

test('independent Apple worker shares business records over reverse RPC and stops its helpers', async context => {
  const directory = await mkdtemp(join(tmpdir(), 'stella-apple-rpc-'))
  const helper = fileURLToPath(new URL('./fake-eventkit.py', import.meta.url))
  await chmod(helper, 0o755)
  const fixture = join(directory, 'worker.py')
  await writeFile(fixture, `import os,sys\nsys.path.insert(0,${JSON.stringify(fileURLToPath(new URL('../../packages/stella-apple-sync/python', import.meta.url)))})\nos.environ['STELLA_EVENTKIT_HELPER']=${JSON.stringify(helper)}\nos.environ['STELLA_EVENTKIT_STATE']=${JSON.stringify(join(directory, 'eventkit-state.json'))}\nfrom worker import main\nmain()\n`)
  const business = new BusinessWorker(join(directory, 'business'), { worker: new URL('../../packages/stella-secretary/business/worker.py', import.meta.url) })
  let apple = new BusinessWorker(join(directory, 'legacy'), { worker: pathToFileURL(fixture), onRequest: input => business.call('apple_peer', input) })
  context.after(async () => { await apple.close(); await business.close(); await rm(directory, { recursive: true, force: true }) })
  await apple.call('initialize')
  const call = (method, args = {}) => apple.call('invoke', { method, arguments: args })
  for (const kind of ['calendar', 'reminders']) {
    await call('set_auto', { kind, enabled: false })
    const started = await call('start_sync', { kind, selections: [{ apple_id: 'a' }] })
    assert.equal(started.result.imported, 1)
    assert.equal(started.state.direction, 'bidirectional')
    assert.equal(started.state.auto_enabled, true)
    assert.equal(started.state.links[0].apple_name, 'Synthetic source')
    assert.equal(started.state.links[0].stella_name, 'Synthetic source')
    assert.equal(started.state.categories.find(item => item.id === started.state.links[0].stella_id).name, 'Synthetic source')
    await call('set_auto', { kind, enabled: false })
    assert.equal((await call('sync', { kind })).imported, 0)
  }
  // Old single-direction settings remain unchanged until explicitly starting sync.
  for (const kind of ['calendar', 'reminders']) {
    const state = await call('status', { kind })
    await call('replace_links', { kind, links: state.links.map(link => ({ ...link, direction: 'import' })) })
  }
  const records = await business.call('records')
  assert.equal(records.filter(row => row.title.startsWith('Synthetic Apple')).length, 2)
  assert.equal(records.find(row => row.kind === 'task').business.list_name, 'Synthetic source')
  assert.ok(records.find(row => row.kind === 'event').business.category_id)
  const event = records.find(row => row.kind === 'event')
  await business.call('apple_peer', { domain: 'calendar', method: 'delete_event', args: [event.id] })
  await call('preview', { calendars: ['a'], direction: 'import' })
  await apple.close()
  const filenames = ['apple-calendar.sqlite3', 'apple-reminders.sqlite3']
  const originals = await Promise.all(filenames.map(name => readFile(join(directory, 'legacy', name))))
  apple = new BusinessWorker(join(directory, 'apple'), { worker: pathToFileURL(fixture), onRequest: input => business.call('apple_peer', input) })
  await apple.call('initialize', { legacy_directory: join(directory, 'legacy') })
  assert.equal((await call('status')).connected, true)
  for (const kind of ['calendar', 'reminders']) {
    const status = await call('status', { kind })
    assert.equal(status.links.length, 1)
    assert.equal(status.links[0].direction, 'import')
    assert.equal(status.auto_enabled, false)
    assert.equal(status.links[0].stella_name, 'Synthetic source')
    assert.deepEqual(status.calendar_ids ?? status.list_ids, ['a'])
    const started = await call('start_sync', { kind, selections: [{ apple_id: 'a', stella_id: status.links[0].stella_id }] })
    assert.equal(started.state.direction, 'bidirectional')
    assert.equal(started.state.auto_enabled, true)
    assert.equal(started.state.links[0].stella_id, status.links[0].stella_id)
    await call('set_auto', { kind, enabled: false })
    for (const selections of [[], [{ apple_id: 'missing' }], [{ apple_id: 'a', stella_id: 'missing' }], [{ apple_id: 'a' }, { apple_id: 'a' }]]) {
      await assert.rejects(call('start_sync', { kind, selections }))
      assert.equal((await call('status', { kind })).links[0].stella_id, status.links[0].stella_id)
    }
  }
  for (let index = 0; index < filenames.length; index++) {
    assert.deepEqual(await readFile(join(directory, 'legacy', filenames[index])), originals[index])
  }
  await apple.close()
  apple = new BusinessWorker(join(directory, 'apple'), { worker: pathToFileURL(fixture), onRequest: input => business.call('apple_peer', input) })
  await apple.call('initialize', { legacy_directory: join(directory, 'legacy') })
  // A second launch must use the migrated database, not recopy the old import-only settings.
  for (const kind of ['calendar', 'reminders']) assert.equal((await call('status', { kind })).links[0].direction, 'bidirectional')
  for (const kind of ['calendar', 'reminders']) {
    const all = await call('start_sync', { kind })
    assert.equal(all.state.sync_all, true)
    await call('set_auto', { kind, enabled: false })
  }
  await call('disconnect')
  assert.equal((await business.call('records')).filter(row => row.title === 'Synthetic Apple task').length, 1)
  await call('set_auto', { kind: 'reminders', enabled: true })
  const pid = apple.process.pid
  await apple.close()
  assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' })
})

// App transport has its own isolated launch, cleanup and permission-denial cases.
test('Apple app helper uses Launch Services without borrowing the host authorization identity', async () => {
  const { execFile } = await import('node:child_process')
  const { promisify } = await import('node:util')
  await promisify(execFile)(process.env.STELLA_BUSINESS_PYTHON || 'python3', [fileURLToPath(new URL('./apple-app-bridge.py', import.meta.url))])
})
