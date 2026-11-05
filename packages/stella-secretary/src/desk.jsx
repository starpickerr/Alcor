import { BearLogo } from './bear-logo.jsx'
import React, { useRef, useState } from 'react'
import { Button, useModalLayer, IconNewChatOutlineRegular, IconPanelLeftOutlineRegular, IconPluginPinwheelOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { BusinessDesk, applicationViews } from './business.jsx'
import { BriefingWorkspace } from './briefing.jsx'

export const deskViews = applicationViews

export function SecretaryDesk({ startSession, openSession, hasModeSwitch, view = 'stella-tasks', business, currentSession, withBriefingSession, openPlugins, renderFactorySlot }) {
  const [startError, setStartError] = useState('')
  async function begin(prompt) {
    setStartError('')
    try { await startSession(prompt) } catch (error) { setStartError(error.message) }
  }
  const date = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date())
  const currentView = deskViews.find(item => item.id === view)
  const application = applicationViews.some(item => item.id === view)
  return <section className={`stella-desk${application ? ' stella-desk-application' : ''}`}>
    {!application && <><header className="stella-desk-header"><span className="stella-wordmark">私人秘书</span><span>{date}</span></header>
    <div className="stella-page-heading"><div><h1>{currentView.label}</h1>
      </div>
      <Button variant="outline" className="stella-heading-chat" onClick={() => begin()}><IconNewChatOutlineRegular size={16} />开始 Stella 对话</Button></div></>}
    {startError && <p role="alert" className="stella-error">{startError}</p>}
    {view === 'stella-briefing' && <BriefingWorkspace api={business} withBriefingSession={withBriefingSession} openPlugins={openPlugins} />}
    {application && view !== 'stella-briefing' && <BusinessDesk key={view} view={view} api={business} currentSession={currentSession} startSession={begin} openSession={openSession} quickChat={kind => renderFactorySlot('stella.quick-chat', { kind })} />}
    {!hasModeSwitch && <p role="status" className="stella-muted">当前版本暂不支持模式切换。</p>}
  </section>
}

export function SecretarySidebar({ collapsed, drawer, width, navigate, startSession, toggleSidebar, usePanelInfo, useRemoteAccess, renderFactorySlot, t }) {
  const panel = usePanelInfo(value => value.activePanelId)
  const canManagePlugins = useRemoteAccess('pluginManager/listBundles')
  const dataViews = applicationViews
  const applications = canManagePlugins ? [...dataViews, { id: 'plugins', label: '插件', icon: IconPluginPinwheelOutlineRegular }] : dataViews
  const [error, setError] = useState('')
  const sidebar = useRef()
  const wide = drawer || !collapsed
  useModalLayer(sidebar, drawer && !collapsed, toggleSidebar)
  return <aside className="stella-sidebar" ref={sidebar} style={{ width: wide ? width : 56 }} data-collapsed={!wide || undefined} data-shortcut-modal={drawer ? 'sidebar' : undefined} role={drawer && !collapsed ? 'dialog' : undefined} aria-modal={drawer && !collapsed ? true : undefined} tabIndex={-1} aria-label="Stella 秘书导航">
    <div className="stella-sidebar-brand"><span className="stella-emblem" aria-hidden="true"><BearLogo size={21} /></span>{wide && <span>Stella</span>}<button type="button" data-sidebar-toggle title={t(collapsed ? 'toggle.open' : 'toggle.collapse')} aria-label={t(collapsed ? 'toggle.open' : 'toggle.collapse')} onClick={toggleSidebar}><IconPanelLeftOutlineRegular size={17} /></button></div>
    <button type="button" className="stella-sidebar-chat" title={t('session.new.label')} aria-label={t('session.new.label')} onClick={async () => { setError(''); try { await startSession() } catch (reason) { setError(reason.message) } }}><IconNewChatOutlineRegular size={17} />{wide && <span>新对话</span>}</button>
    {error && <p role="alert">{error}</p>}
    <nav aria-label="秘书功能">{['应用'].map(group => <div className="stella-nav-group" key={group}>{wide && <p>{group}</p>}{applications.map(item => { const Icon = item.icon; return <button key={item.id} type="button" title={item.label} aria-label={item.label} aria-current={panel === item.id || panel === 'stella' && item.id === 'stella-tasks' ? 'page' : undefined} onClick={() => navigate(item.id)}><Icon size={18} />{wide && <span>{item.label}</span>}</button> })}</div>)}</nav>
    <section className="stella-sidebar-workspaces" aria-label="工作区与会话">{renderFactorySlot('workspace.browser', { wide, expandSidebar: () => { if (collapsed) toggleSidebar() } })}</section>
    <footer>{renderFactorySlot('settings.shell', { wide })}</footer>
  </aside>
}
