import { Notice } from './notice.jsx'
import { BearLogo } from './bear-logo.jsx'
import React, { useRef, useState } from 'react'
import { Button, useModalLayer, IconNewChatOutlineRegular, IconPanelLeftOutlineRegular, IconPluginPinwheelOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { BusinessDesk, applicationViews } from './business.jsx'
import { BriefingWorkspace } from './briefing.jsx'
import { canonicalPanel } from './mode.js'

export const deskViews = applicationViews

export function SecretaryDesk({ startSession, openSession, hasModeSwitch, view = 'alcor-tasks', business, currentSession, briefingContext, openPlugins, briefingView }) {
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
    {view === 'alcor-briefing' && <BriefingWorkspace api={business} briefingContext={briefingContext} openSession={openSession} openPlugins={openPlugins} initialView={briefingView?.()} />}
    {application && view !== 'alcor-briefing' && <BusinessDesk key={view} view={view} api={business} currentSession={currentSession} startSession={begin} openSession={openSession} />}
    {!hasModeSwitch && <Notice role="status" className="alcor-muted">当前版本暂不支持模式切换。</Notice>}
  </section>
}

export function SecretarySidebar({ collapsed, drawer, width, navigate, startSession, toggleSidebar, usePanelInfo, useRemoteAccess, renderFactorySlot, renderQQ, t }) {
  const panel = usePanelInfo(value => value.activePanelId)
  const canManagePlugins = useRemoteAccess('pluginManager/listBundles')
  const dataViews = applicationViews
  const applications = canManagePlugins ? [...dataViews, { id: 'plugins', label: '插件', icon: IconPluginPinwheelOutlineRegular }] : dataViews
  const [error, setError] = useState('')
  const sidebar = useRef()
  const wide = drawer || !collapsed
  useModalLayer(sidebar, drawer && !collapsed, toggleSidebar)
  return <aside className="alcor-sidebar" ref={sidebar} style={{ width: wide ? width : 56 }} data-collapsed={!wide || undefined} data-shortcut-modal={drawer ? 'sidebar' : undefined} role={drawer && !collapsed ? 'dialog' : undefined} aria-modal={drawer && !collapsed ? true : undefined} tabIndex={-1} aria-label="Alcor 秘书导航">
    <div className="alcor-sidebar-brand"><span className="alcor-emblem" aria-hidden="true"><BearLogo size={32} /></span>{wide && <span>Alcor</span>}<button type="button" data-sidebar-toggle title={t(collapsed ? 'toggle.open' : 'toggle.collapse')} aria-label={t(collapsed ? 'toggle.open' : 'toggle.collapse')} onClick={toggleSidebar}><IconPanelLeftOutlineRegular size={17} /></button></div>
    <button type="button" className="alcor-sidebar-chat" title={t('session.new.label')} aria-label={t('session.new.label')} onClick={async () => { setError(''); try { await startSession() } catch (reason) { setError(reason.message) } }}><IconNewChatOutlineRegular size={17} />{wide && <span>新对话</span>}</button>
    {error && <Notice tone="error" role="alert">{error}</Notice>}
    <nav aria-label="秘书功能">{['应用'].map(group => <div className="alcor-nav-group" key={group}>{wide && <p>{group}</p>}{applications.map(item => { const Icon = item.icon; return <button key={item.id} type="button" title={item.label} aria-label={item.label} aria-current={canonicalPanel(panel) === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}><Icon size={18} />{wide && <span>{item.label}</span>}</button> })}</div>)}</nav>
    <section className="alcor-sidebar-workspaces" aria-label="工作区与会话">{renderFactorySlot('workspace.browser', { wide, renderQQ, expandSidebar: () => { if (collapsed) toggleSidebar() } })}</section>
    <footer>{renderFactorySlot('settings.shell', { wide })}</footer>
  </aside>
}
