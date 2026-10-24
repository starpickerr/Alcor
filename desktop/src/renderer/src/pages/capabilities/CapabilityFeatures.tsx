import { useState } from 'react'
import { BookOpen, ChevronRight, Code2, LayoutPanelTop, Plug, Wrench } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import type { CapabilityDetail, CapabilityFeature } from '../../api/capabilityTypes'
import { t } from '../../i18n'
import { pluginPageBinding } from '../../layout/navigation'
import { currentSettingsOrigin } from '../settings/origin'
import { c, copy } from './presentation'
import './capabilities.css'

const rowClass = 'capability-feature-row flex min-w-0 items-center gap-3 px-4 py-3.5 sm:gap-4'
const iconClass = 'capability-feature-icon flex size-9 shrink-0 items-center justify-center rounded-btn bg-surface text-content-secondary'
const focusClass = 'focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent'

function Definition({ label, schema }: { label: string; schema?: Record<string, unknown> }) {
  return <div className="min-w-0 space-y-2"><h4 className="text-xs font-semibold text-content">{label}</h4>
    {schema ? <pre aria-label={`${label} JSON`} className="max-h-72 min-w-0 overflow-auto rounded-btn bg-surface px-3 py-3 text-xs leading-relaxed text-content-secondary">{JSON.stringify(schema, null, 2)}</pre>
      : <p className="text-xs text-content-secondary">{c('提供者未发布参数结构。', 'The provider has not published a parameter schema.')}</p>}</div>
}

function ToolRow({ feature, name, provider }: { feature: CapabilityFeature; name: string; provider: CapabilityDetail }) {
  const [expanded, setExpanded] = useState(false)
  const description = copy(feature.description)
  return <details data-feature-id={feature.id} className="group min-w-0" onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary className={`${rowClass} ${focusClass} cursor-pointer list-none [&::-webkit-details-marker]:hidden hover:bg-surface-2`}>
      <span className={iconClass}><Wrench size={18} aria-hidden="true" /></span>
      <div className="min-w-0 flex-1"><h3 className="truncate text-sm font-semibold text-content">{name}</h3>
        <p className="mt-1 truncate text-sm text-content-secondary" title={description}>{description || c('查看工具定义与参数', 'View the tool definition and parameters')}</p></div>
      <ChevronRight size={17} aria-hidden="true" className="capability-feature-chevron shrink-0 text-content-secondary transition-transform group-open:rotate-90 motion-reduce:transition-none" />
    </summary>
    {expanded && <div className="min-w-0 space-y-5 border-t border-default px-4 py-4 sm:pl-17">
      <dl className="grid min-w-0 gap-3 text-xs sm:grid-cols-2"><div><dt className="text-content-secondary">{c('真实工具名称', 'Registered tool name')}</dt><dd className="mt-1 break-all font-mono text-content">{feature.id}</dd></div>
        <div><dt className="text-content-secondary">{c('来源', 'Source')}</dt><dd className="mt-1 break-words text-content">{feature.source === 'mcp' || provider.kind === 'mcp' ? 'MCP' : c('插件', 'Plugin')} · {copy(provider.name)}</dd></div></dl>
      <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-content-secondary">{copy(feature.detailDescription) || description || c('提供者未发布详细说明。', 'The provider has not published a detailed description.')}</p>
      <Definition label={c('输入参数', 'Input parameters')} schema={feature.inputSchema} />
      {feature.outputSchema && <Definition label={c('返回结构', 'Output schema')} schema={feature.outputSchema} />}
    </div>}
  </details>
}

export default function CapabilityFeatures({ item }: { item: CapabilityDetail }) {
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
  const services = item.features.filter(feature => kind(feature) === 'services')
  const openRuntime = () => {
    const params = new URLSearchParams(location.search); params.set('section', 'runtime')
    navigate(`${location.pathname}?${params}`, { replace: true, state: location.state })
  }
  return <div data-capability-features className="min-w-0 space-y-6">
    {visible.length === 0 && <div className="space-y-2 py-3"><p className="text-sm font-medium text-content">{item.kind === 'mcp' ? c('尚未发现工具', 'No tools discovered') : c('暂无已登记的功能', 'No registered features')}</p>
      {item.kind === 'mcp' ? <p className="text-sm leading-relaxed text-content-secondary">{c('工具列表由服务发现结果提供。可在', 'Tools are listed after discovery. Check the ')}<button type="button" onClick={openRuntime} className="rounded-sm text-accent underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-accent">{c('运行', 'Runtime')}</button>{c('选项卡检查连接。', ' tab for connection information.')}</p>
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
          if (type === 'tools') return <ToolRow key={`${type}/${feature.id}`} feature={feature} name={name} provider={item} />
          const content = <><span className={iconClass}><Icon size={18} aria-hidden /></span><div className="min-w-0 flex-1"><h3 className="truncate text-sm font-semibold text-content">{name}</h3><p className="mt-1 truncate text-sm text-content-secondary" title={copy(feature.description)}>{copy(feature.description) || (type === 'pages' ? c('在插件原生页面中处理业务', 'Open the plugin’s own page') : c('由此提供者登记', 'Registered by this provider'))}</p></div><ChevronRight size={17} aria-hidden className="capability-feature-chevron shrink-0 text-content-secondary" /></>
          if (type === 'pages' && feature.available !== false) return <Link key={`${type}/${feature.id}`} aria-label={c(`打开${name}页面`, `Open ${name} page`)} className={`${rowClass} ${focusClass} hover:bg-surface-2`} to={binding?.path || `/plugins/${encodeURIComponent(item.id)}/pages/${encodeURIComponent(feature.pageId || feature.id)}`} state={currentSettingsOrigin(location.pathname, location.search)}>{content}</Link>
          return <details key={`${type}/${feature.id}`} className="group min-w-0"><summary className={`${rowClass} ${focusClass} list-none cursor-pointer [&::-webkit-details-marker]:hidden`}>{content}</summary><div className="space-y-2 border-t border-default px-4 py-4 text-xs text-content-secondary"><p className="break-all font-mono">{feature.id}</p>{type === 'pages' && feature.available === false && <p>{c('此页面暂不可用，请先启用提供者。', 'This page is unavailable. Enable the provider first.')}</p>}</div></details>
        })}</div>
      </section>
    })}
    <details className="group border-t border-default pt-4"><summary className={`${focusClass} flex cursor-pointer list-none items-center gap-2 py-1 [&::-webkit-details-marker]:hidden`}><Code2 size={18} aria-hidden /><span className="flex-1 text-sm font-semibold text-content">{c('技术信息', 'Technical information')}</span><ChevronRight size={16} aria-hidden className="text-content-secondary group-open:rotate-90" /></summary>
      <dl className="mt-4 space-y-3 text-xs"><div><dt className="text-content-secondary">{c('提供者 ID', 'Provider ID')}</dt><dd className="mt-1 break-all font-mono text-content">{item.id}</dd></div>
        {services.length > 0 && <div><dt className="text-content-secondary">{c('已注册服务', 'Registered services')}</dt><dd className="mt-1 space-y-1 text-content">{services.map(service => <div key={service.id} className="break-all font-mono">{service.id}</div>)}</dd></div>}</dl>
    </details>
  </div>
}
