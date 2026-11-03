import React, { useCallback, useEffect, useRef, useState, useId } from 'react'
import { RefreshCw } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import apiClient from '../api/client'
import type { FileMode, PermissionMode, AuthorizationRule, RunPolicy, RunPolicyState } from '../api/client'
import { getLang } from '../i18n'

import { permissionInput } from './permissionStyles'
export { permissionInput } from './permissionStyles'
import FeatureAccessEditor from './FeatureAccessEditor'
import { ruleGroups, securityCopy } from '../pages/securityLocale'
const saveButton = 'min-h-10 rounded-btn bg-accent px-4 py-2 text-sm font-medium text-accent-contrast hover:bg-accent-hover disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent'
const modes: { id: PermissionMode; zh: string; en: string; zhHelp: string; enHelp: string }[] = [
  { id: 'read-only', zh: '只读', en: 'Read Only', zhHelp: '查看与检索信息，不修改数据或执行命令。', enHelp: 'View and search information, without changing data or running commands.' },
  { id: 'workspace-write', zh: '工作区写入', en: 'Workspace Write', zhHelp: '更新业务数据，读写工作区并运行受限命令。删除、外发和外部执行需确认。', enHelp: 'Update data, write workspace files and run confined commands. Deletion, sending and external execution need confirmation.' },
  { id: 'full-access', zh: '完全访问', en: 'Full Access', zhHelp: '直接执行普通操作，放宽文件与命令限制。仍遵守账号资源范围及系统保护。', enHelp: 'Run ordinary operations directly, with broader file and command access. Account scopes and system protections still apply.' },
  { id: 'custom', zh: '自定义', en: 'Custom', zhHelp: '使用独立规则，具体设置在安全中心的 Agent 权限中管理。', enHelp: 'Use independent rules, edited in Security → Agent permissions.' },
]

export function PolicyFields({ value, onChange, disabled = false }: {
  value: RunPolicy; onChange: (next: RunPolicy) => void; disabled?: boolean
}) {
  const en = getLang() === 'en'
  const group = useId()
  return <fieldset disabled={disabled} className="min-w-0" role="radiogroup" aria-label={en ? 'Permission mode' : '权限模式'}>
    <div className="grid gap-3 md:grid-cols-2">
      {modes.map(mode => {
        const selected = (value.permission_mode || value.filesystem_mode) === mode.id
        return <label key={mode.id} className={`relative flex min-w-0 cursor-pointer flex-col gap-3 rounded-card border p-4 transition-colors focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-accent ${selected ? 'border-accent bg-accent-soft' : 'border-default bg-surface hover:border-strong'} ${disabled ? 'cursor-wait opacity-60' : ''}`}>
          <span className="flex items-center justify-between gap-3">
            <span className="text-sm font-semibold">{en ? mode.en : mode.zh}</span>
            <input type="radio" name={group} value={mode.id} aria-label={en ? mode.en : mode.zh}
              checked={selected} onChange={() => {
                const { rules: _rules, ...base } = value
                onChange(mode.id === 'custom' ? { ...value, permission_mode: 'custom', rules: value.rules || {} }
                  : { ...base, filesystem_mode: mode.id, permission_mode: mode.id })
              }}
              className="size-4 shrink-0 accent-accent" />
          </span>
          <span className="text-xs leading-6 text-content-secondary">{en ? mode.enHelp : mode.zhHelp}</span>
        </label>
      })}
    </div>
  </fieldset>
}

