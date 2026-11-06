import { BearLogo } from './bear-logo.jsx'
import React, { useEffect, useState } from 'react'
import { remote } from './remote.js'
import { createModeController, startSecretarySession } from './mode.js'
import { SecretaryDesk, SecretarySidebar, deskViews } from './desk.jsx'
import { AutomationPage } from './automation.jsx'
import { QuickChat, QuickConversation } from './quick-chat.jsx'
import deskBaseStyle from './desk.css'
import businessStyle from './business-pages.css'
import billStyle from './bills.css'
import agentHubStyle from './agent-hub.css'
import connectionStyle from './connection-settings.css'
const deskStyle = deskBaseStyle + businessStyle + agentHubStyle + billStyle + connectionStyle
import { remote as organizerRemote } from '../../stella-organizer/src/remote.js'
import { Button, Input, Menu, MenuItemButton, FishLogo, IconCheckOutlineRegular, IconChevronDownOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'

export const inject = ['slots', 'remote', 'layout', 'uiWorkspace', 'uiSession', 'sessions', 'workspaces']

function ModeSwitch({ useMode, switchMode, getNotice }) {
  const mode = useMode(value => value)
  const [open, setOpen] = useState(false)
  const close = () => setOpen(false)
  return <div className="stella-mode-bar">
    {/* Portal after native chrome: Electron drag regions follow DOM order, not z-index. */}
    <Menu open={open} onClose={close} portal autoFocus className="stella-mode-picker" listClassName="stella-mode-menu"
      anchor={<Button variant="ghost" size="sm" title="切换 Stella / DeepSeek Harness" aria-label={`切换使用模式，当前 ${mode === 'stella' ? 'Stella' : 'DeepSeek Harness'}`}
        aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(value => !value)}
        onKeyDown={event => { if (event.key === 'ArrowDown') { event.preventDefault(); setOpen(true) } }}>
        {mode === 'stella' ? <BearLogo size={16} /> : <FishLogo size={18} />}
        <span>{mode === 'stella' ? 'Stella' : 'DeepSeek Harness'}</span><IconChevronDownOutlineRegular size={12} />
      </Button>}>
      {['stella', 'dsh'].map(value => <MenuItemButton key={value} current={mode === value}
        icon={value === 'stella' ? <BearLogo size={18} /> : <FishLogo size={18} />}
        onSelect={() => { switchMode(value); close() }}>
        <span className="stella-mode-choice"><span>{value === 'stella' ? 'Stella' : 'DeepSeek Harness'}</span>
          {mode === value && <IconCheckOutlineRegular size={14} />}</span>
      </MenuItemButton>)}
    </Menu>
    {getNotice() && <span role="status">{getNotice()}</span>}
  </div>
}

function Workbench({ startSession }) {
  return <section style={{ padding: 'max(32px, var(--dsh-frame-leading-clearance, 0px)) 24px', maxWidth: 760, margin: 'auto' }}>
    <h1>DSH</h1>
    <Button variant="primary" onClick={() => startSession()}>开始 DSH 对话</Button>
  </section>
}

function Preferences({ describe, update }) {
  const [descriptor, setDescriptor] = useState()
  const [name, setName] = useState('')
  const [status, setStatus] = useState('正在加载…')
  useEffect(() => {
    let disposed = false
    describe().then(result => {
      if (disposed) return
      if (!result.ok) { setStatus('设置不可用，请重新打开。'); return }
      const entry = result.value.namespaces.find(item => item.ns === 'stella-secretary')
      if (!entry) { setStatus('设置不可用，请重新打开。'); return }
      setDescriptor(entry); setName(entry.value.displayName); setStatus('')
    }).catch(() => { if (!disposed) setStatus('连接中断，请重新打开。') })
    return () => { disposed = true }
  }, [describe])
  async function save(event) {
    event.preventDefault()
    if (!descriptor) return
    setStatus('正在保存…')
    try {
      const result = await update(name.trim(), descriptor.revision)
      if (!result.ok) { setStatus('保存失败或设置已变化，请重新打开后重试。'); return }
      setDescriptor(result.value); setStatus('已保存')
    } catch { setStatus('连接中断，请重新打开以确认保存结果。') }
  }
  return <form className="stella-preference-form" onSubmit={save}><h2>Stella</h2><label htmlFor="stella-display-name">助手名称</label>
    <Input id="stella-display-name" value={name} maxLength={80} required disabled={!descriptor} onChange={e => setName(e.target.value)} />
    <Button type="submit" variant="outline" disabled={!descriptor || !name.trim()}>保存</Button><p role="status">{status}</p></form>
}

