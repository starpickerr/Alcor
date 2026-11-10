import { useEffect, useRef, useState } from 'react'
import { BookOpen, ChevronRight, Code2, LayoutPanelTop, Plug, Wrench } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import type { CapabilityDetail, CapabilityFeature } from '../../api/capabilityTypes'
import { t } from '../../i18n'
import { pluginPageBinding } from '../../layout/navigation'
import { currentSettingsOrigin } from '../settings/origin'
import { c, copy } from './presentation'
import apiClient from '../../api/client'
import { Toggle } from '../settings/primitives'
import './capabilities.css'

const rowClass = 'capability-feature-row flex min-w-0 items-center gap-3 px-4 py-3.5 sm:gap-4'
const iconClass = 'capability-feature-icon flex size-9 shrink-0 items-center justify-center rounded-btn bg-surface text-content-secondary'
const focusClass = 'focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent'

function ToolRow({ feature, name, item, onSaved }: { feature: CapabilityFeature; name: string; item: CapabilityDetail; onSaved: (id: string, enabled: boolean) => void }) {
  const [enabled, setEnabled] = useState(feature.enabled !== false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const saving = useRef(false)
  const active = useRef(true)
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  useEffect(() => { setEnabled(feature.enabled !== false) }, [feature.enabled])
  const change = async (next: boolean) => {
    if (saving.current || !feature.toggleId) return
    saving.current = true; setBusy(true); setError('')
    try {
      const result = await apiClient.toggleProviderTool(item.kind, item.id, feature.id, next)
      if (result.status !== 'success') throw new Error(result.message || c('保存失败，请重试。', 'Could not save. Retry.'))
      if (active.current) { setEnabled(next); onSaved(feature.id, next) }
    } catch (cause) { if (active.current) setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { saving.current = false; if (active.current) setBusy(false) }
  }
  return <div data-feature-id={feature.id} className={rowClass}>
    <span className={iconClass}><Wrench size={18} aria-hidden="true" /></span>
    <div className="min-w-0 flex-1"><h3 className="truncate text-sm font-semibold text-content">{name}</h3>
      <p className="mt-1 line-clamp-2 text-sm text-content-secondary">{copy(feature.userSummary) || c('向 Stella 描述你的需求以使用此工具。', 'Describe your request to Stella to use this tool.')}</p>
      {error && <p role="alert" className="mt-1 text-xs text-danger">{error}</p>}
    </div>
    <Toggle label={name} checked={enabled} disabled={busy || !feature.toggleId} onChange={next => void change(next)} />
  </div>
}

export default function CapabilityFeatures({ item, onToolChanged, onDiscover, discovering }: { item: CapabilityDetail; onToolChanged: (id: string, enabled: boolean) => void; onDiscover: () => void; discovering: boolean }) {
  const location = useLocation()
  const groups: { type: string; title: string; countLabel: string; icon: LucideIcon }[] = [
    { type: 'tools', title: c('提供的工具', 'Tools'), countLabel: c('工具数量', 'Tool count'), icon: Wrench },
    { type: 'pages', title: c('页面入口', 'Pages'), countLabel: c('页面数量', 'Page count'), icon: LayoutPanelTop },
    { type: 'skills', title: c('技能', 'Skills'), countLabel: c('技能数量', 'Skill count'), icon: BookOpen },
    { type: 'mcp', title: c('MCP 连接', 'MCP connections'), countLabel: c('连接数量', 'Connection count'), icon: Plug },
    { type: 'other', title: c('其他功能', 'Other features'), countLabel: c('功能数量', 'Feature count'), icon: Code2 },
  ]
  const kind = (feature: CapabilityFeature) => feature.type || (item.kind === 'mcp' ? 'tools' : 'other')
  const visible = item.features.filter(feature => kind(feature) !== 'services')
  const pending = discovering || item.state === 'pending'
  const discoveryMessage = !item.enabled ? c('此连接已停用，启用后可发现工具。', 'This connection is disabled. Enable it to discover tools.')
    : pending ? c('正在连接并发现工具…', 'Connecting and discovering tools…')
    : item.state === 'failed' ? c('工具发现失败，请重试；连接配置和日志可帮助排查。', 'Tool discovery failed. Retry or check connection settings and logs.')
    : item.state === 'needs_auth' ? c('连接需要认证，请先在配置中完成连接。', 'Authentication is required. Complete the connection in Configuration.')
    : item.state === 'ready' ? c('服务未提供可用的公开工具。', 'The service has no public tools available.')
    : c('尚未发现工具，点击发现工具以连接服务。', 'Tools have not been discovered. Select Discover tools to connect.')
  return <div data-capability-features className="min-w-0 space-y-6">
    {item.kind === 'mcp' && !item.retained && <div className="flex flex-wrap items-center justify-between gap-3">
      <p role="status" className="text-sm text-content-secondary">{visible.length === 0 || pending || item.state !== 'ready' ? discoveryMessage : c(`已发现 ${visible.length} 个工具`, `${visible.length} tools discovered`)}</p>
      <button type="button" className="shrink-0 rounded-btn bg-surface-2 px-3 py-2 text-sm disabled:opacity-50" disabled={!item.enabled || pending} onClick={onDiscover}>{pending ? c('正在发现…', 'Discovering…') : item.state === 'failed' ? c('重试', 'Retry') : c('发现工具', 'Discover tools')}</button>
    </div>}
    {visible.length === 0 && item.kind !== 'mcp' && <div className="space-y-2 py-3"><p className="text-sm font-medium text-content">{c('暂无已登记的功能', 'No registered features')}</p><p className="text-sm text-content-secondary">{c('提供者未声明工具或页面入口。', 'This provider has not declared tools or page entries.')}</p></div>}
    {groups.map(({ type, title, countLabel, icon: Icon }) => {
      const features = visible.filter(feature => kind(feature) === type)
      if (features.length === 0) return null
      return <section key={type} data-feature-group={type} className="min-w-0 space-y-3" aria-label={title}>
        <header className="flex items-center gap-2.5"><Icon size={18} aria-hidden /><h2 className="text-sm font-semibold text-content">{title}</h2><span aria-label={countLabel} className="rounded-full bg-surface-2 px-2 py-0.5 text-xs font-medium text-content-secondary">{features.length}</span></header>
        <div className="min-w-0 divide-y divide-default overflow-hidden rounded-btn bg-surface-2/70">{features.map((feature, index) => {
          const binding = type === 'pages' ? pluginPageBinding(item.id, feature.pageId || feature.id) : undefined
          const rawName = binding ? t(binding.labelKey) : copy(feature.name)
          const name = rawName || feature.id || `${type === 'tools' ? c('工具', 'Tool') : type === 'pages' ? c('页面', 'Page') : title} ${index + 1}`
          if (type === 'tools') return <ToolRow key={`${type}/${feature.id}`} feature={feature} name={name} item={item} onSaved={onToolChanged} />
          const content = <><span className={iconClass}><Icon size={18} aria-hidden /></span><div className="min-w-0 flex-1"><h3 className="truncate text-sm font-semibold text-content">{name}</h3><p className="mt-1 truncate text-sm text-content-secondary" title={copy(feature.description)}>{copy(feature.description) || (type === 'pages' ? c('在插件原生页面中处理业务', 'Open the plugin’s own page') : c('由此提供者登记', 'Registered by this provider'))}</p></div><ChevronRight size={17} aria-hidden className="capability-feature-chevron shrink-0 text-content-secondary" /></>
          if (type === 'pages' && feature.available !== false) return <Link key={`${type}/${feature.id}`} aria-label={c(`打开${name}页面`, `Open ${name} page`)} className={`${rowClass} ${focusClass} hover:bg-surface-2`} to={binding?.path || `/plugins/${encodeURIComponent(item.id)}/pages/${encodeURIComponent(feature.pageId || feature.id)}`} state={currentSettingsOrigin(location.pathname, location.search)}>{content}</Link>
          return <details key={`${type}/${feature.id}`} className="group min-w-0"><summary className={`${rowClass} ${focusClass} list-none cursor-pointer [&::-webkit-details-marker]:hidden`}>{content}</summary><div className="space-y-2 border-t border-default px-4 py-4 text-xs text-content-secondary"><p className="break-all font-mono">{feature.id}</p>{type === 'pages' && feature.available === false && <p>{c('此页面暂不可用，请先启用提供者。', 'This page is unavailable. Enable the provider first.')}</p>}</div></details>
        })}</div>
      </section>
    })}
  </div>
}
