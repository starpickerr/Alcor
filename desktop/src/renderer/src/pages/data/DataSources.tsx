import { ChevronRight } from 'lucide-react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { currentSettingsOrigin } from '../settings/origin'
import { SourceIcon } from './DataUsage'
import { c, groupSettingsPath, panel, sourceDescription, sourceOrigin, sourceStatus, type SourceGroup } from './presentation'

export default function DataSources({ groups }: { groups: SourceGroup[] }) {
  const location = useLocation()
  const navigate = useNavigate()
  return <section className="space-y-5">
    <div><h2 className="text-lg font-semibold">{c('信息源', 'Sources')}</h2>
      <p className="mt-1 text-sm leading-relaxed text-content-secondary">{c('查看 Stella 已接入的信息源，点击卡片管理账号与配置。', 'See connected sources. Select a card to manage accounts and settings.')}</p>
    </div>
    {!groups.length && <p className={`${panel} p-5 text-sm text-content-secondary`}>{c('尚无已注册的信息源。请先在能力中连接服务。', 'No sources are registered. Connect a service in Capabilities first.')}</p>}
    <div className="grid gap-4 sm:grid-cols-2">{groups.map(group => {
      const path = groupSettingsPath(group)
      const content = <>
        <div className="flex items-center gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-btn bg-accent-soft"><SourceIcon source={group.source} size={21} /></span>
          <h3 className="min-w-0 flex-1 break-words text-base font-semibold text-content">{group.name}</h3>
          {path && <ChevronRight size={17} aria-hidden="true" className="shrink-0 text-content-tertiary group-hover:text-accent" />}
        </div>
        <p className="mt-4 text-sm leading-relaxed text-content-secondary">{sourceDescription(group.source)}</p>
        <div className="mt-5 flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-t border-default pt-3 text-xs text-content-secondary">
          <span>{group.source.provider === 'email' ? c(`${group.accounts} 个邮箱`, `${group.accounts} ${group.accounts === 1 ? 'mailbox' : 'mailboxes'}`) : sourceOrigin(group.source)}</span>
          <span className="inline-flex items-center gap-1.5"><span aria-hidden="true" className={`size-1.5 shrink-0 rounded-full ${group.status === 'available' ? 'bg-accent' : group.status === 'partial' ? 'bg-amber-500' : 'bg-content-tertiary'}`} />{sourceStatus(group)}</span>
        </div>
        {!path && <p className="mt-3 text-xs text-content-secondary">{c('未关联可管理的能力，暂不可进入配置。', 'No manageable capability is associated with this source.')}</p>}
      </>
      const style = `${panel} group block min-w-0 p-5 sm:p-6 text-content shadow-sm`
      return path ? <Link key={group.id} data-testid={`data-source-${group.id}`} to={path} onClick={event => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
        event.preventDefault()
        navigate(path, { state: currentSettingsOrigin(location.pathname, location.search) })
      }} className={`${style} hover:border-strong hover:bg-surface-2 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent`}>{content}</Link>
        : <article key={group.id} data-testid={`data-source-${group.id}`} className={style} aria-disabled="true">{content}</article>
    })}</div>
    <Link className="inline-flex items-center gap-1 text-sm text-accent hover:underline" to="/skills?tab=mcp">{c('管理来源连接', 'Manage connections')}<ChevronRight size={15} aria-hidden="true" /></Link>
  </section>
}
