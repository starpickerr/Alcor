import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, Loader2, Power } from 'lucide-react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import apiClient from '../../api/client'
import type { CapabilityDetail, CapabilityKind } from '../../api/capabilityTypes'
import Markdown from '../../components/Markdown'
import PageHeader from '../../components/PageHeader'
import { currentSettingsOrigin, type SettingsOrigin } from '../settings/origin'
import CapabilityConfiguration from './CapabilityConfiguration'
import CapabilityTabs from './CapabilityTabs'
import CapabilityFeatures from './CapabilityFeatures'
import { button, c, copy, kindIcon, kindLabel, panel, sourceLabel, providerStateLabel } from './presentation'

const tabLabel = (id: string) => ({ overview: c('概览', 'Overview'), features: c('功能', 'Features'), configuration: c('配置', 'Configuration'), runtime: c('运行', 'Runtime'), content: c('内容', 'Content') }[id] || id)

export default function CapabilityDetailPage({ baseUrl }: { baseUrl: string }) {
  const { kind: requestedKind = '', id = '' } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const kind = requestedKind as CapabilityKind
  const valid = ['plugins', 'mcp', 'skills', 'tools'].includes(kind)
  const [detail, setDetail] = useState<CapabilityDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [saveError, setSaveError] = useState('')
  const [busy, setBusy] = useState(false)
  const [configVisited, setConfigVisited] = useState(false)
  const request = useRef<AbortController | null>(null)
  const generation = useRef(0)
  const saving = useRef(false)
  const load = useCallback(async () => {
    request.current?.abort()
    const controller = new AbortController(); request.current = controller
    setLoading(true); setError('')
    try {
      if (!valid) throw new Error(c('此能力不存在', 'Capability not found'))
      const value = await apiClient.getCapabilityDetail(kind, id, controller.signal)
      if (!controller.signal.aborted) setDetail(value)
    } catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { if (!controller.signal.aborted) setLoading(false) }
  }, [kind, id, valid])
  useEffect(() => {
    generation.current += 1; saving.current = false
    setDetail(null); setSaveError(''); setBusy(false); setConfigVisited(false)
    apiClient.setBaseUrl(baseUrl); void load()
    return () => { generation.current += 1; request.current?.abort() }
  }, [baseUrl, load])
  const item = detail?.kind === kind && detail.id === id ? detail : null
  const params = new URLSearchParams(location.search)
  const selected = params.get('section') || 'overview'
  const section = item?.tabs.includes(selected) ? selected : 'overview'
  useEffect(() => { if (section === 'configuration') setConfigVisited(true) }, [section])
  const state = location.state as SettingsOrigin | null
  const origin = state?.from?.startsWith('/') && !state.from.startsWith('//') ? state : null
  const back = origin?.from || `/skills?tab=${valid ? kind : 'all'}`
  const changeSection = (value: string) => {
    const next = new URLSearchParams(location.search)
    if (value === 'overview') next.delete('section'); else next.set('section', value)
    navigate(`${location.pathname}${next.size ? `?${next}` : ''}`, { replace: true, state: location.state })
  }
  const toggle = async () => {
    if (!item || saving.current || loading) return
    saving.current = true; setBusy(true); setSaveError('')
    const current = generation.current
    try {
      const enabled = !item.enabled
      if (kind === 'plugins') await apiClient.pluginRequest('manage', { action: item.state === 'removed' ? 'reinstall' : enabled ? 'enable' : 'disable', pluginId: id })
      else {
        const result = kind === 'tools' ? await apiClient.toggleTool(id, enabled) : kind === 'skills' ? await apiClient.toggleSkill(id, enabled ? 'open' : 'close') : await apiClient.toggleMcpServer(id, enabled)
        if (result.status !== 'success') throw new Error(result.message || c('保存失败，请重试', 'Could not save. Retry.'))
      }
      if (generation.current === current) await load()
    } catch (cause) { if (generation.current === current) setSaveError(cause instanceof Error ? cause.message : String(cause)) }
    finally { if (generation.current === current) { saving.current = false; setBusy(false) } }
  }
  const Icon = valid ? kindIcon[kind] : kindIcon.tools
  const typeLabel = kind === 'tools' && item?.source === 'plugin' ? c('工具', 'Tools') : valid ? kindLabel(kind) : requestedKind
  return <div data-capability-detail className="flex-1 flex min-h-0 min-w-0 flex-col">
    <PageHeader><nav className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-content-secondary" aria-label={c('能力导航', 'Capability navigation')}>
      <Link to={back} state={origin ? { restoreScroll: origin.scroll } : undefined} className="inline-flex items-center gap-1.5 text-accent"><ArrowLeft size={16} />{c('返回原页面', 'Back to previous page')}</Link>
      <span className="inline-flex min-w-0 items-center gap-2"><Link to="/skills" className="hover:text-accent">{c('能力', 'Capabilities')}</Link><span>/</span><span>{typeLabel}</span></span>
    </nav></PageHeader>
    <main data-stella-scroll className="flex-1 min-h-0 min-w-0 overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-5 px-4 py-6 sm:px-6">
        {loading && !item && <p role="status" className="flex items-center gap-2 py-16 text-sm text-content-secondary"><Loader2 size={18} className="animate-spin" />{c('正在读取能力…', 'Loading capability…')}</p>}
        {error && <div role="alert" className={`${panel} space-y-3 p-5`}><p className="text-sm text-danger">{error}</p><button type="button" className={`${button} bg-surface-2`} disabled={loading || busy} onClick={() => void load()}>{c('重试', 'Retry')}</button></div>}
        {item && <>
          <section className={`${panel} space-y-5 p-5 sm:p-7`} aria-label={c('能力信息', 'Capability information')}>
            <div className="flex flex-wrap items-start gap-4 sm:gap-5">
              <span className="flex size-14 shrink-0 items-center justify-center rounded-card bg-surface-2 text-content-secondary"><Icon size={28} /></span>
              <div className="min-w-0 basis-48 flex-1"><h1 className="break-words text-2xl font-semibold tracking-tight sm:text-3xl">{copy(item.name)}</h1>
                <p className="mt-2 break-words text-sm text-content-secondary">{typeLabel} · {sourceLabel(item.source)}</p></div>
              <span className={`rounded-full px-3 py-1 text-xs font-medium ${item.enabled && !['failed', 'unavailable'].includes(item.state) ? 'bg-accent-soft text-accent' : 'bg-surface-2 text-content-secondary'}`}>{item.enabled ? c('已启用', 'Enabled') : c('已停用', 'Disabled')}</span>
            </div>
            <p className="break-words text-sm leading-relaxed text-content-secondary">{copy(item.description)}</p>
            <button type="button" disabled={busy || loading || ['maintenance', 'draining'].includes(item.state)} onClick={() => void toggle()}
              className={`${button} bg-surface-2 text-content hover:bg-accent-soft`}><Power size={16} />{busy ? c('处理中…', 'Saving…') : item.state === 'removed' ? c('重新安装', 'Reinstall') : item.enabled ? c('停用', 'Disable') : c('启用', 'Enable')}</button>
            {saveError && <p role="alert" className="text-sm text-danger">{saveError}</p>}
          </section>
          <CapabilityTabs label={c('能力详情', 'Capability details')} tabs={item.tabs.map(value => ({ id: value, label: tabLabel(value) }))} value={section} onChange={changeSection} />
          <section role="tabpanel" id="capability-content" aria-labelledby={`capability-tab-${section}`} tabIndex={0} className={`${panel} min-w-0 p-5 sm:p-7`}>
            {section === 'overview' && <div className="space-y-4"><Markdown content={copy(item.overview) || c('暂无详细介绍。', 'No description is available.')} />{item.reason && <p className="text-sm text-content-secondary">{copy(item.reason)}</p>}</div>}
            {section === 'content' && <Markdown content={item.content || c('暂无内容。', 'No content is available.')} />}
            {section === 'features' && <CapabilityFeatures key={`${kind}/${id}`} item={item} />}
            {section === 'runtime' && <div className="space-y-5"><div className="flex items-center justify-between gap-3"><h2 className="text-base font-semibold">{c('运行状态', 'Runtime state')}</h2><button className={`${button} bg-surface-2`} disabled={busy || loading} onClick={() => void load()}>{c('刷新', 'Refresh')}</button></div>
              {item.runtime.state && <dl className="divide-y divide-default"><div className="flex min-w-0 flex-wrap justify-between gap-3 py-3 text-sm"><dt className="text-content-secondary">{kind === 'mcp' ? c('连接状态', 'Connection state') : c('加载状态', 'Load state')}</dt><dd className="min-w-0 break-words">{providerStateLabel(kind, String(item.runtime.state || item.state))}</dd></div></dl>}
              {item.runtime.error && <div role="alert" className="space-y-2 text-sm"><h3 className="font-medium">{c('最近错误', 'Latest error')}</h3><p className="whitespace-pre-wrap break-words text-danger">{String(item.runtime.error)}</p></div>}
              <p className="text-sm text-content-secondary">{kind === 'mcp' ? c('连接状态不代表工具正在执行。当前接口未提供最近检查时间或详细诊断，请查看日志排查。', 'Connection state does not indicate tool execution. Check times and detailed diagnostics are not available from this interface; consult the logs.') : c('加载状态表示插件是否已在当前宿主注册，不代表任务正在执行。详细诊断请查看日志。', 'Load state indicates registration in the current host, not task execution. Consult the logs for detailed diagnostics.')}</p>
              <Link to="/logs" state={currentSettingsOrigin(location.pathname, location.search)} className="text-sm text-accent">{c('查看日志', 'View logs')}</Link></div>}
            {item.tabs.includes('configuration') && (configVisited || section === 'configuration') && <div hidden={section !== 'configuration'}><CapabilityConfiguration key={`${kind}/${id}`} item={item} baseUrl={baseUrl} /></div>}
          </section>
        </>}
      </div>
    </main>
  </div>
}
