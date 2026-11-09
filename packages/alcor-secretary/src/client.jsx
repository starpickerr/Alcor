import noticeStyle from './notice.css'
import { Notice } from './notice.jsx'
import { PluginBriefingSources, BriefingPluginReturn } from './briefing-sources.jsx'
import { BearLogo } from './bear-logo.jsx'
import mascot from './assets/alcor-mascot.svg'
import { BriefingToggle } from './briefing-toggle.jsx'
import React, { useEffect, useState, useSyncExternalStore } from 'react'
import { remote } from './remote.js'
import { canonicalPanel, createModeController, legacyPanelIds, startSecretarySession } from './mode.js'
import { SecretaryDesk, SecretarySidebar, deskViews } from './desk.jsx'
import { AutomationPage } from './automation.jsx'
import { BearOverlay, BearTimeline } from './bear.jsx'
import { createBearState, installPresetFilter } from './bear-state.js'
import { createSpaceNavigation } from './space-navigation.js'
import { ChannelBrowser, ChannelHistory } from './channel-history.jsx'
import { MemoryPreferences } from './memory-preferences.jsx'
import { SpacePreferences } from './space-preferences.jsx'
import bearStyle from './bear.css'
import deskBaseStyle from './desk.css'
import businessStyle from './business-pages.css'
import billStyle from './bills.css'
import connectionStyle from './connection-settings.css'
import themeStyle from './theme.css'
const deskStyle = deskBaseStyle + businessStyle + billStyle + connectionStyle + themeStyle + noticeStyle + bearStyle
import { remote as organizerRemote } from '../../alcor-organizer/src/remote.js'
import { Button, Input, Menu, MenuItemButton, SegmentedControl, FishLogo, IconFolderOpenRegular, IconNewChatOutlineRegular, IconEditOutlineRegular, IconCheckOutlineRegular, IconChevronDownOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'

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

function AlcorSettings({initial,pet,memory,spaces}) {
  const [selected,setSelected]=useState(initial)
  const [visited,setVisited]=useState({[initial]:true})
  const pages=[['assistant','人格与记忆',memory],['pet','小熊',pet],...(spaces ? [['spaces','项目空间',spaces]] : [])]
  return <div className="alcor-settings-content">
    <nav aria-label="Alcor 设置分类">{pages.map(([id,title])=>
      <button type="button" key={id} aria-pressed={selected===id} onClick={()=>{setSelected(id);setVisited(prior=>({...prior,[id]:true}))}}>{id==='assistant'?<IconEditOutlineRegular size={15}/>:id==='pet'?<BearLogo size={17}/>:<IconFolderOpenRegular size={15}/>}<span>{title}</span></button>)}</nav>
    {pages.map(([id,,content])=>visited[id] && <div key={id} hidden={selected!==id}>{content}</div>)}
  </div>
}

function Preferences({ bear }) {
  const prefs = useSyncExternalStore(bear.subscribe, bear.getSnapshot)
  const [petName, setPetName] = useState(prefs.name)
  useEffect(() => setPetName(prefs.name), [prefs.name])
  return <><section className="alcor-bear-options" aria-label="小熊宠物设置"><h2>小熊宠物</h2>
    <div className="alcor-bear-preview" style={{'--alcor-bear-preview-size': `${prefs.size * 1.5}px`}}><img src={mascot} alt={prefs.name} draggable={false} /></div>
    <div className="alcor-bear-name">
      <label htmlFor="alcor-bear-name">名字</label>
      <Input id="alcor-bear-name" aria-label="小熊名称" value={petName} maxLength={40}
        onChange={e => setPetName(e.target.value)}
        onBlur={() => {
          const value = petName.trim() || 'Alcor 小熊'
          setPetName(value)
          bear.update({name:value})
        }}
        onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) e.currentTarget.blur() }} />
    </div>
    <div className="alcor-bear-option-row">
      <span>显示小熊</span>
      <BriefingToggle label="显示 Alcor 小熊" checked={prefs.enabled} onChange={enabled => bear.update({enabled})} />
    </div>
    <div className="alcor-bear-option-row">
      <span>大小</span>
      <SegmentedControl id="alcor-bear-size" label="小熊大小" value={String(prefs.size)}
        onChange={value => bear.update({size:Number(value)})}
        options={[{value:'48',label:'小'},{value:'60',label:'标准'},{value:'84',label:'大'}]} />
    </div>
    <div className="alcor-bear-option-row">
      <span>悬停展开对话</span>
      <BriefingToggle label="悬停展开输入框" checked={prefs.hover} onChange={hover => bear.update({hover})} />
    </div>
    <div className="alcor-bear-option-row">
      <span>位置</span>
      <Button size="sm" variant="outline" disabled={prefs.position.every(n => n === 1)} onClick={() => bear.update({position:[1,1]})}>恢复默认位置</Button>
    </div>
    </section>
    </>
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
    ctx.slots.inject('plugins.detail.section', () => ctx.slots.register({ name: 'plugins.detail.section', id: 'alcor-briefing-sources', inject: () => ({ api: ctx.remote.alcorOrganizer }) }, PluginBriefingSources))
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
    const preferences = initial => <AlcorSettings initial={initial==='pet'?'pet':'assistant'}
      pet={<Preferences bear={state.preferences} />} memory={<MemoryPreferences api={ctx.remote.alcorSecretary} />}
      spaces={spaceNavigation && <SpacePreferences navigation={spaceNavigation} allWorkspaces={ctx.workspaces.list} allSessions={ctx.sessions.list} />} />
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
    ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: 'dsh', inject: () => ({ navigation:spaceNavigation, startSession:id => spaceNavigation ? spaceNavigation.startSession(id) : ctx.uiWorkspace.startSession(id), openSession:id => ctx.uiWorkspace.openSession(id) }) }, Workbench))
    ctx.slots.inject('shell.top', () => {
      const controller = createModeController(ctx, storage)
      modeController = controller
      const navigation = createSpaceNavigation(ctx, controller)
      spaceNavigation = navigation
      ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: 'alcor-qq-history', inject: () => ({ navigation, api: ctx.remote.session, secretary: ctx.remote.alcorSecretary }) }, ChannelHistory))
      ctx.slots.inject('shell.overlay', () => ctx.slots.register({
        name:'shell.overlay', id:'alcor-bear',
        children:{'alcor.bear.timeline':{kind:'single',scope:'session'}},
        inject:() => ({state, renderSettings:preferences, product:navigation.state, followupApi:ctx.remote.alcorSecretary, ctx:{sessions:ctx.sessions,workspaces:ctx.workspaces,uiWorkspace:ctx.uiWorkspace}, hooks:{mode:controller, mainSelection:ctx.uiWorkspace.selection}}),
      }, BearOverlay))
      ctx.slots.inject('alcor.bear.timeline', () => ctx.slots.register({name:'alcor.bear.timeline'}, BearTimeline))
      let releaseSidebar
      const syncSidebar = () => {
        if (controller.getSnapshot() === 'alcor' && !releaseSidebar) {
          releaseSidebar = ctx.slots.register({ name: 'sidebar', priority: -10, locale: 'sidebar', inject: () => ({
            navigate,
            startSession,
            renderQQ: (styles, expandSidebar) => <ChannelBrowser navigation={navigation} catalog={ctx.sessions.list} styles={styles} expandSidebar={expandSidebar} />,
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
        return () => { stopSidebar(); releaseSidebar?.(); navigation.dispose(); modeController = undefined; style.remove(); controller.dispose() }
      })
    })
  })
}
