import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createModeController, readModeState, startSecretarySession } from '../../packages/stella-secretary/src/mode.js'

function fixture(initial = null) {
  let saved = initial
  const storage = { getItem: () => saved, setItem: (_key, value) => { saved = value } }
  const listeners = new Set()
  const changes = []
  let panel = null
  let selection = { sessionId: 'secretary-session' }
  let phase = 'ready'
  let preset
  const source = getSnapshot => ({ getSnapshot, subscribe: fn => { listeners.add(fn); return () => listeners.delete(fn) } })
  const notify = () => { for (const fn of listeners) fn() }
  const ctx = {
    remote: { agentPresets: { list: async () => ({ ok: true, value: { presets: [{ id: 'owner-default', isDefault: true }] } }) } },
    on: (_event, fn) => { preset = fn; return () => { preset = undefined } },
    layout: {
      panelInfo: source(() => ({ activePanelId: panel })),
      selectPanel(value) { if (value === 'removed') throw new Error('Removed panel'); panel = value; changes.push(['panel', value]); notify() },
    },
    uiWorkspace: {
      selection: source(() => selection),
      openSession(id) { selection = { sessionId: id }; changes.push(['session', id]); notify() },
    },
    sessions: { list: source(() => ({ phase, byId: phase === 'pending' ? {} : { 'secretary-session': {}, 'work-session': {} } })) },
  }
  return { ctx, storage, changes, listeners, setPhase(value) { phase = value; notify() }, get preset() { return preset?.() } }
}

test('switching restores positions without creating sessions or changing stored session policy', async () => {
  const f = fixture(JSON.stringify({ mode: 'stella', locations: { stella: { panel: 'stella' } } }))
  const c = createModeController(f.ctx, f.storage)
  assert.equal(c.getSnapshot(), 'stella')
  assert.equal(await f.preset, 'stella')
  f.ctx.layout.selectPanel(null)
  c.switchMode('dsh')
  assert.equal(await f.preset, 'owner-default')
  assert.equal(f.changes.at(-1)[1], 'dsh')
  f.ctx.uiWorkspace.openSession('work-session')
  f.ctx.layout.selectPanel(null)
  c.switchMode('stella')
  assert.deepEqual(f.ctx.uiWorkspace.selection.getSnapshot(), { sessionId: 'secretary-session' })
  c.switchMode('dsh')
  assert.deepEqual(f.ctx.uiWorkspace.selection.getSnapshot(), { sessionId: 'work-session' })
  assert.equal(readModeState(f.storage).mode, 'dsh')
  c.dispose()
  assert.equal(f.listeners.size, 0)
  assert.equal(f.preset, undefined)
})

test('invalid persistence, unavailable pages and storage failure have safe fallbacks', () => {
  for (const initial of ['not json', '{"mode":"admin"}', 'null']) assert.equal(readModeState(fixture(initial).storage).mode, 'dsh')
  const f = fixture(JSON.stringify({ mode: 'dsh', locations: { dsh: { panel: 'removed', sessionId: 'gone' } } }))
  const c = createModeController(f.ctx, f.storage)
  assert.equal(f.changes.at(-1)[1], 'dsh')
  assert.match(c.notice, /不可用/)
  assert.throws(() => c.switchMode('admin'), /Unknown/)
  f.storage.setItem = () => { throw new Error('Storage disabled') }
  c.switchMode('stella')
  assert.match(c.notice, /无法保存/)
  c.dispose()
})

test('the removed daily page restores to tasks while retaining the existing session', () => {
  const f = fixture(JSON.stringify({ mode: 'stella', locations: { stella: { panel: 'stella', sessionId: 'secretary-session' } } }))
  const controller = createModeController(f.ctx, f.storage)
  assert.equal(f.ctx.layout.panelInfo.getSnapshot().activePanelId, 'stella-tasks')
  assert.equal(f.ctx.uiWorkspace.selection.getSnapshot().sessionId, 'secretary-session')
  controller.dispose()
  const fresh = fixture()
  const first = createModeController(fresh.ctx, fresh.storage)
  first.switchMode('stella')
  assert.equal(fresh.ctx.layout.panelInfo.getSnapshot().activePanelId, 'stella-tasks')
  assert.equal(readModeState(fresh.storage).locations.stella.panel, 'stella-tasks')
  first.dispose()
  const removed = fixture(JSON.stringify({ mode: 'stella', locations: { stella: { panel: 'removed' } } }))
  const fallback = createModeController(removed.ctx, removed.storage)
  assert.equal(removed.ctx.layout.panelInfo.getSnapshot().activePanelId, 'stella-tasks')
  fallback.dispose()
})

