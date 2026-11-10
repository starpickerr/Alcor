import './capabilities/capabilities.css'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronRight, Layers2, Loader2, Plus, Search } from 'lucide-react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import apiClient from '../api/client'
import type { CapabilityItem, CapabilityKind } from '../api/capabilityTypes'
import PageHeader from '../components/PageHeader'
import { currentSettingsOrigin } from './settings/origin'
import CapabilityTabs from './capabilities/CapabilityTabs'
import AddCapability from './capabilities/AddCapability'
import { button, c, copy, detailPath, kindIcon, kindLabel, panel, capabilityStatusLabel } from './capabilities/presentation'

export default function SkillsPage({ baseUrl }: { baseUrl: string }) {
  const location = useLocation()
  const navigate = useNavigate()
  const params = new URLSearchParams(location.search)
  const requested = params.get('tab') || location.hash.slice(1)
  const kinds: CapabilityKind[] = ['plugins', 'mcp', 'skills', 'tools']
  const tab = kinds.includes(requested as CapabilityKind) ? requested : 'all'
  const query = params.get('q') || ''
  const [items, setItems] = useState<CapabilityItem[]>([])
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState(Boolean(query))
  const [adding, setAdding] = useState(false)
  const request = useRef<AbortController | null>(null)
  const load = useCallback(async () => {
    request.current?.abort()
    const controller = new AbortController(); request.current = controller
    setLoading(true); setError('')
    try {
      const data = await apiClient.getCapabilities(controller.signal)
      if (!controller.signal.aborted) { setItems(data.items); setErrors(data.errors || {}) }
    } catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { if (!controller.signal.aborted) setLoading(false) }
  }, [])
  useEffect(() => { apiClient.setBaseUrl(baseUrl); void load(); return () => request.current?.abort() }, [baseUrl, load])
  useEffect(() => {
    const scroll = (location.state as { restoreScroll?: number } | null)?.restoreScroll
    if (!loading && typeof scroll === 'number') document.querySelector('[data-stella-scroll]')?.scrollTo(0, scroll)
  }, [loading, location.state])
  const select = (next: string, term = query) => {
    const values = new URLSearchParams()
    if (next !== 'all') values.set('tab', next)
    if (term) values.set('q', term)
    navigate(`/skills${values.size ? `?${values}` : ''}`, { replace: true, state: location.state })
  }
  const plugin = params.get('plugin')
  if (params.get('view') === 'settings') {
    return <Navigate to={plugin ? detailPath('plugins', plugin, 'configuration') : '/skills?tab=plugins'} replace state={location.state} />
  }
  const rows = items.filter(item => (tab === 'all' || item.kind === tab) &&
    `${copy(item.name)} ${copy(item.description)} ${item.id}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  return <div className="flex-1 flex min-h-0 flex-col">
    <PageHeader className="flex items-center justify-between gap-4">
      <h1 className="inline-flex items-center gap-2.5 text-xl font-bold"><Layers2 size={22} className="text-content-secondary" />{c('能力', 'Capabilities')}</h1>
      <div className="flex items-center gap-1">
        <button type="button" aria-label={c('搜索能力', 'Search capabilities')} aria-expanded={search} onClick={() => setSearch(value => !value)} className="rounded-btn p-2 text-content-secondary hover:bg-surface-2"><Search size={20} /></button>
        <button type="button" aria-label={c('添加能力', 'Add capability')} onClick={() => setAdding(true)} className="rounded-btn p-2 text-content-secondary hover:bg-surface-2"><Plus size={21} /></button>
      </div>
    </PageHeader>
    <main data-stella-scroll className="capability-directory flex-1 min-h-0 overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-5 px-4 py-6 sm:px-6">
        {search && <label className="flex items-center gap-2 rounded-btn border border-default bg-surface px-3 py-2 text-content-secondary">
          <Search size={17} /><input type="search" aria-label={c('搜索能力', 'Search capabilities')} autoFocus value={query} onChange={event => select(tab, event.target.value)}
            placeholder={c('搜索名称、功能或说明', 'Search names, functions or descriptions')} className="min-w-0 flex-1 bg-transparent text-sm text-content outline-none" /></label>}
        <CapabilityTabs label={c('能力分类', 'Capability categories')} value={tab} onChange={value => select(value)} tabs={[{ id: 'all', label: c('全部', 'All') }, ...kinds.map(kind => ({ id: kind, label: kindLabel(kind) }))]} />
        <div role="tabpanel" id="capability-content" aria-labelledby={`capability-tab-${tab}`} tabIndex={0} className="space-y-4">
          {(error || Object.keys(errors).length > 0) && <div role="alert" className={`${panel} flex flex-wrap items-center justify-between gap-3 p-4 text-sm`}>
            <p className="text-danger">{error || `${c('部分目录暂不可用', 'Some inventories are unavailable')}: ${Object.keys(errors).map(kind => kindLabel(kind as CapabilityKind)).join('、')}`}</p>
            <button type="button" className={`${button} bg-surface-2`} disabled={loading} onClick={() => void load()}>{c('重试', 'Retry')}</button>
          </div>}
          {loading ? <p role="status" className="flex items-center justify-center gap-2 py-20 text-sm text-content-secondary"><Loader2 size={18} className="animate-spin" />{c('正在读取能力…', 'Loading capabilities…')}</p> : !rows.length ?
            <div className={`${panel} p-8 text-center text-sm text-content-secondary`}>{query ? c('没有匹配的能力，试试其他关键词。', 'No matches. Try another search.') : c('此分类暂无能力。', 'No capabilities in this category.')}</div> :
            <div className={`${panel} divide-y divide-default overflow-hidden`}>{rows.map(item => {
              const Icon = kindIcon[item.kind]
              return <Link key={`${item.kind}/${item.id}`} aria-label={copy(item.name)} to={detailPath(item.kind, item.id)} state={currentSettingsOrigin(location.pathname, location.search)}
                className="capability-row group flex min-w-0 items-center gap-4 px-5 py-5 transition-colors hover:bg-surface-2 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent sm:gap-5 sm:px-6 sm:py-6">
                <span className="capability-row-icon flex size-12 shrink-0 items-center justify-center rounded-card bg-surface-2 text-content-secondary group-hover:text-accent"><Icon size={23} /></span>
                <div className="min-w-0 flex-1 space-y-1"><h2 className="break-words text-base font-semibold text-content">{copy(item.name)}</h2>
                  <p className="text-xs text-content-secondary">{kindLabel(item.kind)} · {capabilityStatusLabel(item)}</p>
                  <p className="break-words text-sm leading-relaxed text-content-secondary">{copy(item.description)}</p></div>
                <ChevronRight size={19} aria-hidden="true" className="capability-row-chevron shrink-0 text-content-tertiary group-hover:text-accent" />
              </Link>
            })}</div>}
        </div>
      </div>
    </main>
    {adding && <AddCapability onClose={() => setAdding(false)} onInstalled={id => { setAdding(false); navigate(detailPath('plugins', id)) }} />}
  </div>
}
