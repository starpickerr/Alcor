import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createModeController, readModeState, startSecretarySession, canonicalPanel, legacyPanelIds } from '../../packages/alcor-secretary/src/mode.js'

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
  assert.equal(c.getSnapshot(), 'alcor')
  assert.equal(await f.preset, 'alcor')
  f.ctx.layout.selectPanel(null)
  c.switchMode('dsh')
  assert.equal(await f.preset, 'owner-default')
  assert.equal(f.changes.at(-1)[1], 'dsh')
  f.ctx.uiWorkspace.openSession('work-session')
  f.ctx.layout.selectPanel(null)
  c.switchMode('alcor')
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
  c.switchMode('alcor')
  assert.match(c.notice, /无法保存/)
  c.dispose()
})

test('the removed daily page restores to tasks while retaining the existing session', () => {
  const f = fixture(JSON.stringify({ mode: 'stella', locations: { stella: { panel: 'stella', sessionId: 'secretary-session' } } }))
  const controller = createModeController(f.ctx, f.storage)
  assert.equal(f.ctx.layout.panelInfo.getSnapshot().activePanelId, 'alcor-tasks')
  assert.equal(f.ctx.uiWorkspace.selection.getSnapshot().sessionId, 'secretary-session')
  controller.dispose()
  const fresh = fixture()
  const first = createModeController(fresh.ctx, fresh.storage)
  first.switchMode('alcor')
  assert.equal(fresh.ctx.layout.panelInfo.getSnapshot().activePanelId, 'alcor-tasks')
  assert.equal(readModeState(fresh.storage).locations.alcor.panel, 'alcor-tasks')
  first.dispose()
  const removed = fixture(JSON.stringify({ mode: 'stella', locations: { stella: { panel: 'removed' } } }))
  const fallback = createModeController(removed.ctx, removed.storage)
  assert.equal(removed.ctx.layout.panelInfo.getSnapshot().activePanelId, 'alcor-tasks')
  fallback.dispose()
})

test('removed follow-up pages restore to tasks and retain the native session', () => {
  for (const panel of ['stella-history', 'stella-attention']) {
    const f = fixture(JSON.stringify({ mode: 'stella', locations: { stella: { panel, sessionId: 'secretary-session' } } }))
    const controller = createModeController(f.ctx, f.storage)
    assert.equal(f.ctx.layout.panelInfo.getSnapshot().activePanelId, 'alcor-tasks')
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
  assert.equal(b.getSnapshot(), 'alcor')
  assert.equal(await second.preset, 'alcor')
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
  controller.switchMode('alcor')
  assert.equal(await f.preset, 'alcor')
  controller.switchMode('dsh')
  assert.equal(await f.preset, 'owner-default')
  assert.equal(f.ctx.layout.panelInfo.getSnapshot().activePanelId, 'existing-plugin')
  assert.equal(f.ctx.uiWorkspace.selection.getSnapshot().sessionId, 'secretary-session')
  controller.dispose()
})

test('secretary creation delegates workspace selection to DSH and only sets its preset and draft', () => {
  const calls = []
  const ctx = {
    remote: { alcorSecretary: { workspaceState: () => { throw new Error('Retired private bindings must never be read') } } },
    uiWorkspace: { startSession: (...args) => calls.push(args) },
  }
  startSecretarySession(ctx)
  assert.deepEqual(calls, [[undefined, { agentPreset: 'alcor' }]])
  startSecretarySession(ctx, 'Help me plan today')
  assert.deepEqual(calls.at(-1), [undefined, { agentPreset: 'alcor', prompt: 'Help me plan today', whenDraftExists: 'append' }])
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
  assert.equal(f.ctx.layout.panelInfo.getSnapshot().activePanelId, 'alcor-briefing')
  assert.equal(f.ctx.uiWorkspace.selection.getSnapshot().sessionId, 'secretary-session')
  controller.dispose()
})

function keyedStorage(entries = {}) {
  const values = new Map(Object.entries(entries))
  return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) }
}

