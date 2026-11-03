import React from 'react'
import { Link, useLocation } from 'react-router-dom'
import apiClient from '../api/client'
import { getLang } from '../i18n'
import { securityCopy } from '../pages/securityLocale'
import RunPolicyEditor from './RunPolicyEditor'
import { Feedback, useSecurityData } from './SecurityAuthorizationPanels'

const loadSubjects = () => apiClient.getRunPolicySubjects()
const card = 'min-w-0 rounded-card border border-default bg-surface p-5 sm:p-6'

export default function AgentPermissionsPanel() {
  const en = getLang() === 'en'
  const copy = securityCopy(getLang())
  const location = useLocation()
  const query = new URLSearchParams(location.search)
  const kind = query.get('kind') === 'session' ? 'session' : query.get('kind') === 'task' ? 'task' : 'template'
  const subject = kind === 'template' ? 'default' : query.get('subject') || ''
  const returnTo = query.get('returnTo') || ''
  const safeReturn = returnTo.startsWith('/') && !returnTo.startsWith('//') && !returnTo.startsWith('/security') ? returnTo : ''
  const state = useSecurityData(loadSubjects)
  return <div className="space-y-5 text-content">
    {safeReturn && <Link to={safeReturn} className="inline-flex min-h-10 items-center text-sm text-accent">{en ? 'Return to source' : '返回原页面'}</Link>}
    <section className={card}>
      <h2 className="text-base font-semibold text-content">{kind === 'template' ? (en ? 'Default permissions' : '默认权限') : `${kind === 'task' ? (en ? 'Task' : '任务') : (en ? 'Conversation' : '对话')} · ${subject}`}</h2>
      <p className="my-4 text-sm leading-6 text-content-secondary">{kind === 'template' ? copy.overviewDefaultHelp : (en ? 'This object keeps an independent snapshot. Changes do not update other conversations or tasks.' : '当前对象独立保存权限，修改不影响其他对话或任务。')}</p>
      {subject ? <RunPolicyEditor kind={kind} subject={subject} agent={query.get('agent') || undefined} detailed onSaved={() => { void state.refresh() }} /> : <p role="alert" className="text-sm text-danger">{en ? 'Select a conversation or task from its original page.' : '请从对应对话或任务页面选择需要编辑的对象。'}</p>}
      {kind !== 'template' && <Link to="/security?tab=agent-permissions" className="mt-4 inline-flex min-h-10 items-center text-sm text-accent">{en ? 'Manage defaults' : '管理默认权限'}</Link>}
    </section>
    <section className={card}>
      <h2 className="text-base font-semibold text-content">{en ? 'Existing permission snapshots' : '已有对象的权限快照'}</h2>
      <p className="my-3 text-sm leading-6 text-content-secondary">{en ? 'Each snapshot is independent. Object management uses the existing conversation and task entries.' : '各对象独立保存权限，普通管理仍使用现有对话与任务入口。'}</p>
      <Feedback {...state} />
      {state.data?.length === 0 && <p className="text-sm text-content-secondary">{en ? 'No permission snapshots yet.' : '暂无权限快照。'}</p>}
      {state.data?.map(row => <div key={`${row.agent}:${row.kind}:${row.subject}`} className="flex flex-wrap items-center justify-between gap-3 border-b border-default py-3 text-sm">
        <div className="min-w-0 flex-1"><p className="break-all">{row.kind === 'task' ? (en ? 'Task' : '任务') : (en ? 'Conversation' : '对话')} · {row.subject}</p>
          <p className="mt-1 text-xs text-content-secondary">{row.agent} · {copy.presets[row.policy.permission_mode || row.policy.filesystem_mode]?.name} · {row.migration_status === 'complete' ? (en ? 'Independent snapshot' : '独立快照') : (en ? 'Needs confirmation' : '需要确认')}</p>
        </div>
        <div className="flex flex-wrap gap-3"><Link className="text-accent" to={`/security?${new URLSearchParams({ tab: 'agent-permissions', kind: row.kind, subject: row.subject, agent: row.agent, ...(safeReturn ? { returnTo: safeReturn } : {}) })}`}>{en ? 'Set permissions' : '设置权限'}</Link>
          <Link className="text-accent" to={row.original_url}>{en ? 'Manage object' : '前往管理'}</Link></div>
      </div>)}
    </section>
  </div>
}
