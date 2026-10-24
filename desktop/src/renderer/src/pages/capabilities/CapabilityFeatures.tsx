import { useEffect, useRef, useState } from 'react'
import { BookOpen, ChevronRight, Code2, LayoutPanelTop, Plug, Wrench } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
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

function Definition({ label, schema }: { label: string; schema?: Record<string, unknown> }) {
  return <div className="min-w-0 space-y-2"><h4 className="text-xs font-semibold text-content">{label}</h4>
    {schema ? <pre aria-label={`${label} JSON`} className="max-h-72 min-w-0 overflow-auto rounded-btn bg-surface px-3 py-3 text-xs leading-relaxed text-content-secondary">{JSON.stringify(schema, null, 2)}</pre>
      : <p className="text-xs text-content-secondary">{c('提供者未发布参数结构。', 'The provider has not published a parameter schema.')}</p>}</div>
}

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

function TechnicalTool({ feature }: { feature: CapabilityFeature }) {
  const descriptions = [...new Set([copy(feature.description), copy(feature.detailDescription)].filter(Boolean))]
  return <section data-technical-tool-id={feature.id} className="min-w-0 space-y-4 border-t border-default pt-4">
    <h3 className="text-sm font-semibold">{copy(feature.name) || feature.id}</h3>
    <div className="text-xs"><p className="text-content-secondary">{c('工具 ID', 'Tool ID')}</p><p className="mt-1 break-all font-mono">{feature.id}</p></div>
    {descriptions.length > 0 && <div className="space-y-2"><h4 className="text-xs font-semibold">{c('原始描述', 'Original description')}</h4><div className="max-h-60 overflow-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-content-secondary">{descriptions.join('\n\n')}</div></div>}
    <Definition label={c('输入参数', 'Input parameters')} schema={feature.inputSchema} />
    {feature.outputSchema && <Definition label={c('返回结构', 'Output schema')} schema={feature.outputSchema} />}
  </section>
}

export default function CapabilityFeatures({ item, onToolChanged }: { item: CapabilityDetail; onToolChanged: (id: string, enabled: boolean) => void }) {
  const [technicalExpanded, setTechnicalExpanded] = useState(false)
  const location = useLocation()
  const navigate = useNavigate()
  const groups: { type: string; title: string; countLabel: string; icon: LucideIcon }[] = [
    { type: 'tools', title: c('提供的工具', 'Tools'), countLabel: c('工具数量', 'Tool count'), icon: Wrench },
    { type: 'pages', title: c('页面入口', 'Pages'), countLabel: c('页面数量', 'Page count'), icon: LayoutPanelTop },
    { type: 'skills', title: c('技能', 'Skills'), countLabel: c('技能数量', 'Skill count'), icon: BookOpen },
    { type: 'mcp', title: c('MCP 连接', 'MCP connections'), countLabel: c('连接数量', 'Connection count'), icon: Plug },
    { type: 'other', title: c('其他功能', 'Other features'), countLabel: c('功能数量', 'Feature count'), icon: Code2 },
  ]
  const kind = (feature: CapabilityFeature) => feature.type || (item.kind === 'mcp' ? 'tools' : 'other')
  const visible = item.features.filter(feature => kind(feature) !== 'services')
  const services = [...new Set([...item.features.filter(feature => kind(feature) === 'services').map(feature => feature.id), ...(item.technical?.services || [])])]
  const openRuntime = () => {
    const params = new URLSearchParams(location.search); params.set('section', 'runtime')
    navigate(`${location.pathname}?${params}`, { replace: true, state: location.state })
  }
  return <div data-capability-features className="min-w-0 space-y-6">
    {visible.length === 0 && <div className="space-y-2 py-3"><p className="text-sm font-medium text-content">{item.kind === 'mcp' ? c('尚未发现工具', 'No tools discovered') : c('暂无已登记的功能', 'No registered features')}</p>
      {item.kind === 'mcp' && !item.tabs.includes('runtime') ? <p className="text-sm text-content-secondary">{c('连接后将显示服务发现的工具。', 'Discovered tools will appear after connection.')}</p> : item.kind === 'mcp' ? <p className="text-sm leading-relaxed text-content-secondary">{c('工具列表由服务发现结果提供。可在', 'Tools are listed after discovery. Check the ')}<button type="button" onClick={openRuntime} className="rounded-sm text-accent underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-accent">{c('运行', 'Runtime')}</button>{c('选项卡检查连接。', ' tab for connection information.')}</p>
        : <p className="text-sm text-content-secondary">{services.length > 0 ? c('此提供者仅登记了服务接口，可在技术信息中查看。', 'This provider registers service interfaces only. See Technical information.') : c('提供者未声明工具或页面入口。', 'This provider has not declared tools or page entries.')}</p>}</div>}
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
    <details className="group border-t border-default pt-4" onToggle={event => setTechnicalExpanded(event.currentTarget.open)}><summary className={`${focusClass} flex cursor-pointer list-none items-center gap-2 py-1 [&::-webkit-details-marker]:hidden`}><Code2 size={18} aria-hidden /><span className="flex-1 text-sm font-semibold text-content">{c('技术信息', 'Technical information')}</span><ChevronRight size={16} aria-hidden className="text-content-secondary group-open:rotate-90" /></summary>
      <dl className="mt-4 space-y-3 text-xs"><div><dt className="text-content-secondary">{c('提供者 ID', 'Provider ID')}</dt><dd className="mt-1 break-all font-mono text-content">{item.id}</dd></div>
        {item.version && <div><dt className="text-content-secondary">{c('插件版本', 'Plugin version')}</dt><dd className="mt-1 font-mono text-content">{item.version}</dd></div>}
        {item.technical?.transport && <div><dt className="text-content-secondary">{c('连接方式', 'Transport')}</dt><dd className="mt-1 font-mono text-content">{item.technical.transport}</dd></div>}
        {item.technical?.generation !== undefined && <div><dt className="text-content-secondary">{c('注册代次', 'Registration generation')}</dt><dd className="mt-1 font-mono text-content">{item.technical.generation}</dd></div>}
        {services.length > 0 && <div><dt className="text-content-secondary">{c('已注册服务', 'Registered services')}</dt><dd className="mt-1 space-y-1 text-content">{services.map(service => <div key={service} className="break-all font-mono">{service}</div>)}</dd></div>}</dl>
    {technicalExpanded && <div className="mt-5 space-y-5">{visible.filter(feature => kind(feature) === 'tools').map(feature => <TechnicalTool key={feature.id} feature={feature} />)}</div>}
    </details>
  </div>
}