test('mode migration preserves legacy locations, custom data, and new-key precedence across restart', () => {
  const legacy = JSON.stringify({ mode: 'stella', customName: '我的助手', locations: { stella: { panel: 'stella-tasks', sessionId: 'secretary-session', custom: 'keep' } } })
  const storage = keyedStorage({ 'stella.product-mode.v1': legacy })
  const initial = readModeState(storage)
  assert.deepEqual(JSON.parse(storage.getItem('alcor.product-mode.v1')), { mode: 'alcor', customName: '我的助手', locations: { alcor: { panel: 'alcor-tasks', sessionId: 'secretary-session', custom: 'keep' } } })
  assert.equal(storage.getItem('stella.product-mode.v1'), legacy)
  assert.equal(initial.locations.alcor.sessionId, 'secretary-session')
  const f = fixture(), controller = createModeController(f.ctx, storage)
  controller.switchMode('dsh'); controller.switchMode('alcor'); controller.dispose()
  const saved = JSON.parse(storage.getItem('alcor.product-mode.v1'))
  assert.equal(saved.customName, '我的助手'); assert.equal(saved.locations.alcor.custom, 'keep')
  const restart = createModeController(f.ctx, storage)
  assert.equal(restart.getSnapshot(), 'alcor'); restart.dispose()
  assert.equal(storage.getItem('stella.product-mode.v1'), legacy)
  const preferred = JSON.stringify({ mode: 'dsh', locations: {}, customName: '新名称' })
  storage.setItem('alcor.product-mode.v1', preferred)
  assert.equal(readModeState(storage).customName, '新名称')
  assert.equal(storage.getItem('alcor.product-mode.v1'), preferred)
})

test('new and legacy presets register equivalent isolated compositions', async () => {
  const { readFile } = await import('node:fs/promises')
  const { parse } = await import('yaml')
  const rows = parse(await readFile(new URL('../../packages/alcor-secretary/cordis.patch.yml', import.meta.url), 'utf8'))
    .flatMap(row => row.insert ?? []).filter(row => row.name === '@deepseek-ai/dsh-agent-preset')
  assert.deepEqual(rows.map(row => row.config.id), ['alcor', 'alcor-briefing', 'stella', 'stella-briefing'])
  const normalize = plugins => plugins.map(plugin => ({ ...plugin, id: plugin.id.replace(/^stella-/, "alcor-") }))
  assert.deepEqual(rows[0].config.plugins, normalize(rows[2].config.plugins))
  assert.deepEqual(rows[1].config.plugins, rows[3].config.plugins)
  assert.match(rows[2].config.name, /历史会话.*兼容/)
  assert.match(rows[3].config.name, /兼容/)
})

test('a destination appearing during migration takes precedence', () => {
  let reads = 0
  const legacy = JSON.stringify({ mode: 'stella', locations: {} })
  const current = JSON.stringify({ mode: 'dsh', locations: {} })
  const storage = { getItem: key => key.startsWith('alcor.') ? (++reads === 1 ? null : current) : legacy,
    setItem: () => { throw Error('Must not overwrite the new value') } }
  assert.equal(readModeState(storage).mode, 'dsh')
})

test('every legacy owned panel maps once while unrelated host panels remain unchanged', () => {
  const aliases = { stella: 'alcor-tasks', 'stella-history': 'alcor-tasks', 'stella-attention': 'alcor-tasks', 'stella-agents': 'alcor-tasks', 'stella-sources': 'alcor-briefing',
    'stella-tasks': 'alcor-tasks', 'stella-calendar': 'alcor-calendar', 'stella-briefing': 'alcor-briefing', 'stella-bills': 'alcor-bills', 'stella-schedules': 'alcor-schedules' }
  assert.deepEqual(new Set(legacyPanelIds), new Set(Object.keys(aliases)))
  for (const [old, current] of Object.entries(aliases)) {
    assert.equal(canonicalPanel(old), current)
    const value = { mode: 'stella', locations: { stella: { panel: old, sessionId: 'secretary-session', custom: 'keep' } } }
    const storage = keyedStorage({ 'stella.product-mode.v1': JSON.stringify(value) })
    const state = readModeState(storage)
    assert.equal(state.mode, 'alcor'); assert.equal(state.locations.alcor.panel, current)
    assert.equal(state.locations.alcor.sessionId, 'secretary-session'); assert.equal(state.locations.alcor.custom, 'keep')
    assert.equal(storage.getItem('stella.product-mode.v1'), JSON.stringify(value))
    assert.equal(JSON.parse(storage.getItem('alcor.product-mode.v1')).locations.alcor.panel, current)
  }
  for (const panel of [null, 'plugins', 'schedules', 'my-stella-panel', 'toString']) assert.equal(canonicalPanel(panel), panel)
})

