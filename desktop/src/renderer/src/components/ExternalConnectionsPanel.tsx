import React from 'react'
import { Link } from 'react-router-dom'
import apiClient from '../api/client'
import { getLang } from '../i18n'
import { useSettingsLink, useRestoreScroll } from '../pages/settings/origin'
import { Feedback, useSecurityData } from './SecurityAuthorizationPanels'
import { Toggle } from '../pages/settings/primitives'

const load = () => apiClient.externalConnections()
export default function ExternalConnectionsPanel() {
  const en = getLang() === 'en'
  const openSettings = useSettingsLink()
  const state = useSecurityData(load)
  useRestoreScroll(!!state.data)
  const title = (name: string) => ({ 'Public web': en ? 'Public web' : '公共网页', 'Package registries': en ? 'Package registries' : '依赖包仓库' }[name] || name)
  return <section className="min-w-0 rounded-card border border-default bg-surface p-5 text-content sm:p-6">
    <h2 className="text-base font-semibold text-content">{en ? 'External connections' : '外部连接'}</h2>
    <p className="my-3 text-sm leading-6 text-content-secondary">{en ? 'Connection switches are independent of capability and tool switches. Closing a connection revokes current requests and blocks new ones; accounts and business data are preserved.' : '连接开关独立于能力和工具开关。关闭后撤销当前请求并拒绝新请求，账号和业务数据保留。'}</p>
    <Feedback {...state} />
    {state.data?.length === 0 && <p className="text-sm text-content-secondary">{en ? 'No configured external services.' : '暂无已配置的外部服务。'}</p>}
    {state.data?.map(row => <div key={row.id} className="flex flex-wrap items-center justify-between gap-4 border-t border-default py-4">
      <div className="min-w-0 flex-1"><p className="break-all text-sm font-semibold">{title(row.name)}</p>
        {row.endpoint && <p className="mt-1 break-all text-xs text-content-secondary">{row.endpoint}</p>}
        <p className="mt-1 break-all text-xs text-content-secondary">{row.owners.join(' · ')} · {row.enabled ? (en ? 'Enabled' : '已开启') : (en ? 'Closed' : '已关闭')}{row.active_requests > 0 ? ` · ${en ? 'Requests' : '请求'} ${row.active_requests}` : ''}</p>
        {row.configure_url && <Link className="mt-2 inline-flex min-h-8 items-center text-xs text-accent" to={row.configure_url} onClick={event => openSettings(event, row.configure_url!)}>{en ? 'Open owner configuration' : '前往所属功能配置'}</Link>}
      </div>
      <span aria-label={`${en ? 'Connection' : '连接'} ${title(row.name)}`}><Toggle label={`${en ? 'Connection' : '连接'} ${title(row.name)}`} checked={row.enabled} disabled={state.busy} onChange={enabled => { void state.run(async () => { await apiClient.setExternalConnection(row.id, enabled, row.revision); return load() }, en ? 'Connection state saved.' : '连接状态已保存。') }} /></span>
    </div>)}
  </section>
}
