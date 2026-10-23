import { Link, useSearchParams } from 'react-router-dom'
import type { CapabilityDetail } from '../../api/capabilityTypes'
import PluginSettings from '../settings/PluginSettings'
import { MailboxSettings } from '../data/MailboxSettings'
import { ElearningSettings } from '../data/ElearningSettings'
import ElearningFileSettings from '../settings/ElearningFileSettings'
import AppleIntegrations from '../settings/AppleIntegrations'
import { c } from './presentation'

export default function CapabilityConfiguration({ item, baseUrl }: { item: CapabilityDetail; baseUrl: string }) {
  const [params] = useSearchParams()
  return <div className="space-y-6">
    {item.kind === 'plugins' && <>
      <PluginSettings key={item.id} baseUrl={baseUrl} pluginId={item.id} embedded />
      {(item.id === 'stella.calendar' || item.id === 'stella.tasks') && <section id="apple-integration" className="space-y-4">
        <h2 className="text-base font-semibold">{item.id === 'stella.calendar' ? c('Apple 日历', 'Apple Calendar') : c('Apple 提醒事项', 'Apple Reminders')}</h2>
        <AppleIntegrations section={params.get('integration') === 'apple-reminders' || item.id === 'stella.tasks' ? 'apple-reminders' : 'apple-calendar'} kind={item.id === 'stella.calendar' ? 'calendar' : 'todos'} />
      </section>}
    </>}
    {item.kind === 'mcp' && item.id === 'email' && <MailboxSettings />}
    {item.kind === 'mcp' && item.id === 'elearning' && <><ElearningSettings /><ElearningFileSettings /></>}
    <Link className="inline-flex text-sm text-accent hover:underline" to="/security?tab=connections">{c('管理账号与资源访问授权', 'Manage account and resource access')}</Link>
  </div>
}