test('new mode key wins and conflicting legacy location is preserved', () => {
  const previous = { panel: 'stella-calendar', sessionId: 'old-session', title: '旧名称' }
  const selected = { panel: 'alcor-bills', sessionId: 'secretary-session', title: '新名称' }
  const raw = JSON.stringify({ mode: 'stella', customName: '个人名字', locations: { stella: previous, alcor: selected, dsh: { panel: 'plugins', sessionId: 'work-session' }, other: { future: true } } })
  const storage = keyedStorage({ 'alcor.product-mode.v1': raw, 'stella.product-mode.v1': JSON.stringify({ mode: 'dsh', locations: {} }) })
  const f = fixture(), controller = createModeController(f.ctx, storage)
  assert.equal(controller.getSnapshot(), 'alcor')
  assert.equal(f.ctx.layout.panelInfo.getSnapshot().activePanelId, 'alcor-bills')
  const migrated = JSON.parse(storage.getItem('alcor.product-mode.v1'))
  assert.deepEqual(migrated.locations.stella, previous)
  assert.deepEqual(migrated.locations.alcor, selected)
  assert.deepEqual(migrated.locations.other, { future: true }); assert.equal(migrated.customName, '个人名字')
  controller.dispose()
})

test('corrupt or inaccessible state and quota errors do not overwrite recoverable values', () => {
  const old = JSON.stringify({ mode: 'stella', locations: { stella: { panel: 'stella-tasks', sessionId: 'secretary-session' } } })
  const storage = keyedStorage({ 'alcor.product-mode.v1': 'invalid', 'stella.product-mode.v1': old })
  const f = fixture(), controller = createModeController(f.ctx, storage)
  controller.switchMode('alcor')
  assert.equal(storage.getItem('alcor.product-mode.v1'), 'invalid'); assert.equal(storage.getItem('stella.product-mode.v1'), old)
  controller.dispose()
  const quota = keyedStorage({ 'stella.product-mode.v1': old }); quota.setItem = () => { throw Error('quota') }
  const q = fixture(), qcontroller = createModeController(q.ctx, quota)
  assert.equal(qcontroller.getSnapshot(), 'alcor'); assert.equal(q.ctx.uiWorkspace.selection.getSnapshot().sessionId, 'secretary-session')
  assert.match(qcontroller.notice, /无法保存/); assert.equal(quota.getItem('stella.product-mode.v1'), old)
  assert.equal(quota.getItem('alcor.product-mode.v1'), null); qcontroller.dispose()
  const unavailable = { getItem() { throw Error('denied') }, setItem() { throw Error('must not write') } }
  const u = createModeController(fixture().ctx, unavailable); u.switchMode('alcor'); u.dispose()
})

test('a stale second window cannot overwrite an already observed update', () => {
  const storage = keyedStorage({ 'alcor.product-mode.v1': JSON.stringify({ mode: 'alcor', locations: {} }) })
  const f1 = fixture(), f2 = fixture(), one = createModeController(f1.ctx, storage), two = createModeController(f2.ctx, storage)
  one.switchMode('dsh')
  const latest = storage.getItem('alcor.product-mode.v1')
  two.switchMode('dsh')
  assert.equal(storage.getItem('alcor.product-mode.v1'), latest)
  assert.match(two.notice, /另一窗口/)
  assert.equal(two.getSnapshot(), 'dsh')
  one.dispose(); two.dispose()
})

test('startup waits for session catalog and renamed panel registration without discarding location', () => {
  const old = JSON.stringify({ mode: 'stella', locations: { stella: { panel: 'stella-calendar', sessionId: 'work-session', note: 'keep' } } })
  const storage = keyedStorage({ 'alcor.product-mode.v1': old }), f = fixture()
  let slotCallback, keys = []
  f.ctx.slots = { entries: () => keys.map(key => ({ options: { key } })), subscribe: (_slot, callback) => { slotCallback = callback; return () => {} } }
  f.setPhase('pending')
  const controller = createModeController(f.ctx, storage)
  assert.deepEqual(f.changes, [])
  f.setPhase('ready'); assert.deepEqual(f.changes, [])
  assert.equal(JSON.parse(storage.getItem('alcor.product-mode.v1')).locations.stella.note, 'keep')
  keys = ['alcor-calendar']; slotCallback()
  assert.equal(f.ctx.uiWorkspace.selection.getSnapshot().sessionId, 'work-session')
  assert.equal(f.ctx.layout.panelInfo.getSnapshot().activePanelId, 'alcor-calendar')
  assert.equal(JSON.parse(storage.getItem('alcor.product-mode.v1')).locations.alcor.note, 'keep')
  controller.dispose()
})

