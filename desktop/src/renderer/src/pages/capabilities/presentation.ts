import { BookOpen, Plug, Puzzle, Wrench } from 'lucide-react'
import { getLang } from '../../i18n'
import type { CapabilityCopy, CapabilityItem, CapabilityKind } from '../../api/capabilityTypes'
export const c = (zh: string, en: string) => getLang() === 'zh' ? zh : en
export const copy = (value: CapabilityCopy | null | undefined) => {
  if (typeof value === 'string') return value.trim()
  const preferred = value?.[getLang() === 'en' ? 'en' : 'zh']
  return preferred?.trim() || value?.zh?.trim() || value?.en?.trim() || ''
}
export const kindLabel = (kind: CapabilityKind) => ({ plugins: c('插件', 'Plugins'), mcp: 'MCP', skills: 'Skill', tools: c('内置工具', 'Built-in tools') })[kind]
export const kindIcon = { plugins: Puzzle, mcp: Plug, skills: BookOpen, tools: Wrench }
export const detailPath = (kind: CapabilityKind, id: string, section?: string) => `/skills/${kind}/${encodeURIComponent(id)}${section ? `?section=${section}` : ''}`
export const stateLabel = (state: string) => ({
  enabled: c('已启用', 'Enabled'), available: c('可用', 'Available'), ready: c('已连接', 'Connected'),
  active: c('运行中', 'Running'), stopped: c('已停止', 'Stopped'), not_started: c('尚未连接', 'Not connected'),
  disabled: c('已停用', 'Disabled'), removed: c('已卸载', 'Removed'), failed: c('故障', 'Failed'),
  unavailable: c('暂不可用', 'Unavailable'), maintenance: c('维护中', 'Maintenance'),
  pending: c('连接中', 'Connecting'), needs_auth: c('需要认证', 'Authentication required'),
}[state] || state)
export const sourceLabel = (source: string) => ({ builtin: c('Stella 内置', 'Built into Stella'), installed: c('已安装', 'Installed'),
  retained: c('保留的连接设置', 'Retained connection settings'), custom: c('自定义', 'Custom'), plugin: c('插件提供', 'Provided by a plugin'), stdio: c('本地连接', 'Local connection'),
  sse: c('远程连接', 'Remote connection'), 'streamable-http': c('远程连接', 'Remote connection') }[source] || source)
export const panel = 'rounded-card border border-default bg-surface'
export const button = 'inline-flex items-center justify-center gap-2 rounded-btn px-4 py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50'

/** Registry lifecycle and connection readiness do not describe task execution. */
export const providerStateLabel = (kind: CapabilityKind, state: string) => {
  if (kind === 'plugins') return ({
    active: c('已加载', 'Loaded'), stopped: c('未加载', 'Not loaded'),
    validating: c('校验中', 'Validating'), starting: c('加载中', 'Loading'),
    draining: c('卸载中', 'Unloading'), degraded: c('加载异常', 'Degraded'),
    blocked: c('加载受阻', 'Blocked'),
  }[state] || stateLabel(state))
  return stateLabel(state)
}

/** User availability summary, separate from provider lifecycle and task execution. */
export const capabilityStatusLabel = (item: CapabilityItem) => {
  if (item.kind !== 'plugins' && item.kind !== 'mcp') return stateLabel(item.state)
  if (item.state === 'removed') return c('已卸载', 'Removed')
  if (!item.enabled) return c('已停用', 'Disabled')
  if (['failed', 'degraded', 'unavailable'].includes(item.state)) return c('运行异常', 'Problem detected')
  if (item.state === 'blocked') return c('暂不可用', 'Unavailable')
  if (item.state === 'maintenance') return c('维护中', 'Maintenance')
  if (item.state === 'draining') return c('处理中', 'Updating')
  if (item.kind === 'mcp') {
    if (item.state === 'needs_auth') return c('需要授权', 'Authorization required')
    if (item.state === 'pending') return c('连接中', 'Connecting')
    if (item.state === 'not_started') return c('待连接', 'Awaiting connection')
    if (item.state !== 'ready') return c('状态未知', 'Status unknown')
  } else if (!['stopped', 'active', 'starting', 'validating', 'enabled', 'available'].includes(item.state)) {
    return c('状态未知', 'Status unknown')
  }
  return c('已启用', 'Enabled')
}
