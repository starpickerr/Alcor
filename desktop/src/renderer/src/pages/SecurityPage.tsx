import React, { useCallback, useEffect, useState } from 'react'
import { ShieldCheck, RefreshCw, ChevronRight, Clock3, Link2, KeyRound, Activity, Database, LockKeyhole, type LucideIcon } from 'lucide-react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import apiClient from '../api/client'
import type { SecurityActivityCounts, SecurityCenterState, SecurityRecord } from '../api/client'
import { getLang } from '../i18n'
import SensitiveResourcesPanel from '../components/SensitiveResourcesPanel'
import ExternalConnectionsPanel from '../components/ExternalConnectionsPanel'
import CredentialEditor from '../components/CredentialEditor'
import { useRestoreScroll } from './settings/origin'
import PageHeader from '../components/PageHeader'
import AgentPermissionsPanel from '../components/AgentPermissionsPanel'
import { RuntimeAuthorizationPanel, PermissionMigrationPanel } from '../components/SecurityAuthorizationPanels'
import { useSessionStore } from '../store/sessionStore'
import { SECURITY_POLICY_CHANGED } from '../store/securityPolicyEvents'
import type { ToolInfo } from '../types'
import {
  securityCopy,
  securityReason,
  securityResource,
  securitySecretName,
  securityTrace,
} from './securityLocale'

const toolTitle = (toolId: string, catalog: ToolInfo[]) => {
  const lang = getLang() === 'en' ? 'en' : 'zh'
  const match = catalog.find((item) => item.id === toolId)
  return match?.title?.[lang] || match?.title?.zh || toolId
}

const tabs = ['overview', 'agent-permissions', 'runtime', 'connections', 'resources'] as const
type Tab = (typeof tabs)[number]
const tabIcons: Record<Tab, LucideIcon> = {
  overview: ShieldCheck,
  'agent-permissions': ShieldCheck,
  resources: KeyRound, connections: Link2, runtime: LockKeyhole,
}

const modeLabel = (
  copy: ReturnType<typeof securityCopy>,
  mode: string | null | undefined,
) => {
  if (!mode) return copy.overviewUnavailable
  return (copy.presets as Record<string, { name: string }>)[mode]?.name || mode
}

const card = 'min-w-0 rounded-card border border-default bg-surface p-5 sm:p-6'
const overviewCard = `${card} shadow-sm`
const metricValue = 'text-3xl font-semibold tracking-tight tabular-nums leading-none'
const heading = 'text-base font-semibold text-content leading-snug'
const input =
  'min-w-0 min-h-10 rounded-btn border border-default bg-surface px-3 py-2 text-sm text-content hover:border-strong focus-visible:outline-2 focus-visible:outline-accent'
const action =
  'min-h-10 shrink-0 rounded-btn bg-accent text-accent-contrast px-4 py-2 text-sm font-medium hover:bg-accent-hover disabled:bg-surface-2 disabled:text-content-tertiary focus-visible:outline-2 focus-visible:outline-accent'
const faded = 'text-sm leading-relaxed text-content-secondary'

const SectionTitle: React.FC<{ icon: LucideIcon; to?: string; children: React.ReactNode }> = ({ icon: Icon, to, children }) => {
  const title = <>
    <span className="flex size-8 shrink-0 items-center justify-center rounded-btn bg-accent-soft text-accent">
      <Icon size={17} aria-hidden="true" />
    </span>
    <span className="min-w-0 flex-1">{children}</span>
    {to && <ChevronRight size={17} aria-hidden="true" className="shrink-0 text-content-tertiary group-hover:text-accent-active" />}
  </>
  return <h2 className={heading}>
    {to ? <Link to={to} className="group flex min-h-10 items-center gap-2.5 rounded-btn hover:text-accent-active dark:hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent">{title}</Link>
      : <span className="flex items-center gap-2.5">{title}</span>}
  </h2>
}