export async function apply(ctx) {
  ctx.slots.registerFactory({ name: 'stella.quick-chat', scope: 'root',
    children: { 'stella.quick-chat.conversation': { kind: 'single', scope: 'session' } },
    inject: () => ({ sessions: ctx.sessions, getCurrentSession: () => ctx.uiSession.adapter.current.getSnapshot().props.sessionId }),
  }, QuickChat)
  ctx.slots.inject('stella.quick-chat.conversation', () => ctx.slots.register({ name: 'stella.quick-chat.conversation' }, QuickConversation))
  ctx.effect(() => {
    const style = document.createElement('style')
    style.textContent = deskStyle
    document.head.append(style)
    return () => style.remove()
  })
  await ctx.remote.$mount(remote)
  await ctx.remote.$mount(organizerRemote)
  ctx.inject(['remote.stellaSecretary', 'remote.stellaOrganizer', 'remote.settings', 'remote.agentPresets', 'remote.schedule'], ctx => {
    let modeController
    const describe = () => ctx.remote.settings.describe()
    const update = (displayName, revision) => ctx.remote.settings.update('stella-secretary', { displayName }, revision)
    const startSession = prompt => { modeController?.switchMode('stella'); return startSecretarySession(ctx, prompt) }
    const openWorkspace = workspaceId => { modeController?.switchMode('stella'); return ctx.uiWorkspace.openWorkspace(workspaceId, undefined, { agentPreset: 'stella' }) }
    let briefingSession
    const withBriefingSession = async work => {
      const current = ctx.uiSession.adapter.current.getSnapshot().props.sessionId
      const catalog = ctx.sessions.list.getSnapshot()
      let id = catalog.byId[current]?.projectionValues?.agentPreset === 'stella' ? current : briefingSession
      if (!id || !catalog.byId[id]) {
        const workspace = ctx.workspaces.list.getSnapshot().items.find(item => item.sessionIds.includes(current)) || ctx.workspaces.list.getSnapshot().items[0]
        if (!workspace) throw new Error('请先添加一个 DSH 工作区，再生成简报。')
        id = briefingSession = await ctx.sessions.create({ workspaceId: workspace.workspaceId, agentPreset: 'stella' })
      }
      const reference = ctx.sessions.retain(id, { source: 'stellaBriefing' })
      try { await reference.ready; return await work(id) } finally { reference.release() }
    }
    const preferences = () => <div className="stella-settings-content"><Preferences describe={describe} update={update} /></div>
    const navigate = panel => { modeController?.switchMode('stella'); ctx.layout.selectPanel(panel) }
    // Keep the previous entry as an alias for saved layouts and unpatched DSH clients.
    for (const panel of ['stella', 'stella-history', 'stella-attention', ...deskViews.filter(view => view.id !== 'stella-schedules').map(view => view.id)]) ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: panel, inject: () => ({
      startSession,
      openSession: id => ctx.uiWorkspace.openSession(id),
      view: ['stella', 'stella-history', 'stella-attention'].includes(panel) ? 'stella-tasks' : panel,
      business: ctx.remote.stellaOrganizer,
      withBriefingSession,
      openPlugins: name => { modeController?.switchMode('stella'); const navigation = ctx.get('pluginNavigation'); if (name && navigation) navigation.openBundle(name); else navigate('plugins') },
      currentSession: () => ctx.uiSession.adapter.current.getSnapshot().props.sessionId,
      hasModeSwitch: !!modeController,
    }) }, SecretaryDesk))
    const automationProps = (scope, available = false) => ({ scope, api: ctx.remote.stellaOrganizer, schedule: ctx.remote.schedule,
      available, openNative: () => ctx.layout.selectPanel('schedules'),
    })
    ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: 'stella-schedules', inject: () => automationProps('stella') }, AutomationPage))
    ctx.slots.inject('schedule.manager.header', () => {
      ctx.slots.register({ name: 'main', key: 'stella-schedules', priority: -10, inject: () => automationProps('stella', true) }, AutomationPage)
      ctx.slots.register({ name: 'main', key: 'schedules', priority: -10, inject: () => automationProps('all', true) }, AutomationPage)
    })
    ctx.slots.inject('settings.section', () => ctx.slots.register({ name: 'settings.section', id: 'stella', order: 5, label: () => 'Stella' }, preferences))
    ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: 'dsh', inject: () => ({ startSession: () => ctx.uiWorkspace.startSession() }) }, Workbench))
    ctx.slots.inject('shell.top', () => {
      let storage
      try { storage = window.localStorage } catch { /* The controller reports unavailable persistence. */ }
      const controller = createModeController(ctx, storage)
      modeController = controller
      let releaseSidebar
      const syncSidebar = () => {
        if (controller.getSnapshot() === 'stella' && !releaseSidebar) {
          releaseSidebar = ctx.slots.register({ name: 'sidebar', priority: -10, locale: 'sidebar', inject: () => ({
            navigate,
            startSession,
            toggleSidebar: () => ctx.layout.toggleSidebar(),
          }) }, SecretarySidebar)
        } else if (controller.getSnapshot() !== 'stella' && releaseSidebar) {
          releaseSidebar(); releaseSidebar = undefined
        }
      }
      syncSidebar()
      const stopSidebar = controller.subscribe(syncSidebar)
      ctx.slots.register({ name: 'shell.top', id: 'stella-mode', inject: () => ({ hooks: { mode: controller }, switchMode: controller.switchMode, getNotice: () => controller.notice }) }, ModeSwitch)
      ctx.effect(() => {
        const style = document.createElement('style')
        style.textContent = `
          .stella-mode-bar { display:flex; align-items:center; gap:12px; height:44px; padding:6px 16px; box-sizing:border-box; color:var(--dsw-alias-label-primary); font-family:var(--dsw-font-family); -webkit-app-region:drag; }
          html[data-platform='darwin']:not([data-fullscreen]) .stella-mode-bar { padding-left:88px; }
          .stella-mode-picker { position:relative; -webkit-app-region:no-drag; }
          .stella-mode-bar button { gap:7px; height:30px; padding:0 9px; border-radius:var(--dsw-radius-md); font-family:inherit; font-size:13px; font-weight:500; }
          .stella-mode-bar button > svg:last-child { margin-left:3px; color:var(--dsw-alias-label-tertiary); }
          .stella-mode-bar button[aria-expanded=true] { background:var(--dsw-alias-interactive-bg-hover); }
          .stella-mode-bar button:focus-visible { outline:var(--dsw-focus-ring-width) solid var(--dsw-alias-state-business-primary); outline-offset:2px; }
          .stella-mode-menu { min-width:230px; font-family:var(--dsw-font-family); }
          .stella-mode-menu [role=menuitem] { min-height:40px; font-size:13px; }
          .stella-mode-choice { display:flex; align-items:center; justify-content:space-between; gap:20px; }
          .stella-mode-choice > svg { flex-shrink:0; color:var(--dsw-alias-label-secondary); }
          .stella-mode-bar [role=status] { font-size:12px; color:var(--dsw-alias-label-secondary); }
        `
        document.head.append(style)
        return () => { stopSidebar(); releaseSidebar?.(); modeController = undefined; style.remove(); controller.dispose() }
      })
    })
  })
}
