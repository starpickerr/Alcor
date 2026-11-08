import noticeStyle from './notice.css'
import { Notice } from './notice.jsx'
import { PluginBriefingSources } from './briefing-sources.jsx'
import { BearLogo } from './bear-logo.jsx'
import React, { useEffect, useState, useSyncExternalStore } from 'react'
import { remote } from './remote.js'
import { canonicalPanel, createModeController, legacyPanelIds, startSecretarySession } from './mode.js'
import { SecretaryDesk, SecretarySidebar, deskViews } from './desk.jsx'
import { AutomationPage } from './automation.jsx'
import { BearOverlay, BearTimeline } from './bear.jsx'
import { createBearState, installPresetFilter } from './bear-state.js'
import bearStyle from './bear.css'
import deskBaseStyle from './desk.css'
import businessStyle from './business-pages.css'
import billStyle from './bills.css'
import connectionStyle from './connection-settings.css'
import themeStyle from './theme.css'
const deskStyle = deskBaseStyle + businessStyle + billStyle + connectionStyle + themeStyle + noticeStyle + bearStyle
import { remote as organizerRemote } from '../../alcor-organizer/src/remote.js'
import { Button, Input, Menu, MenuItemButton, FishLogo, IconCheckOutlineRegular, IconChevronDownOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'

export const inject = ['slots', 'remote', 'layout', 'uiWorkspace', 'uiSession', 'sessions', 'workspaces']

function ModeSwitch({ useMode, switchMode, getNotice }) {
  const mode = useMode(value => value)
  const [open, setOpen] = useState(false)
  const close = () => setOpen(false)
  return <div className="alcor-mode-bar">
    {/* Portal after native chrome: Electron drag regions follow DOM order, not z-index. */}
    <Menu open={open} onClose={close} portal autoFocus className="alcor-mode-picker" listClassName="alcor-mode-menu"
      anchor={<Button variant="ghost" size="sm" title="切换 Alcor / DeepSeek Harness" aria-label={`切换使用模式，当前 ${mode === 'alcor' ? 'Alcor' : 'DeepSeek Harness'}`}
        aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(value => !value)}
        onKeyDown={event => { if (event.key === 'ArrowDown') { event.preventDefault(); setOpen(true) } }}>
        {mode === 'alcor' ? <BearLogo size={20} /> : <FishLogo size={18} />}
        <span>{mode === 'alcor' ? 'Alcor' : 'DeepSeek Harness'}</span><IconChevronDownOutlineRegular size={12} />
      </Button>}>
      {['alcor', 'dsh'].map(value => <MenuItemButton key={value} current={mode === value}
        icon={value === 'alcor' ? <BearLogo size={22} /> : <FishLogo size={18} />}
        onSelect={() => { switchMode(value); close() }}>
        <span className="alcor-mode-choice"><span>{value === 'alcor' ? 'Alcor' : 'DeepSeek Harness'}</span>
          {mode === value && <IconCheckOutlineRegular size={14} />}</span>
      </MenuItemButton>)}
    </Menu>
    {getNotice() && <Notice role="status" tone="warning" className="alcor-mode-notice">{getNotice()}</Notice>}
  </div>
}

function Workbench({ startSession }) {
  return <section style={{ padding: 'max(32px, var(--dsh-frame-leading-clearance, 0px)) 24px', maxWidth: 760, margin: 'auto' }}>
    <h1>DSH</h1>
    <Button variant="primary" onClick={() => startSession()}>开始 DSH 对话</Button>
  </section>
}