const OverviewMetrics: React.FC<{ items: { label: string; value: React.ReactNode }[] }> = ({ items }) => (
  <dl className="grid grid-cols-3 divide-x divide-default">
    {items.map(item => <div key={item.label} className="min-w-0 px-3 first:pl-0 last:pr-0">
      <dt className="min-h-10 text-xs leading-5 text-content-secondary">{item.label}</dt>
      <dd className={`${typeof item.value === 'number' ? metricValue : 'text-sm font-medium leading-5'} mt-1`}>{item.value}</dd>
    </div>)}
  </dl>
)

const SecurityPage: React.FC<{ baseUrl: string }> = ({ baseUrl }) => {
  const copy = securityCopy(getLang())
  const sid = useSessionStore((s) => s.activeId)
  const location = useLocation()
  const navigate = useNavigate()
  const requestedTab = new URLSearchParams(location.search).get('tab')
  const tab: Tab = (tabs as readonly string[]).includes(requestedTab || '') ? requestedTab as Tab : 'overview'
  const setTab = (next: Tab) => navigate(next === 'overview' ? '/security' : `/security?tab=${next}`)
  const [center, setCenter] = useState<SecurityCenterState | null>(null)
  useRestoreScroll(!!center)
  useEffect(() => {
    const aliases: Record<string, string> = { secrets: 'resources', plugins: 'runtime', activity: 'overview', permissions: 'agent-permissions' }
    const ownerCredential = new URLSearchParams(location.search).get('credential') || new URLSearchParams(location.search).get('secret') || ''
    if ((requestedTab === 'secrets' || requestedTab === 'resources') && (ownerCredential.endsWith('_API_KEY') && ownerCredential !== 'BOCHA_API_KEY' || ownerCredential.startsWith('CUSTOM:'))) {
      navigate('/models?credential=' + encodeURIComponent(ownerCredential), { replace: true, state: { from: '/security?tab=resources', scroll: 0 } })
      return
    }
    if (requestedTab && aliases[requestedTab]) {
      const query = new URLSearchParams(location.search)
      const plugin = query.get('plugin')
      if (requestedTab === 'plugins' && plugin) {
        navigate(`/skills/plugins/${encodeURIComponent(plugin)}?section=configuration`, { replace: true, state: { from: '/security?tab=runtime', scroll: 0 } })
        return
      }
      const secret = query.get('secret') || ''
      if (requestedTab === 'secrets' && (secret.endsWith('_API_KEY') || secret.startsWith('CUSTOM:'))) {
        navigate('/models?credential=' + encodeURIComponent(secret), { replace: true, state: { from: '/security?tab=resources', scroll: 0 } })
        return
      }
      const target = aliases[requestedTab]
      query.delete('tab'); query.delete('plugin')
      if (query.has('secret')) { query.set('credential', query.get('secret') || ''); query.delete('secret') }
      if (target !== 'overview') query.set('tab', target)
      navigate('/security' + (query.size ? '?' + query.toString() : ''), { replace: true })
    }
    if (requestedTab === 'privacy') navigate('/data?section=usage', { replace: true })
    if (requestedTab === 'background') navigate('/tasks', { replace: true })
  }, [requestedTab, navigate, location.hash, center])
  const [records, setRecords] = useState<SecurityRecord[]>([])
  const [availableEvents, setAvailableEvents] = useState<SecurityRecord[]>([])
  const [activityCounts, setActivityCounts] = useState<SecurityActivityCounts | null>(null)
  const [toolCatalog, setToolCatalog] = useState<ToolInfo[]>([])
  const [pending, setPending] = useState<
    {
      id: string
      tool: string
      session: string | null
      summary: string
      resource: string
      risk: string
      reason: string
      expanded_scope: string
      effect: string
    }[]
  >([])
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [sinceDraft, setSinceDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [password, setPassword] = useState('')

  const load = useCallback(async () => {
    try {
      const queryFilters = { ...filters }
      if (queryFilters.since)
        queryFilters.since = String(Math.floor(new Date(queryFilters.since + 'T00:00:00').getTime() / 1000))
      const filtered = Object.values(filters).some(Boolean)
      const [state, events, confirmations, allEvents, tools] = await Promise.all([
        apiClient.getSecurityCenter(sid || undefined),
        apiClient.getSecurityRecords(queryFilters),
        apiClient.getSecurityConfirmations(),
        filtered ? apiClient.getSecurityRecords() : Promise.resolve(null),
        apiClient.getTools().catch(() => [] as ToolInfo[]),
      ])
      if (state.status !== 'success') throw new Error(copy.loading)
      setCenter(state)
      setRecords(events.records)
      setAvailableEvents(allEvents?.records || events.records)
      setActivityCounts(state.summary.activity_counts || events.counts || null)
      setToolCatalog(tools)
      setPending(confirmations as typeof pending)
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : copy.saveFailed)
    }
  }, [sid, filters, copy])
  useEffect(() => {
    apiClient.setBaseUrl(baseUrl)
    void load()
  }, [baseUrl, load])
  useEffect(() => {
    const onPolicyChanged = () => { void load() }
    window.addEventListener(SECURITY_POLICY_CHANGED, onPolicyChanged)
    return () => window.removeEventListener(SECURITY_POLICY_CHANGED, onPolicyChanged)
  }, [load])
  const mutate = async (fn: () => Promise<{ status: string; message?: string }>, success = copy.saved) => {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await fn()
      if (result.status !== 'success') throw new Error(result.message || copy.saveFailed)
      setNotice(success)
      await load()
      return true
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : copy.saveFailed)
      return false
    } finally {
      setBusy(false)
    }
  }
  const credentialName = new URLSearchParams(location.search).get('credential') || ''
  const eventList = (items: SecurityRecord[]) =>
    items.length ? (
      <div className="divide-y divide-default">
        {items.map((item, index) => {
          const outcome = (copy.outcomes as Record<string, string>)[item.decision] || copy.reasonFallback
          const eventLabel = (copy.events as Record<string, string>)[item.event]
          const namedTool = toolTitle(item.tool, toolCatalog)
          const event =
            item.event === 'tool_decision' || !eventLabel
              ? namedTool
              : eventLabel
          const risk = (copy.risk as Record<string, string>)[item.risk] || copy.risk.L2
          const trace = securityTrace(copy, item.trace || [])
          return (
            <details key={`${item.at}-${index}`} className="group py-3 text-sm">
              <summary className="cursor-pointer list-none flex items-start gap-3 focus-visible:outline-2 focus-visible:outline-accent">
                <span
                  className={`mt-0.5 inline-block size-2.5 rounded-full shrink-0 ${item.decision === 'deny' ? 'bg-danger' : item.decision === 'confirm' ? 'bg-amber-500' : 'bg-accent'}`}
                />
                <span className="min-w-0 flex-1">
                  <span className="font-medium">
                    {event} · {securityResource(copy, item.resource)}
                  </span>
                  <span className="block text-xs text-content-secondary mt-1">
                    {new Date(item.at * 1000).toLocaleString(getLang() === 'en' ? 'en-US' : 'zh-CN')}
                    {' · '}
                    {risk}
                  </span>
                </span>
                <span className="text-xs font-medium text-content-secondary shrink-0">{outcome}</span>
                <ChevronRight
                  size={15}
                  className="text-content-tertiary shrink-0 transition-transform group-open:rotate-90"
                />
              </summary>
              <div className="mt-3 ml-5 space-y-2 text-content-secondary break-words">
                <p className="text-content">
                  {copy.reason}
                  {copy.fieldSeparator}
                  {securityReason(copy, item)}
                </p>
                <p>
                  {copy.assistant}
                  {copy.fieldSeparator}
                  {item.agent === 'default' ? 'Stella' : item.agent}
                </p>
                {item.project && (
                  <p>
                    {copy.project}
                    {copy.fieldSeparator}
                    {item.project}
                  </p>
                )}
                {item.conversation && (
                  <p>
                    {copy.conversation}
                    {copy.fieldSeparator}
                    {item.conversation}
                  </p>
                )}
                {trace.length > 0 && (
                  <div className="rounded-btn bg-surface-2 px-3 py-2 text-xs space-y-1">
                    <p className="font-medium text-content">{copy.decisionSteps}</p>
                    {trace.map((step, i) => (
                      <p key={i}>{step}</p>
                    ))}
                  </div>
                )}
              </div>
            </details>
          )
        })}
      </div>
    ) : (
      <p className={faded}>{copy.noEvents}</p>
    )

  return (
    <div
      lang={getLang() === 'en' ? 'en-US' : 'zh-CN'}
      className="h-full flex flex-col min-h-0 min-w-0"
    >
      <PageHeader className="flex justify-between items-start gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-3">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-card bg-accent-soft text-accent">
              <ShieldCheck size={23} aria-hidden="true" />
            </span>
            {copy.title}
          </h1>
          <p className={`${faded} mt-2`}>{copy.subtitle}</p>
        </div>
        <button type="button" className={`${input} shrink-0`} aria-label={copy.refresh} title={copy.refresh} onClick={() => void load()}>
          <RefreshCw size={17} />
        </button>
      </PageHeader>
      <div data-stella-scroll className="flex-1 min-h-0 overflow-y-auto px-4 sm:px-6 py-5 sm:py-6">
      <div data-stella-scroll-content className="max-w-5xl mx-auto space-y-6 text-content">
        <nav aria-label={copy.title} className="flex gap-1 overflow-x-auto border-b border-default pb-3">
          {tabs.map((name) => {
            const Icon = tabIcons[name]
            return (
            <button
              type="button"
              key={name}
              onClick={() => setTab(name)}
              aria-current={tab === name ? 'page' : undefined}
              className={`flex items-center gap-2 shrink-0 min-h-10 px-3 py-2 rounded-btn text-sm focus-visible:outline-2 focus-visible:outline-accent ${tab === name ? 'bg-accent-soft text-content font-semibold' : 'text-content-secondary hover:bg-surface-2'}`}
            >
              <Icon size={16} aria-hidden="true" className={tab === name ? 'text-accent' : 'text-content-tertiary'} />
              {copy.tabs[name]}
            </button>
          )})}
        </nav>
        {notice && (
          <p role="status" className="rounded-btn border border-default bg-accent-soft px-4 py-3 text-sm text-content">
            {notice}
          </p>
        )}
        {error && (
          <p role="alert" className="rounded-btn border border-danger-border bg-danger-soft px-4 py-3 text-sm text-danger">
            {error}
          </p>
        )}
        {!center ? (
          <p className={faded}>{copy.loading}</p>
        ) : (
          <>
            {tab === 'overview' && (() => {
              const globalMode = center.global_policy.permission_mode || center.global_policy.preset
              const taskSummary = center.summary.background_tasks
              const connectionPreview = center.connections.slice(0, 3)
              const secretPreview = center.secrets.slice(0, 4)
              const dataResources = center.data_access?.resources || []
              return (
              <div className="grid lg:grid-cols-2 gap-4 sm:gap-5">
                <section className={`${overviewCard} lg:col-span-2 flex flex-col lg:flex-row lg:items-center gap-5 lg:gap-8`}>
                  <div className="min-w-0 flex-1">
                    <SectionTitle icon={ShieldCheck} to="/security?tab=agent-permissions">{copy.presetsTitle}</SectionTitle>
                    <p className={`${faded} mt-3`}>{copy.overviewDefaultHelp}</p>
                  </div>
                  <div className="shrink-0 rounded-btn bg-accent-soft px-5 py-4 lg:min-w-52">
                    <p className="text-xs text-content-secondary">{copy.currentProtection}</p>
                    <p className="text-2xl font-semibold tracking-tight mt-1.5">{modeLabel(copy, globalMode)}</p>
                  </div>
                </section>
                <section className={overviewCard}>
                  <SectionTitle icon={Clock3} to="/tasks">{copy.overviewBackgroundTitle}</SectionTitle>
                  {!taskSummary || taskSummary.available === false ? (
                    <p className={`${faded} mt-4`}>{copy.overviewUnavailable}</p>
                  ) : (
                    <div className="mt-5">
                      <OverviewMetrics items={[
                        { label: copy.overviewTotalTasks, value: taskSummary.total ?? copy.overviewUnavailable },
                        { label: copy.backgroundEnabled, value: taskSummary.enabled ?? copy.overviewUnavailable },
                        { label: copy.backgroundPaused, value: taskSummary.paused ?? copy.overviewUnavailable },
                      ]} />
                      {taskSummary.modes && (
                        <div className="mt-5 border-t border-default pt-3 flex flex-wrap gap-x-3 gap-y-2 text-xs leading-5 text-content-secondary">
                          {(['read-only', 'workspace-write', 'full-access'] as const).map((mode) => (
                            <span key={mode}>
                              {modeLabel(copy, mode)} <span className="font-medium tabular-nums text-content">{taskSummary.modes?.[mode] ?? 0}</span>
                            </span>
                          ))}
                          {(taskSummary.unknown || 0) > 0 && (
                            <span>{copy.overviewUnknownPermissions(taskSummary.unknown || 0)}</span>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </section>
                <section className={overviewCard}>
                  <SectionTitle icon={Link2} to="/security?tab=connections">{copy.overviewConnectionsTitle}</SectionTitle>
                  <div className="mt-5 mb-4 flex items-end gap-2">
                    <p className={metricValue}>
                      {typeof center.summary.connection_count === 'number'
                        ? center.summary.connection_count
                        : copy.overviewUnavailable}
                    </p>
                    <span className="pb-0.5 text-xs text-content-secondary">{copy.connectionsCount}</span>
                  </div>
                  <div className="divide-y divide-default border-t border-default text-sm">
                    {connectionPreview.map((item) => (
                      <div key={item.id} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 py-2.5">
                        <span className="min-w-0 font-medium break-words">{(copy.connectionNames as Record<string, string>)[item.id] || item.name}</span>
                        <span className="inline-flex items-center gap-1.5 text-xs text-content-secondary">
                          <span aria-hidden="true" className={`size-1.5 shrink-0 rounded-full ${item.connected ? 'bg-accent' : 'bg-content-tertiary'}`} />
                          {item.connected ? copy.connected : copy.disconnected}
                          {item.connected && item.allowed === false && <> · {copy.accessRevoked}</>}
                        </span>
                      </div>
                    ))}
                    {center.connections.length === 0 && <p className={`${faded} py-3`}>{copy.noConnections}</p>}
                    {center.connections.length > 3 && (
                      <p className="pt-3 text-xs text-content-secondary">{copy.overviewMoreItems(center.connections.length - 3)}</p>
                    )}
                  </div>
                </section>
                <section className={overviewCard}>
                  <SectionTitle icon={KeyRound} to="/security?tab=resources">{copy.overviewSecretsTitle}</SectionTitle>
                  <div className="mt-5 mb-4 flex items-end gap-2">
                    <p className={metricValue}>
                      {typeof center.summary.secret_count === 'number'
                        ? center.summary.secret_count
                        : copy.overviewUnavailable}
                    </p>
                    <span className="pb-0.5 text-xs text-content-secondary">{copy.overviewSecretCountLabel}</span>
                  </div>
                  <div className="divide-y divide-default border-t border-default text-sm">
                    {secretPreview.map((item) => (
                      <div key={item.name} className="flex items-center justify-between gap-3 py-2.5">
                        <span className="font-medium break-words min-w-0">{securitySecretName(copy, item.name, item.display_name)}</span>
                        <span className="inline-flex items-center gap-1.5 text-xs text-content-secondary shrink-0">
                          <span aria-hidden="true" className={`size-1.5 rounded-full ${item.configured ? 'bg-accent' : 'bg-content-tertiary'}`} />
                          {item.configured ? copy.configured : copy.notConfigured}
                        </span>
                      </div>
                    ))}
                    {center.secrets.length === 0 && <p className={`${faded} py-3`}>{copy.noSecrets}</p>}
                    {center.secrets.length > 4 && (
                      <p className="pt-3 text-xs text-content-secondary">{copy.overviewMoreItems(center.secrets.length - 4)}</p>
                    )}
                  </div>
                </section>

                <section className={`${overviewCard} lg:col-span-2`}>
                  <SectionTitle icon={Database} to="/data?section=usage">{copy.overviewPrivacyTitle}</SectionTitle>
                  <div className="mt-4 space-y-2 text-sm">
                    {dataResources.slice(0, 4).map(item => <p key={item.id}>{item.name} <span className="text-content-secondary">{item.provider}</span></p>)}
                    <Link className="text-accent" to="/data">{getLang() === 'zh' ? '管理数据' : 'Manage data'}</Link>
                  </div>
                </section>
              </div>
              )
            })()}
            {tab === 'overview' && <PermissionMigrationPanel />}
            {tab === 'agent-permissions' && <AgentPermissionsPanel />}
            {tab === 'resources' && <div className="space-y-5"><SensitiveResourcesPanel />
              {credentialName && <section className={card}><h2 className={heading}>{copy.manageSecret} · {credentialName}</h2><CredentialEditor name={credentialName} configured={center.secrets.find(item => item.name === credentialName)?.configured} /></section>}
            </div>}
            {tab === 'connections' && <ExternalConnectionsPanel />}
            {tab === 'runtime' && <div className={card}><RuntimeAuthorizationPanel /></div>}
            {tab === 'overview' && <details className="rounded-card border border-default"><summary className="cursor-pointer px-5 py-4 text-sm text-content">{copy.appPassword}</summary>
                <section className={card}>
                  <SectionTitle icon={LockKeyhole}>{copy.appPassword}</SectionTitle>
                  <p className={`${faded} mt-3`}>{copy.appPasswordHelp}</p>
                  <p className="text-xs text-content-secondary mt-3 flex items-center gap-2">
                    <span aria-hidden="true" className={`size-1.5 rounded-full ${center.summary.app_password_set ? 'bg-accent' : 'bg-content-tertiary'}`} />
                    {center.summary.app_password_set ? copy.configured : copy.notConfigured}
                  </p>
                  <div className="flex flex-col md:flex-row gap-3 mt-3">
                    <input
                      type="password"
                      autoComplete="new-password"
                      className={`${input} flex-1`}
                      aria-label={copy.newPassword}
                      placeholder={copy.newPassword}
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                    />
                    <button
                      disabled={busy || !password}
                      className={action}
                      onClick={() => {
                        void mutate(() => apiClient.updateConfig({ web_password: password })).then((ok) => {
                          if (ok) setPassword('')
                        })
                      }}
                    >
                      {copy.changePassword}
                    </button>
                    {center.summary.app_password_set && <button type="button" disabled={busy} className={`${input} text-danger`} onClick={() => {
                      if (window.confirm(getLang() === 'zh' ? '清除访问密码？' : 'Clear the access password?')) void mutate(() => apiClient.updateConfig({ web_password: '' }))
                    }}>{getLang() === 'zh' ? '清除访问密码' : 'Clear access password'}</button>}
                  </div>
                </section>
            </details>}
            {tab === 'overview' && (
              <section className={card}>
                <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-2 mb-3">
                  <div>
                    <SectionTitle icon={Activity}>{copy.activityTitle}</SectionTitle>
                    <p className={`${faded} mt-1`}>{copy.activityHelp}</p>
                  </div>
                  <button
                    type="button"
                    className="text-xs text-accent shrink-0 self-end"
                    onClick={() => {
                      setFilters({})
                      setSinceDraft('')
                    }}
                  >
                    {copy.clearFilters}
                  </button>
                </div>
                {activityCounts && (
                  <div className="mb-4 grid grid-cols-1 sm:grid-cols-3 gap-3 text-sm">
                    <div className="rounded-btn bg-surface-2 px-3 py-2">
                      <span className="text-content-secondary">{copy.autoAllow}</span>
                      <p className="font-medium mt-1">{activityCounts.auto_allow}</p>
                    </div>
                    <div className="rounded-btn bg-surface-2 px-3 py-2">
                      <span className="text-content-secondary">{copy.userAuthorized}</span>
                      <p className="font-medium mt-1">{activityCounts.user_authorized}</p>
                    </div>
                    <div className="rounded-btn bg-surface-2 px-3 py-2">
                      <span className="text-content-secondary">{copy.deniedCount}</span>
                      <p className="font-medium mt-1">{activityCounts.denied}</p>
                    </div>
                    {activityCounts.uncertain && (
                      <p className="sm:col-span-3 text-xs text-content-secondary">{copy.activityStatsUncertain}</p>
                    )}
                  </div>
                )}
                <div className="grid sm:grid-cols-3 gap-3 mb-4">
                  <label className="text-xs text-content-secondary">
                    {copy.filterAgent}
                    <select
                      className={`${input} w-full mt-1`}
                      value={filters.agent || ''}
                      onChange={(event) => setFilters({ ...filters, agent: event.target.value })}
                    >
                      <option value="">{copy.all}</option>
                      {[...new Set(availableEvents.map((event) => event.agent))].map((agent) => (
                        <option key={agent} value={agent}>
                          {agent === 'default' ? 'Stella' : agent}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs text-content-secondary">
                    {copy.filterProject}
                    <select
                      className={`${input} w-full mt-1`}
                      value={filters.project || ''}
                      onChange={(event) => setFilters({ ...filters, project: event.target.value })}
                    >
                      <option value="">{copy.all}</option>
                      {center.projects.map((item) => (
                        <option key={item.path} value={item.path}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs text-content-secondary">
                    {copy.filterTool}
                    <select
                      className={`${input} w-full mt-1`}
                      value={filters.tool || ''}
                      onChange={(event) => setFilters({ ...filters, tool: event.target.value })}
                    >
                      <option value="">{copy.all}</option>
                      {[...new Set(availableEvents.map((event) => event.tool))].map((tool) => (
                        <option key={tool} value={tool}>
                          {toolTitle(tool, toolCatalog)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs text-content-secondary">
                    {copy.filterRisk}
                    <select
                      className={`${input} w-full mt-1`}
                      value={filters.risk || ''}
                      onChange={(event) => setFilters({ ...filters, risk: event.target.value })}
                    >
                      <option value="">{copy.all}</option>
                      {(['L0', 'L1', 'L2', 'L3', 'L4'] as const).map((risk) => (
                        <option key={risk} value={risk}>
                          {copy.risk[risk]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs text-content-secondary">
                    {copy.filterDecision}
                    <select
                      className={`${input} w-full mt-1`}
                      value={filters.decision || ''}
                      onChange={(event) => setFilters({ ...filters, decision: event.target.value })}
                    >
                      <option value="">{copy.all}</option>
                      {(
                        [
                          'allow',
                          'confirm',
                          'deny',
                          'policy_change',
                          'secret_use',
                          'external_access',
                        ] as const
                      ).map((outcome) => (
                        <option key={outcome} value={outcome}>
                          {copy.outcomes[outcome]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs text-content-secondary">
                    {copy.filterSince}
                    <input
                      type="text"
                      inputMode="numeric"
                      className={`${input} w-full mt-1`}
                      placeholder={copy.datePlaceholder}
                      value={sinceDraft}
                      onChange={(event) => {
                        const value = event.target.value.trim()
                        setSinceDraft(value)
                        if (
                          !value ||
                          (/^\d{4}-\d{2}-\d{2}$/.test(value) &&
                            !Number.isNaN(new Date(value + 'T00:00:00').getTime()))
                        ) {
                          setFilters({ ...filters, since: value })
                        }
                      }}
                    />
                  </label>
                </div>
                <div className="mb-3 flex justify-end"><button type="button" disabled={busy} className={`${input} text-danger`} onClick={() => {
                  if (window.confirm(copy.privacyConfirmClear)) void mutate(() => apiClient.cleanupPrivacy('clear_security_activity'), copy.saved)
                }}>{copy.privacyClearActivity}</button></div>
                {eventList(records)}
              </section>
            )}

          </>
        )}
      </div>
      </div>
    </div>
  )
}
export default SecurityPage