test('removed follow-up pages restore to tasks and retain the native session', () => {
  for (const panel of ['stella-history', 'stella-attention']) {
    const f = fixture(JSON.stringify({ mode: 'stella', locations: { stella: { panel, sessionId: 'secretary-session' } } }))
    const controller = createModeController(f.ctx, f.storage)
    assert.equal(f.ctx.layout.panelInfo.getSnapshot().activePanelId, 'stella-tasks')
    assert.equal(f.ctx.uiWorkspace.selection.getSnapshot().sessionId, 'secretary-session')
    controller.dispose()
  }
})

test('separate windows keep independent modes despite shared preference storage', async () => {
  const first = fixture(JSON.stringify({ mode: 'stella', locations: {} }))
  const second = fixture()
  const a = createModeController(first.ctx, first.storage)
  const b = createModeController(second.ctx, first.storage)
  a.switchMode('dsh')
  assert.equal(b.getSnapshot(), 'stella')
  assert.equal(await second.preset, 'stella')
  a.dispose(); b.dispose()
})


test('startup waits for the authoritative catalog without overwriting the saved session', () => {
  const f = fixture(JSON.stringify({ mode: 'dsh', locations: { dsh: { panel: null, sessionId: 'work-session' } } }))
  f.setPhase('pending')
  const c = createModeController(f.ctx, f.storage)
  assert.deepEqual(f.changes, [])
  f.ctx.layout.selectPanel('stella')
  assert.equal(readModeState(f.storage).locations.dsh.sessionId, 'work-session')
  f.setPhase('ready')
  assert.deepEqual(f.ctx.uiWorkspace.selection.getSnapshot(), { sessionId: 'work-session' })
  assert.equal(f.changes.at(-1)[1], null)
  assert.equal(c.notice, '')
  c.dispose()
})

test('first activation preserves the existing DSH page, session and host preset', async () => {
  const f = fixture()
  f.ctx.layout.selectPanel('existing-plugin')
  f.changes.length = 0
  const controller = createModeController(f.ctx, f.storage)
  assert.equal(controller.getSnapshot(), 'dsh')
  assert.equal(await f.preset, 'owner-default')
  assert.deepEqual(f.changes, [])
  controller.switchMode('stella')
  assert.equal(await f.preset, 'stella')
  controller.switchMode('dsh')
  assert.equal(await f.preset, 'owner-default')
  assert.equal(f.ctx.layout.panelInfo.getSnapshot().activePanelId, 'existing-plugin')
  assert.equal(f.ctx.uiWorkspace.selection.getSnapshot().sessionId, 'secretary-session')
  controller.dispose()
})

test('secretary creation delegates workspace selection to DSH and only sets its preset and draft', () => {
  const calls = []
  const ctx = {
    remote: { stellaSecretary: { workspaceState: () => { throw new Error('Retired private bindings must never be read') } } },
    uiWorkspace: { startSession: (...args) => calls.push(args) },
  }
  startSecretarySession(ctx)
  assert.deepEqual(calls, [[undefined, { agentPreset: 'stella' }]])
  startSecretarySession(ctx, 'Help me plan today')
  assert.deepEqual(calls.at(-1), [undefined, { agentPreset: 'stella', prompt: 'Help me plan today', whenDraftExists: 'append' }])
})

test('DSH creation resolves the current owner default and refuses unavailable defaults', async () => {
  const f = fixture()
  const controller = createModeController(f.ctx, f.storage)
  assert.equal(await f.preset, 'owner-default')
  f.ctx.remote.agentPresets.list = async () => ({ ok: true, value: { presets: [{ id: 'new-owner-default', isDefault: true }] } })
  assert.equal(await f.preset, 'new-owner-default')
  for (const result of [{ ok: false }, { ok: true, value: { presets: [] } }]) {
    f.ctx.remote.agentPresets.list = async () => result
    await assert.rejects(f.preset, /无法读取 DSH 默认 Agent/)
  }
  controller.dispose()
})


test('removed data page restores to briefing without discarding the native session', () => {
  const f = fixture(JSON.stringify({ mode: 'stella', locations: { stella: { panel: 'stella-sources', sessionId: 'secretary-session' } } }))
  const controller = createModeController(f.ctx, f.storage)
  assert.equal(f.ctx.layout.panelInfo.getSnapshot().activePanelId, 'stella-briefing')
  assert.equal(f.ctx.uiWorkspace.selection.getSnapshot().sessionId, 'secretary-session')
  controller.dispose()
})
