import {sharedWorkspaceSlot} from './shared-workspace.js'
import {sidebarOwner} from './sidebar-owner.js'
import {withModeView} from '../../alcor-plugin-info/src/mode-view.jsx'
import {applicationEnvironment} from '../../alcor-plugin-info/src/assignments.js'
import noticeStyle from './notice.css'
import { Notice } from './notice.jsx'
import { PluginBriefingSources, BriefingPluginReturn } from './briefing-sources.jsx'
import { BearLogo } from './bear-logo.jsx'
import React, { useEffect, useState, useSyncExternalStore } from 'react'
import { remote } from './remote.js'
import { canonicalPanel, createModeController, legacyPanelIds, startSecretarySession } from './mode.js'
import { SecretaryDesk, SecretarySidebar, deskViews } from './desk.jsx'
import { AutomationPage } from './automation.jsx'
import { installPresetFilter } from './session-presets.js'
import { createSpaceNavigation } from './space-navigation.js'
import { SpacePreferences } from './space-preferences.jsx'
import deskBaseStyle from './desk.css'
import businessStyle from './business-pages.css'
import billStyle from './bills.css'
import connectionStyle from './connection-settings.css'
import themeStyle from './theme.css'
const deskStyle = deskBaseStyle + businessStyle + billStyle + connectionStyle + themeStyle + noticeStyle
import { remote as organizerRemote } from '../../alcor-organizer/src/remote.js'
import { Button, Menu, MenuItemButton, FishLogo, IconPanelLeftOutlineRegular, IconFolderOpenRegular, IconNewChatOutlineRegular, IconCheckOutlineRegular, IconChevronDownOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'

export const inject = ['locale','loader','configForms','slots', 'remote', 'layout', 'uiWorkspace', 'uiSession', 'sessions', 'workspaces']

function ModeSwitch({ useMode, switchMode, getNotice, onReady }) {
  const mode = useMode(value => value)
  const [open, setOpen] = useState(false)
  const close = () => setOpen(false)
  // Preserve the required native sidebar through the official initial boot audit.
  useEffect(() => { onReady?.() }, [onReady])
  if(globalThis.__ALCOR_CLIENT_SHELL__)return null
  if(applicationEnvironment()==='alcor')return <div className="alcor-mode-bar"><BearLogo size={20}/><span>Alcor</span>
    {getNotice() && <Notice role="status" tone="warning" className="alcor-mode-notice">{getNotice()}</Notice>}</div>
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

function Workbench({ navigation, startSession, openSession }) {
  const [error,setError]=useState('')
  async function begin(id) {
    try {setError('');await startSession(id)} catch(reason){setError(reason.message)}
  }
  return <section className="alcor-workbench">
    <header><div className="alcor-workbench-title"><FishLogo size={26} /><h1>DSH 工作台</h1></div>
      <Button variant="primary" onClick={()=>begin()}>开始 DSH 对话</Button></header>
    {error && <Notice tone="error">{error}</Notice>}
    {navigation && <WorkbenchHistory navigation={navigation} begin={begin} openSession={openSession} />}
  </section>
}
function WorkbenchHistory({navigation,begin,openSession}) {
  const [expanded,setExpanded]=useState(false)
  const projects=useSyncExternalStore(navigation.workspaces.subscribe,navigation.workspaces.getSnapshot).items
  const catalog=useSyncExternalStore(navigation.sessions.subscribe,navigation.sessions.getSnapshot)
  return <>
    {projects.length>0 && <section><h2>项目</h2><div className="alcor-workbench-projects">{(expanded?projects:projects.slice(0,4)).map(item=>
      <button type="button" key={item.workspaceId} title={item.path} aria-label={'在'+item.title+'中新建对话'} onClick={()=>begin(item.workspaceId)}>
        <IconFolderOpenRegular size={21} /><span>{item.title}</span><small>{item.sessionIds.length} 段对话</small></button>)}</div>{projects.length>4 && <Button size="sm" variant="ghost" aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}>{expanded?'收起项目':'查看全部项目'}</Button>}</section>}
    {catalog.ids.length>0 && <section><h2>最近对话</h2>{catalog.ids.slice(0,6).map(id=>
      <button className="alcor-workbench-chat" type="button" key={id} onClick={()=>openSession(id)}><IconNewChatOutlineRegular size={16} /><span>{catalog.byId[id]?.displayTitle || '未命名对话'}</span></button>)}</section>}
  </>
}

export async function apply(ctx) {
  installPresetFilter(ctx)
  let storage
  try { storage = window.localStorage } catch { /* Settings remain available in memory. */ }
  ctx.effect(() => {
    const style = document.createElement('style')
    style.textContent = deskStyle
    document.head.append(style)
    return () => style.remove()
  })
  await ctx.remote.$mount(remote)
  await ctx.remote.$mount(organizerRemote)
  ctx.inject(['remote.alcorSecretary', 'remote.alcorOrganizer', 'remote.settings', 'remote.agentPresets', 'remote.schedule', 'remote.session'], ctx => {
  let modeController, spaceNavigation
    let briefingVisit = null
    const visitListeners = new Set()
    const briefingNavigation = {
      getSnapshot: () => briefingVisit,
      subscribe(listener) { visitListeners.add(listener); return () => visitListeners.delete(listener) },
      update(value) { briefingVisit = value; for (const listener of visitListeners) listener() },
    }
    ctx.effect(() => ctx.layout.panelInfo.subscribe(() => {
      if (!briefingVisit) return
      const panel = canonicalPanel(ctx.layout.panelInfo.getSnapshot().activePanelId)
      const expected = briefingVisit.returning ? 'alcor-briefing' : 'plugins'
      if (panel !== expected) briefingNavigation.update(null)
    }))
    const startSession = prompt => { modeController?.switchMode('alcor'); return startSecretarySession(ctx, prompt) }
    const openWorkspace = workspaceId => { modeController?.switchMode('alcor'); return ctx.uiWorkspace.openWorkspace(workspaceId, undefined, { agentPreset: 'alcor' }) }
    const briefingContext = () => {
      const current = ctx.uiSession.adapter.current.getSnapshot().props.sessionId
      const workspace = ctx.workspaces.list.getSnapshot().items.find(item => item.sessionIds.includes(current)) || ctx.workspaces.list.getSnapshot().items[0]
      return workspace ? { workspaceId: workspace.workspaceId } : {}
    }
    const SchoolSources=withModeView(ctx,'@alcor/elearning',PluginBriefingSources)
    ctx.slots.inject('plugins.detail.section', () => ctx.slots.register({ name: 'plugins.detail.section', id: 'alcor-briefing-sources', inject: () => ({ api: ctx.remote.alcorOrganizer }) }, props=>props.subject?.kind==='bundle' && props.subject.pkg?.name==='@alcor/elearning' ? <SchoolSources {...props}/> : null))
    ctx.slots.inject('plugins.detail.actions', () => ctx.slots.register({
      name:'plugins.detail.actions', id:'alcor-briefing-return', order:-10,
      inject:() => ({navigation:briefingNavigation, onReturn:() => {
        const visit = briefingVisit
        if (!visit) return
        modeController?.switchMode('alcor')
        briefingNavigation.update({...visit, returning:true})
        ctx.layout.selectPanel('alcor-briefing')
      }}),
    }, BriefingPluginReturn))
    const preferences = () => <div className="alcor-settings-content">{spaceNavigation && <SpacePreferences navigation={spaceNavigation} allWorkspaces={ctx.workspaces.list} allSessions={ctx.sessions.list} />}</div>
    const navigate = panel => { briefingNavigation.update(null); modeController?.switchMode('alcor'); ctx.layout.selectPanel(canonicalPanel(panel)) }
    // Keep the previous entry as an alias for saved layouts and unpatched DSH clients.
    for (const panel of [...new Set([...legacyPanelIds, ...deskViews.map(view => view.id)])].filter(key => canonicalPanel(key) !== 'alcor-schedules')) ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: panel, inject: () => ({
      startSession,
      openSession: id => ctx.uiWorkspace.openSession(id),
      view: canonicalPanel(panel),
      business: ctx.remote.alcorOrganizer,
      briefingContext,
      briefingView:() => briefingVisit?.returning ? briefingVisit.view : undefined,
      openPlugins: (name, view) => {
        modeController?.switchMode('alcor')
        briefingNavigation.update({packageName:name, view, returning:false})
        const navigation = ctx.get('pluginNavigation')
        if (name && navigation) navigation.openBundle(name)
        else if (navigation) navigation.openList()
        else ctx.layout.selectPanel('plugins')
      },
      currentSession: () => ctx.uiSession.adapter.current.getSnapshot().props.sessionId,
      hasModeSwitch: !!modeController,
      modeNotice: () => modeController?.notice,
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
    if(applicationEnvironment()!=='alcor')ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: 'dsh', inject: () => ({ navigation:spaceNavigation, startSession:id => spaceNavigation ? spaceNavigation.startSession(id) : ctx.uiWorkspace.startSession(id), openSession:id => ctx.uiWorkspace.openSession(id) }) }, Workbench))
    ctx.slots.inject(globalThis.__ALCOR_CLIENT_SHELL__ ? 'shell.top' : 'shell.overlay', () => {
      const shared = !globalThis.__ALCOR_CLIENT_SHELL__
      const controller = createModeController(ctx, storage, { shared })
      modeController = controller
      const navigation = shared ? null : createSpaceNavigation(ctx, controller)
      spaceNavigation = navigation
      let releaseSidebar, sharedOwner, ownerReady=false
      const syncSidebar = () => {
        if (shared) { if(ownerReady) sharedOwner?.select(controller.getSnapshot()==='alcor').catch(error=>console.error('Alcor sidebar transition failed',error)); return }
        if (controller.getSnapshot() === 'alcor' && !releaseSidebar) {
          releaseSidebar = ctx.slots.register({ name: 'sidebar', priority: -10, locale: 'sidebar', inject: () => ({
            navigate,
            startSession,
            pluginPanels: { list: () => ctx.slots.entriesOfSlot('sidebar.panellist'), subscribe: listener => ctx.slots.subscribe('sidebar.panellist',listener) },
            hubForm:ctx.configForms.get('agent-hub'),
            toggleSidebar: () => ctx.layout.toggleSidebar(),
          }) }, SecretarySidebar)
        } else if (controller.getSnapshot() !== 'alcor' && releaseSidebar) {
          releaseSidebar(); releaseSidebar = undefined
        }
      }
      if (shared) {
        ctx.effect(() => ctx.locale.register('alcor-sidebar', {zh:{'toggle.open':'展开侧边栏','toggle.collapse':'收起侧边栏','session.new.label':'新建会话'},en:{'toggle.open':'Expand sidebar','toggle.collapse':'Collapse sidebar','session.new.label':'New session'}}))
        sharedOwner=sidebarOwner(ctx.loader,()=>{
          const children=Object.fromEntries(['sidebar.brand.mark','sidebar.brand.name','sidebar.toggle.badge','sidebar.workspaces','sidebar.settings'].map(name=>[name,{kind:'single',scope:'root'}]))
          children['sidebar.workspaces']=sharedWorkspaceSlot
          for(const name of ['sidebar.panellist','sidebar.panellist.actions','sidebar.footer.action'])children[name]={kind:'list',scope:'root'}
          const release=ctx.slots.register({name:'sidebar',priority:-10,children,locale:'alcor-sidebar',inject:()=>({
            nativeShared:true,navigate,startSession,toggleSidebar:()=>ctx.layout.toggleSidebar(),
            pluginPanels:{list:()=>ctx.slots.entriesOfSlot('sidebar.panellist'),subscribe:listener=>ctx.slots.subscribe('sidebar.panellist',listener)},
            hubForm:ctx.configForms.get('agent-hub'),
          })},SecretarySidebar)
          const leading=ctx.slots.register({name:'shell.leading',priority:-10},()=> <Button variant="ghost" aria-label="展开侧边栏" onClick={()=>ctx.layout.toggleSidebar()}><IconPanelLeftOutlineRegular size={17}/></Button>)
          return ()=>{leading();release()}
        })
        ctx.effect(()=>()=>sharedOwner.dispose())
      }
      const activateSidebar=()=>{ownerReady=true;syncSidebar()}
      syncSidebar()
      const stopSidebar = controller.subscribe(syncSidebar)
      if(!globalThis.__ALCOR_CLIENT_SHELL__)ctx.slots.register({ name: 'shell.overlay', id: 'alcor-mode', inject: () => ({ hooks: { mode: controller }, switchMode: controller.switchMode, getNotice: () => controller.notice, onReady:activateSidebar }) }, ModeSwitch)
      ctx.effect(() => {
        const style = document.createElement('style')
        style.textContent = `
          .alcor-mode-bar { position:absolute; top:calc(var(--dsh-frame-chrome-top, 0px) + 8px); left:50%; transform:translateX(-50%); display:flex; align-items:center; gap:8px; height:36px; padding:3px 5px; border:1px solid var(--dsw-alias-border-base, #ddd); border-radius:8px; background:var(--dsw-alias-bg-base, white); box-sizing:border-box; color:var(--dsw-alias-label-primary); font-family:var(--dsw-font-family); -webkit-app-region:no-drag; pointer-events:auto; }
          .alcor-mode-picker { position:relative; -webkit-app-region:no-drag; }
          .alcor-mode-bar button { -webkit-app-region:no-drag; gap:7px; height:30px; padding:0 9px; border-radius:var(--dsw-radius-md); font-family:inherit; font-size:13px; font-weight:500; }
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
        return () => { stopSidebar(); releaseSidebar?.(); navigation?.dispose(); modeController = undefined; style.remove(); controller.dispose() }
      })
    })
  })
}