function Preferences({ describe, update, bear }) {
  const prefs = useSyncExternalStore(bear.subscribe, bear.getSnapshot)
  const [descriptor, setDescriptor] = useState()
  const [name, setName] = useState('')
  const [status, setStatus] = useState({message:'正在加载…',tone:'info'})
  useEffect(() => {
    let disposed = false
    describe().then(result => {
      if (disposed) return
      if (!result.ok) { setStatus({message:'设置不可用，请重新打开。',tone:'error'}); return }
      const entry = result.value.namespaces.find(item => item.ns === 'alcor-secretary')
      if (!entry) { setStatus({message:'设置不可用，请重新打开。',tone:'error'}); return }
      setDescriptor(entry); setName(entry.value.displayName); setStatus({message:'',tone:'info'})
    }).catch(() => { if (!disposed) setStatus({message:'连接中断，请重新打开。',tone:'error'}) })
    return () => { disposed = true }
  }, [describe])
  async function save(event) {
    event.preventDefault()
    if (!descriptor) return
    setStatus({message:'正在保存…',tone:'info'})
    try {
      const result = await update(name.trim(), descriptor.revision)
      if (!result.ok) { setStatus({message:'保存失败或设置已变化，请重新打开后重试。',tone:'error'}); return }
      setDescriptor(result.value); setStatus({message:'已保存',tone:'success'})
    } catch { setStatus({message:'连接中断，请重新打开以确认保存结果。',tone:'error'}) }
  }
  return <><form className="alcor-preference-form" onSubmit={save}><h2>Alcor</h2><label htmlFor="alcor-display-name">助手名称</label>
    <Input id="alcor-display-name" value={name} maxLength={80} required disabled={!descriptor} onChange={e => setName(e.target.value)} />
    <Button type="submit" variant="outline" disabled={!descriptor || !name.trim()}>保存</Button>{status.message && <Notice tone={status.tone} role={status.tone === 'error' ? 'alert' : 'status'}>{status.message}</Notice>}</form>
    <section className="alcor-bear-options"><h3>Alcor 小熊</h3>
      <label><input type="checkbox" checked={prefs.enabled} onChange={e => bear.update({enabled:e.target.checked})} />显示 Alcor 小熊</label>
      <label><input type="checkbox" checked={prefs.hover} disabled={!prefs.enabled} onChange={e => bear.update({hover:e.target.checked})} />悬停展开输入框</label>
      <Button size="sm" variant="outline" disabled={prefs.position.every(n => n === 1)} onClick={() => bear.update({position:[1,1]})}>恢复默认位置</Button>
    </section></>
}