export default function RunPolicyEditor({ kind, subject, agent, onSaved, detailed = false }: {
  kind: 'template' | 'session' | 'task'; subject: string; agent?: string; detailed?: boolean; onSaved?: (policy: RunPolicy) => void
}) {
  const en = getLang() === 'en'
  const copy = securityCopy(getLang())
  const location = useLocation()
  const editorLink = `/security?${new URLSearchParams({ tab: 'agent-permissions', kind, subject, ...(agent ? { agent } : {}), returnTo: location.pathname + location.search })}`
  const [state, setState] = useState<RunPolicyState | null>(null)
  const [connectionOpen, setConnectionOpen] = useState(false)
  const [draft, setDraft] = useState<RunPolicy | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const generation = useRef(0)
  const load = useCallback(async () => {
    const token = ++generation.current
    setBusy(true); setError(''); setMessage('')
    try {
      const result = await apiClient.getRunPolicy(kind, subject, agent)
      if (token === generation.current) { setState(result); setDraft(result.policy) }
    } catch (cause) { if (token === generation.current) setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { if (token === generation.current) setBusy(false) }
  }, [kind, subject, agent])
  useEffect(() => { setState(null); setDraft(null); void load(); return () => { generation.current++ } }, [load])
  const save = async () => {
    if (!draft || busy) return
    const token = generation.current
    setBusy(true); setError(''); setMessage('')
    try {
      const result = await apiClient.saveRunPolicy(kind, subject, { filesystem_mode: draft.filesystem_mode,
        permission_mode: draft.permission_mode || draft.filesystem_mode,
        ...(draft.permission_mode === 'custom' ? { rules: draft.rules || {} } : {}), policy_revision: draft.policy_revision }, agent)
      if (token !== generation.current) return
      setState(result); setDraft(result.policy); setMessage(en ? 'Permissions saved.' : '权限已保存。'); onSaved?.(result.policy)
    } catch (cause) { if (token === generation.current) setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { if (token === generation.current) setBusy(false) }
  }
  return <div className="min-w-0 space-y-4 text-content">
    {error && <p role="alert" className="rounded-btn border border-danger/30 p-3 text-sm text-danger">{error}</p>}
    {message && <p role="status" className="text-sm text-accent">{message}</p>}
    {!draft && !error && <p role="status" className="text-sm text-content-secondary">{en ? 'Loading permissions…' : '正在读取权限…'}</p>}
    {state?.migration_status === 'needs_confirmation' && <p className="rounded-btn bg-surface-2 p-3 text-sm">{en ? 'Existing authorization needs confirmation. Access stays restricted until you save a verified policy.' : '旧授权需要确认。在核对并保存前，将保持限制状态。'}</p>}
    {draft && state && <PolicyFields value={draft} onChange={value => { setDraft(value); setMessage('') }} disabled={busy} />}
    {draft?.permission_mode === 'custom' && (detailed ? <div className="space-y-5">
      <label className="block text-sm">{en ? 'Base mode' : '基准模式'}<select aria-label={en ? 'Base mode' : '基准模式'} className={`${permissionInput} mt-2 w-full`} disabled={busy}
        value={draft.filesystem_mode} onChange={event => setDraft({ ...draft, filesystem_mode: event.target.value as FileMode })}>
        {modes.filter(mode => mode.id !== 'custom').map(mode => <option key={mode.id} value={mode.id}>{en ? mode.en : mode.zh}</option>)}
      </select></label>
      <p className="text-xs leading-6 text-content-secondary">{en ? 'The base mode is the ceiling. Rules can further restrict it; choose a broader base before allowing more. Account scopes and system protections remain separate. Background actions that need confirmation stop safely.' : '基准模式是权限上限，具体规则可以进一步限制；需要放宽时先选择更高的基准模式。账号资源范围与系统保护仍单独检查，后台操作需要确认时会安全停止。'}</p>
      {ruleGroups.map(group => <fieldset key={group.id} className="min-w-0 space-y-3"><legend className="mb-2 text-sm font-semibold">{copy.groups[group.id]}</legend>
        {group.rules.map(name => <label key={name} className="flex flex-wrap items-start justify-between gap-3 border-b border-default pb-3">
          <span className="min-w-0 flex-1"><span className="text-sm">{copy.rules[name].name}</span><span className="mt-1 block text-xs text-content-secondary">{copy.rules[name].help}</span></span>
          <select aria-label={copy.rules[name].name} className={permissionInput} disabled={busy} value={draft.rules?.[name] || ''}
            onChange={event => { const rules = { ...draft.rules }; if (event.target.value) rules[name] = event.target.value as AuthorizationRule; else delete rules[name]; setDraft({ ...draft, rules }) }}>
            <option value="">{copy.decisions.preset}</option>{(['allow', 'confirm', 'deny'] as const).map(value => <option value={value} key={value}>{copy.decisions[value]}</option>)}
          </select>
        </label>)}
      </fieldset>)}
    </div> : <Link className="block text-sm text-accent" to={editorLink}>{en ? 'Edit custom rules in Security Center' : '在安全中心编辑自定义规则'}</Link>)}
    <div className="flex flex-wrap items-center gap-3 border-t border-default pt-4">
      <button type="button" disabled={busy || !draft} onClick={() => void save()} className={saveButton}>{busy ? (en ? 'Saving…' : '处理中…') : (en ? 'Save permissions' : '保存权限')}</button>
      <button type="button" disabled={busy} onClick={() => void load()} className="flex min-h-10 items-center gap-1.5 text-sm text-content-secondary hover:text-content focus-visible:outline-2 focus-visible:outline-accent"><RefreshCw size={13} />{en ? 'Refresh' : '刷新'}</button>
      {draft && <span className="text-xs text-content-tertiary">{en ? `Revision ${draft.policy_revision}` : `权限版本 ${draft.policy_revision}`}</span>}
    </div>
    {kind === 'session' && <details onToggle={event => setConnectionOpen(event.currentTarget.open)}><summary className="cursor-pointer text-sm text-accent">{en ? 'Retained connection limits' : '保留的连接访问范围'}</summary>{connectionOpen && <div className="mt-4"><FeatureAccessEditor context={{ module: 'sessions', id: subject, agent }} restrictionsOnly /></div>}</details>}
  </div>
}
