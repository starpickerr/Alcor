import { KnowledgePage } from './knowledge.jsx'
import {sharedWorkspaceContext} from './shared-workspace.js'
import { Notice } from './notice.jsx'
import { BearLogo } from './bear-logo.jsx'
import { SectionToggle, WorkspaceToggle, useSidebarSection } from './sidebar-sections.jsx'
import React, { useEffect, useRef, useState } from 'react'
import { Button, useModalLayer, IconNewChatOutlineRegular, IconPanelLeftOutlineRegular, IconPluginPinwheelOutlineRegular, IconChecklistOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { BusinessDesk, applicationViews } from './business.jsx'
import { BriefingWorkspace } from './briefing.jsx'
import { canonicalPanel } from './mode.js'

export const deskViews = applicationViews

export function SecretaryDesk({ startSession, openSession, hasModeSwitch, modeNotice, view = 'alcor-tasks', business, currentSession, openPlugins, briefingView, startBriefing, workspaces }) {
  const [startError, setStartError] = useState('')
  async function begin(prompt) {
    setStartError('')
    try { await startSession(prompt) } catch (error) { setStartError(error.message) }
  }
  const date = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date())
  const currentView = deskViews.find(item => item.id === view)
  const application = applicationViews.some(item => item.id === view)
  return <section className={`alcor-desk${application ? ' alcor-desk-application' : ''}`}>
    {!application && <><header className="alcor-desk-header"><span className="alcor-wordmark">私人秘书</span><span>{date}</span></header>
    <div className="alcor-page-heading"><div><h1>{currentView.label}</h1>
      </div>
      <Button variant="outline" className="alcor-heading-chat" onClick={() => begin()}><IconNewChatOutlineRegular size={16} />开始 Alcor 对话</Button></div></>}
    {startError && <Notice tone="error" role="alert" className="alcor-error">{startError}</Notice>}
    {globalThis.__ALCOR_CLIENT_SHELL__ && modeNotice?.() && <Notice role="status" tone="warning">{modeNotice()}</Notice>}
    {view === 'alcor-briefing' && <BriefingWorkspace api={business} openSession={openSession} openPlugins={openPlugins} initialView={briefingView?.()} startBriefing={startBriefing} workspaces={workspaces} currentSession={currentSession} />}
    {view === 'alcor-knowledge' && <KnowledgePage api={business} />}
    {application && view !== 'alcor-briefing' && view !== 'alcor-knowledge' && <BusinessDesk key={view} view={view} api={business} currentSession={currentSession} startSession={begin} openSession={openSession} />}
    {!hasModeSwitch && <Notice role="status" className="alcor-muted">当前版本暂不支持模式切换。</Notice>}
  </section>
}

export function SecretarySidebar({ collapsed, drawer, width, navigate, startSession, toggleSidebar, usePanelInfo, useRemoteAccess, renderFactorySlot, renderSlot, nativeShared=false, nativeShell=false, pluginPanels, t }) {
  const panel = usePanelInfo(value => value.activePanelId)
  const canManagePlugins = useRemoteAccess('pluginManager/listBundles')
  const applications = [...applicationViews]
  const system = [{ id: 'alcor-logs', label: '日志', icon: IconChecklistOutlineRegular }]
  if (canManagePlugins) system.push({ id:'plugins', label:'插件', icon:IconPluginPinwheelOutlineRegular })
  const [error, setError] = useState('')
  const sidebar = useRef()
  const nav = useRef(null)
  const appsSection = useSidebarSection('applications')
  const systemSection = useSidebarSection('system')
  const wide = drawer || !collapsed
  useModalLayer(sidebar, drawer && !collapsed, toggleSidebar)
  const actions=<>
    <button type="button" className="alcor-sidebar-chat" title={t('session.new.label')} aria-label={t('session.new.label')} onClick={async () => { setError(''); try { await startSession() } catch (reason) { setError(reason.message) } }}><IconNewChatOutlineRegular size={17} />{wide && <span>新对话</span>}</button>
    {error && <Notice tone="error" role="alert">{error}</Notice>}
    <nav aria-label="秘书功能" ref={nav}>{[['应用', applications, appsSection], ['系统', system, systemSection]].map(([label, items, section]) =>
      <div className="alcor-nav-group" role="group" aria-label={label} key={label}>
        {wide && <SectionToggle label={label} section={section} />}
        {(!wide || section.expanded) && <>
          {items.map(item => {
            const Icon = item.icon
            return <button key={item.id} type="button" title={item.label} aria-label={item.label} aria-current={canonicalPanel(panel) === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}>
              <Icon size={18} />{wide && <span>{item.label}</span>}
            </button>
          })}
          {label === '系统' && <PluginPanels source={pluginPanels} wide={wide} panel={panel} navigate={navigate} />}
        </>}
      </div>)}</nav>
    {wide && <WorkspaceToggle nav={nav} />}
  </>
  if(nativeShell)return <div className="alcor-sidebar-actions" data-wide={wide || undefined}>{actions}</div>
  return <aside className="alcor-sidebar" data-native-shared={nativeShared || undefined} ref={sidebar} style={{ width: wide ? width : 56 }} data-collapsed={!wide || undefined} data-shortcut-modal={drawer ? 'sidebar' : undefined} role={drawer && !collapsed ? 'dialog' : undefined} aria-modal={drawer && !collapsed ? true : undefined} tabIndex={-1} aria-label="Alcor 秘书导航">
    <div className="alcor-sidebar-brand"><span className="alcor-emblem" aria-hidden="true"><BearLogo size={32} /></span>{wide && <span>Alcor</span>}<button type="button" data-sidebar-toggle title={t(collapsed ? 'toggle.open' : 'toggle.collapse')} aria-label={t(collapsed ? 'toggle.open' : 'toggle.collapse')} onClick={toggleSidebar}><IconPanelLeftOutlineRegular size={17} /></button></div>
    {actions}
    <section className="alcor-sidebar-workspaces" aria-label="工作区与会话">{nativeShared ? renderSlot('sidebar.workspaces', {wide,expandSidebar:()=>{if(collapsed)toggleSidebar()}}, {hookContext:sharedWorkspaceContext}) : renderFactorySlot('workspace.browser', { wide, expandSidebar: () => { if (collapsed) toggleSidebar() } })}</section>
    <footer>{nativeShared ? <>{renderSlot('sidebar.footer.action',{wide})}{renderSlot('sidebar.settings',{wide})}</> : renderFactorySlot('settings.shell', { wide })}</footer>
  </aside>
}


export function PluginPanels({source, wide, panel, navigate}) {
  const [entries,setEntries] = useState(() => source.list())
  useEffect(() => { const refresh=()=>setEntries(source.list()); const stop=source.subscribe(refresh); refresh(); return stop },[source])
  const panels=entries.map(({options})=>options).filter(row=>!['plugins','schedules'].includes(row.id)).sort((a,b)=>(a.order??0)-(b.order??0))
  if (!panels.length) return null
  return <>{panels.map(row=>{
    const label=typeof row.label==='function' ? row.label() : row.label ?? row.id
    return <button type="button" key={row.id} title={label} aria-label={label} aria-current={panel===row.id?'page':undefined} onClick={()=>navigate(row.id)}>
      <IconPluginPinwheelOutlineRegular size={18} />{wide && <span>{label}</span>}
    </button>
  })}</>
}