export async function apply(ctx) {
  installPresetFilter(ctx)
  let storage
  try { storage = window.localStorage } catch { /* Settings remain available in memory. */ }
  const state = createBearState(storage)
  ctx.effect(() => {
    const style = document.createElement('style')
    style.textContent = deskStyle
    document.head.append(style)
    window.addEventListener('storage', state.onStorage)
    return () => {window.removeEventListener('storage', state.onStorage); style.remove()}
  })
  await ctx.remote.$mount(remote)
  await ctx.remote.$mount(organizerRemote)
  ctx.inject(['remote.alcorSecretary', 'remote.alcorOrganizer', 'remote.settings', 'remote.agentPresets', 'remote.schedule'], ctx => {
    let modeController
    const describe = () => ctx.remote.settings.describe()
    const update = (displayName, revision) => ctx.remote.settings.update('alcor-secretary', { displayName }, revision)
    const startSession = prompt => { modeController?.switchMode('alcor'); return startSecretarySession(ctx, prompt) }
    const openWorkspace = workspaceId => { modeController?.switchMode('alcor'); return ctx.uiWorkspace.openWorkspace(workspaceId, undefined, { agentPreset: 'alcor' }) }
    const briefingContext = () => {
      const current = ctx.uiSession.adapter.current.getSnapshot().props.sessionId
      const workspace = ctx.workspaces.list.getSnapshot().items.find(item => item.sessionIds.includes(current)) || ctx.workspaces.list.getSnapshot().items[0]
      return workspace ? { workspaceId: workspace.workspaceId } : {}
    }
    ctx.slots.inject('plugins.detail.section', () => ctx.slots.register({ name: 'plugins.detail.section', id: 'alcor-briefing-sources', inject: () => ({ api: ctx.remote.alcorOrganizer }) }, PluginBriefingSources))
    const preferences = () => <div className="alcor-settings-content"><Preferences describe={describe} update={update} bear={state.preferences} /></div>
    const navigate = panel => { modeController?.switchMode('alcor'); ctx.layout.selectPanel(canonicalPanel(panel)) }
    // Keep the previous entry as an alias for saved layouts and unpatched DSH clients.
    for (const panel of [...new Set([...legacyPanelIds, ...deskViews.map(view => view.id)])].filter(key => canonicalPanel(key) !== 'alcor-schedules')) ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: panel, inject: () => ({
      startSession,
      openSession: id => ctx.uiWorkspace.openSession(id),
      view: canonicalPanel(panel),
      business: ctx.remote.alcorOrganizer,
      briefingContext,
      openPlugins: name => { modeController?.switchMode('alcor'); const navigation = ctx.get('pluginNavigation'); if (name && navigation) navigation.openBundle(name); else navigate('plugins') },
      currentSession: () => ctx.uiSession.adapter.current.getSnapshot().props.sessionId,
      hasModeSwitch: !!modeController,
    }) }, SecretaryDesk))
    const automationProps = (scope, available = false) => ({ scope, api: ctx.remote.alcorOrganizer, schedule: ctx.remote.schedule,
      available, openNative: () => ctx.layout.selectPanel('schedules'),
    })
    for (const panel of ['alcor-schedules', 'stella-schedules']) ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: panel, inject: () => automationProps('alcor') }, AutomationPage))
    ctx.slots.inject('schedule.manager.header', () => {
      for (const panel of ['alcor-schedules', 'stella-schedules']) ctx.slots.register({ name: 'main', key: panel, priority: -10, inject: () => automationProps('alcor', true) }, AutomationPage)
      ctx.slots.register({ name: 'main', key: 'schedules', priority: -10, inject: () => automationProps('all', true) }, AutomationPage)
    })
    ctx.slots.inject('settings.section', () => ctx.slots.register({ name: 'settings.section', id: 'alcor', order: 5, label: () => 'Alcor' }, preferences))
    ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: 'dsh', inject: () => ({ startSession: () => ctx.uiWorkspace.startSession() }) }, Workbench))
    ctx.slots.inject('shell.top', () => {
      const controller = createModeController(ctx, storage)
      modeController = controller
      ctx.slots.inject('shell.overlay', () => ctx.slots.register({
        name:'shell.overlay', id:'alcor-bear',
        children:{'alcor.bear.timeline':{kind:'single',scope:'session'}},
        inject:() => ({state, ctx:{sessions:ctx.sessions,workspaces:ctx.workspaces,uiWorkspace:ctx.uiWorkspace}, hooks:{mode:controller, mainSelection:ctx.uiWorkspace.selection}}),
      }, BearOverlay))
      ctx.slots.inject('alcor.bear.timeline', () => ctx.slots.register({name:'alcor.bear.timeline'}, BearTimeline))
      let releaseSidebar
      const syncSidebar = () => {
        if (controller.getSnapshot() === 'alcor' && !releaseSidebar) {
          releaseSidebar = ctx.slots.register({ name: 'sidebar', priority: -10, locale: 'sidebar', inject: () => ({
            navigate,
            startSession,
            toggleSidebar: () => ctx.layout.toggleSidebar(),
          }) }, SecretarySidebar)
        } else if (controller.getSnapshot() !== 'alcor' && releaseSidebar) {
          releaseSidebar(); releaseSidebar = undefined
        }
      }
      syncSidebar()
      const stopSidebar = controller.subscribe(syncSidebar)
      ctx.slots.register({ name: 'shell.top', id: 'alcor-mode', inject: () => ({ hooks: { mode: controller }, switchMode: controller.switchMode, getNotice: () => controller.notice }) }, ModeSwitch)
      ctx.effect(() => {
        const style = document.createElement('style')
        style.textContent = `
          .alcor-mode-bar { display:flex; align-items:center; gap:12px; height:44px; padding:6px 16px; box-sizing:border-box; color:var(--dsw-alias-label-primary); font-family:var(--dsw-font-family); -webkit-app-region:drag; }
          html[data-platform='darwin']:not([data-fullscreen]) .alcor-mode-bar { padding-left:88px; }
          .alcor-mode-picker { position:relative; -webkit-app-region:no-drag; }
          .alcor-mode-bar button { gap:7px; height:30px; padding:0 9px; border-radius:var(--dsw-radius-md); font-family:inherit; font-size:13px; font-weight:500; }
          .alcor-mode-bar button > svg:last-child { margin-left:3px; color:var(--dsw-alias-label-tertiary); }
          .alcor-mode-bar button[aria-expanded=true] { background:var(--dsw-alias-interactive-bg-hover); }
          .alcor-mode-bar button:focus-visible { outline:var(--dsw-focus-ring-width) solid var(--dsw-alias-state-business-primary); outline-offset:2px; }
          .alcor-mode-menu { min-width:230px; font-family:var(--dsw-font-family); }
          .alcor-mode-menu [role=menuitem] { min-height:40px; font-size:13px; }
          .alcor-mode-choice { display:flex; align-items:center; justify-content:space-between; gap:20px; }
          .alcor-mode-choice > svg { flex-shrink:0; color:var(--dsw-alias-label-secondary); }
          .alcor-mode-bar [role=status] { font-size:12px; color:var(--dsw-alias-label-secondary); }
        `
        document.head.append(style)
        return () => { stopSidebar(); releaseSidebar?.(); modeController = undefined; style.remove(); controller.dispose() }
      })
    })
  })
}
