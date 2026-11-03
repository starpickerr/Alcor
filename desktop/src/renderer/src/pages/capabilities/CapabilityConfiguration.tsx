import { ConversationMcpAccess } from '../../components/FeatureAccessEditor'
import { useUIStore } from '../../store/uiStore'
import type { ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import type { CapabilityDetail } from '../../api/capabilityTypes'
import PluginSettings from '../settings/PluginSettings'
import { ElearningSettings } from '../data/ElearningSettings'
import { MailboxSettings } from '../data/MailboxSettings'
import ElearningFileSettings from '../settings/ElearningFileSettings'
import AppleIntegrations from '../settings/AppleIntegrations'
import { c, stateLabel } from './presentation'
import McpOAuthSettings from './McpOAuthSettings'
import McpCredentialSettings from './McpCredentialSettings'
import { McpRuntimeSettings } from '../../components/SecurityAuthorizationPanels'

export default function CapabilityConfiguration({ item, baseUrl }: { item: CapabilityDetail; baseUrl: string }) {
  const [params] = useSearchParams()
  const session = useUIStore(state => state.activeSessionId)
  const settingsUrl = item.configuration?.settingsUrl
  const oauth = item.kind === 'mcp' && (item.source === 'streamable-http' || item.retained)
  const mcpSettings: Record<string, ReactNode> = {
    '/elearning': <><ElearningSettings /><ElearningFileSettings /></>,
    '/email': <MailboxSettings />,
  }
  return <div className="space-y-6">
    {item.kind === 'mcp' && !item.retained && <>
      <p role="status" className="text-sm text-content-secondary">{c('连接状态：', 'Connection: ')}{stateLabel(item.state)}</p>
      <p className="text-sm text-content-secondary">{c('启用连接后，需为当前对话选择资源授权；启用状态不代表 Agent 已获使用权限。', 'Enabling a connection also requires conversation resource authorization.')}</p>
      {session ? <ConversationMcpAccess key={`${session}:${item.id}`} session={session} only={item.id} /> : <Link className="text-sm text-accent" to="/chat">{c('打开对话后选择此 MCP', 'Open a conversation to select this MCP')}</Link>}
    </>}
    {item.kind === 'plugins' && <>
      <PluginSettings key={item.id} baseUrl={baseUrl} pluginId={item.id} embedded />
      {(item.id === 'stella.calendar' || item.id === 'stella.tasks') && <section id="apple-integration" className="space-y-4">
        <h2 className="text-base font-semibold">{item.id === 'stella.calendar' ? c('Apple 日历', 'Apple Calendar') : c('Apple 提醒事项', 'Apple Reminders')}</h2>
        <AppleIntegrations section={params.get('integration') === 'apple-reminders' || item.id === 'stella.tasks' ? 'apple-reminders' : 'apple-calendar'} kind={item.id === 'stella.calendar' ? 'calendar' : 'todos'} />
      </section>}
    </>}
    {oauth && <McpOAuthSettings key={`oauth:${item.id}`} name={item.id} />}
    {item.kind === 'mcp' && !settingsUrl && !item.retained && <><McpCredentialSettings key={`credentials:${item.id}`} name={item.id} /><McpRuntimeSettings key={`runtime:${item.id}`} name={item.id} /></>}
    {item.kind === 'mcp' && (settingsUrl && mcpSettings[settingsUrl] ? mcpSettings[settingsUrl] :
      settingsUrl?.startsWith('/') && !settingsUrl.startsWith('//') && !settingsUrl.startsWith('/skills') ?
        <Link to={settingsUrl} className="text-sm text-accent">{c('打开提供者配置', 'Open provider settings')}</Link> :
        !oauth && item.retained && <p className="text-sm text-content-secondary">{c('此提供者未发布可编辑的配置项。', 'This provider has not published editable settings.')}</p>)}
  </div>
}
