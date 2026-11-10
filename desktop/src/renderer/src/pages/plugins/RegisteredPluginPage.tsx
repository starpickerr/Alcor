import { useEffect, useState } from 'react'
import { ArrowLeft } from 'lucide-react'
import { Link, useLocation, useParams } from 'react-router-dom'
import apiClient from '../../api/client'
import { c, copy } from '../capabilities/presentation'
import PluginPageHost from './PluginPageHost'

/** Route adapter for declared plugin pages without a built-in host navigation entry. */
export default function RegisteredPluginPage({ baseUrl }: { baseUrl: string }) {
  const { pluginId = '', pageId = '' } = useParams()
  const location = useLocation()
  const [result, setResult] = useState<{ pluginId: string; pageId: string; title: string; error: string } | null>(null)
  const current = result?.pluginId === pluginId && result.pageId === pageId ? result : null
  useEffect(() => {
    const controller = new AbortController()
    setResult(null)
    apiClient.setBaseUrl(baseUrl)
    void apiClient.getCapabilityDetail('plugins', pluginId, controller.signal).then(provider => {
      if (controller.signal.aborted) return
      const feature = provider.features.find(item => item.type === 'pages' && (item.pageId || item.id) === pageId)
      if (!feature || feature.available === false) throw new Error(c('此插件页面未登记或暂不可用。', 'This plugin page is not registered or is unavailable.'))
      setResult({ pluginId, pageId, title: copy(feature.name) || copy(provider.name), error: '' })
    }).catch(cause => { if (!controller.signal.aborted) setResult({ pluginId, pageId, title: '', error: cause instanceof Error ? cause.message : String(cause) }) })
    return () => controller.abort()
  }, [baseUrl, pluginId, pageId])
  const from = (location.state as { from?: string } | null)?.from
  const back = from?.startsWith('/') && !from.startsWith('//') ? from : `/skills/plugins/${encodeURIComponent(pluginId)}?section=features`
  return <div className="flex min-h-0 min-w-0 flex-1 flex-col">
    <div className="border-b border-default px-6 py-3"><Link to={back} className="inline-flex items-center gap-2 text-sm text-accent"><ArrowLeft size={16} />{c('返回能力详情', 'Back to capability')}</Link></div>
    {current?.error ? <p role="alert" className="p-6 text-sm text-danger">{current.error}</p> : current?.title ? <PluginPageHost key={`${pluginId}/${pageId}`} baseUrl={baseUrl} pluginId={pluginId} pageId={pageId} title={current.title} /> : <p role="status" className="p-6 text-sm text-content-secondary">{c('正在读取页面…', 'Loading page…')}</p>}
  </div>
}