test('an unavailable saved session remains stored across an automatic fallback and mode switch', () => {
  const storage = keyedStorage({ 'alcor.product-mode.v1': JSON.stringify({ mode: 'alcor', locations: { alcor: { panel: 'alcor-tasks', sessionId: 'missing', note: 'keep' } } }) })
  const f = fixture(), controller = createModeController(f.ctx, storage)
  assert.match(controller.notice, /不可用/)
  controller.switchMode('dsh')
  assert.equal(JSON.parse(storage.getItem('alcor.product-mode.v1')).locations.alcor.sessionId, 'missing')
  assert.equal(JSON.parse(storage.getItem('alcor.product-mode.v1')).locations.alcor.note, 'keep')
  controller.dispose()
})

test('a selected legacy layout alias redirects to its registered new panel and saves it', () => {
  const storage = keyedStorage(), f = fixture()
  f.ctx.slots = { entries: () => ['stella-calendar', 'alcor-calendar', 'alcor-tasks'].map(key => ({ options: { key } })), subscribe: () => () => {} }
  const controller = createModeController(f.ctx, storage)
  controller.switchMode('alcor')
  f.ctx.layout.selectPanel('stella-calendar')
  assert.equal(f.ctx.layout.panelInfo.getSnapshot().activePanelId, 'alcor-calendar')
  assert.equal(JSON.parse(storage.getItem('alcor.product-mode.v1')).locations.alcor.panel, 'alcor-calendar')
  controller.dispose()
  const fresh = fixture(); fresh.ctx.slots = f.ctx.slots
  const restart = createModeController(fresh.ctx, storage)
  assert.equal(fresh.ctx.layout.panelInfo.getSnapshot().activePanelId, 'alcor-calendar')
  restart.dispose()
})

test('host main slots register new panels and legacy aliases with equivalent destinations', async () => {
  const { build } = await import('esbuild'), { createRequire } = await import('node:module')
  const output = await build({ entryPoints: [new URL('../../packages/alcor-secretary/src/client.jsx', import.meta.url).pathname],
    bundle: true, packages: 'external', platform: 'node', format: 'cjs', loader: { '.css': 'text', '.svg': 'dataurl' }, write: false })
  const module = { exports: {} }, noop = () => {}, calls = []
  const React = { createElement: noop, useEffect: noop, useState: noop, useRef: noop }
  const generic = new Proxy(noop, { get: () => noop }), require = createRequire(import.meta.url)
  new Function('module', 'exports', 'require', output.outputFiles[0].text)(module, module.exports, name => name === 'react' ? React : name === 'zod' ? require(name) : generic)
  const slots = { registerFactory: noop, inject: (name, callback) => { if (['main', 'schedule.manager.header'].includes(name)) callback() }, register: row => calls.push(row) }
  const ctx = { slots, configForms:{get:()=>({})}, effect: noop, remote: { $mount: async () => {}, settings: {}, schedule: {}, alcorSecretary: {}, alcorOrganizer: {} },
    inject: (_names, callback) => callback(ctx), uiWorkspace: {}, uiSession: {}, sessions: {}, workspaces: {}, layout: {} }
  await module.exports.apply(ctx)
  for (const [legacy, current] of Object.entries({ stella: 'alcor-tasks', 'stella-history': 'alcor-tasks', 'stella-attention': 'alcor-tasks', 'stella-agents': 'alcor-tasks', 'stella-sources': 'alcor-briefing', 'stella-tasks': 'alcor-tasks', 'stella-calendar': 'alcor-calendar', 'stella-briefing': 'alcor-briefing', 'stella-bills': 'alcor-bills' })) {
    assert.equal(calls.find(row => row.key === legacy)?.inject().view, current)
    assert.equal(calls.find(row => row.key === current)?.inject().view, current)
  }
  for (const key of ['stella-schedules', 'alcor-schedules']) {
    const rows = calls.filter(row => row.key === key)
    assert.equal(rows.length, 2); assert.equal(rows[0].inject().scope, 'alcor'); assert.equal(rows[1].priority, -10)
  }
  assert.equal(calls.find(row => row.key === 'schedules').inject().scope, 'all')
})


test('retired social settings restore to secretary tasks without redirecting third-party plugins', () => {
  for (const panel of ['alcor-persona', 'alcor-channels', 'alcor-qq-history']) {
    assert.equal(canonicalPanel(panel), 'alcor-tasks')
    const f = fixture(JSON.stringify({mode:'alcor',locations:{alcor:{panel}}}))
    const controller = createModeController(f.ctx, f.storage)
    assert.equal(f.ctx.layout.panelInfo.getSnapshot().activePanelId, 'alcor-tasks')
    controller.dispose()
  }
  assert.equal(canonicalPanel('independent-plugin'), 'independent-plugin')
})
