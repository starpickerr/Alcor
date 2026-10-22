import React, { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown, RefreshCw, Shield } from 'lucide-react'
import apiClient from '../api/client'
import type { CapabilityEntry, CapabilityOperations, FileMode, RunPolicy, RunPolicyState } from '../api/client'
import { getLang } from '../i18n'

export const permissionInput = 'min-h-10 min-w-0 rounded-btn border border-default bg-surface px-3 py-2 text-sm text-content focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50'
const saveButton = 'min-h-10 rounded-btn bg-accent px-4 py-2 text-sm font-medium text-accent-contrast hover:bg-accent-hover disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent'
const modes: { id: FileMode; zh: string; en: string; description: string }[] = [
  { id: 'read-only', zh: '只读', en: 'Read Only', description: '读取授权工作区；业务写入由下方能力独立决定。' },
  { id: 'workspace-write', zh: '工作区写入', en: 'Workspace Write', description: '读写绑定工作区，命令在受限环境中执行。' },
  { id: 'full-access', zh: '完全访问', en: 'Full Access', description: '放宽普通文件与命令限制；账号、私有空间和插件仍需授权。' },
]
const sensitive = [
  { id: 'delete', zh: '删除', en: 'Delete' },
  { id: 'external_send', zh: '对外发送', en: 'External send' },
  { id: 'system_execute', zh: '系统执行', en: 'System execution' },
] as const

export function PolicyFields({ value, onChange, catalog, disabled = false }: {
  value: RunPolicy; onChange: (next: RunPolicy) => void; catalog: CapabilityEntry[]; disabled?: boolean
}) {
  const en = getLang() === 'en'
  const [source, setSource] = useState('all')
  const [paths, setPaths] = useState((value.extra_roots || []).join('\n'))
  const savedPaths = (value.extra_roots || []).join('\n')
  useEffect(() => setPaths(savedPaths), [savedPaths])
  const visible = catalog.filter(row => source === 'all' || row.source === source)
  const changeCapability = (id: string, operations: CapabilityOperations) => onChange({ ...value, capabilities: { ...value.capabilities, [id]: operations } })
  return <div className="space-y-6">
    <div className="space-y-2">
      <label className="grid gap-2 text-sm font-medium">{en ? 'Files and commands' : '文件与命令权限'}
        <select className={`${permissionInput} w-full`} value={value.filesystem_mode} disabled={disabled}
          onChange={event => onChange({ ...value, filesystem_mode: event.target.value as FileMode })}>
          {modes.map(mode => <option key={mode.id} value={mode.id}>{en ? mode.en : mode.zh}</option>)}
        </select>
      </label>
      <p className="text-xs leading-relaxed text-content-secondary">{en ? 'This controls file and command access. Business capabilities and account access are authorized separately.' : modes.find(mode => mode.id === value.filesystem_mode)?.description}</p>
      <details className="pt-2 text-sm">
        <summary className="cursor-pointer text-content-secondary">{en ? 'Paths outside the workspace' : '工作区外路径授权'}</summary>
        <label className="mt-3 grid gap-2 text-xs text-content-secondary">{en ? 'Additional directories (one per line)' : '额外授权目录（每行一个）'}
          <textarea className={`${permissionInput} min-h-24 w-full`} disabled={disabled} value={paths}
            onChange={event => setPaths(event.target.value)} onBlur={() => onChange({ ...value, extra_roots: paths.split('\n').map(path => path.trim()).filter(Boolean) })} />
        </label>
        <p className="mt-2 text-xs leading-relaxed text-content-secondary">{en ? 'File mode still applies. Credentials, managed data and other private workspaces stay protected.' : '仍受所选文件模式约束。凭据、托管业务数据和其他私有工作区继续受保护。'}</p>
      </details>
    </div>
    <div>
      <h3 className="text-sm font-semibold">{en ? 'Agent capabilities' : 'Agent 可用能力'}</h3>
      <p className="mt-1 text-xs leading-relaxed text-content-secondary">{en ? 'Manage allows reading and updating. Sensitive operations require separate approval or preauthorization.' : '允许管理包括读取与更新。删除、对外发送和系统执行分别审批；后台执行须提前授权。'}</p>
      <div className="my-3 flex flex-wrap gap-1" aria-label={en ? 'Capability sources' : '能力来源'}>
        {[['all', '全部', 'All'], ['builtin', '内置工具', 'Built-in'], ['plugin', '插件', 'Plugins'], ['mcp', 'MCP', 'MCP']].map(([id, zh, english]) =>
          <button key={id} type="button" aria-pressed={source === id} onClick={() => setSource(id)}
            className={`min-h-8 rounded-btn px-3 text-xs focus-visible:outline-2 focus-visible:outline-accent ${source === id ? 'bg-accent-soft text-accent' : 'text-content-secondary hover:bg-surface-2'}`}>{en ? english : zh}</button>)}
      </div>
      <div className="divide-y divide-default">
        {visible.map(row => {
          const operations = value.capabilities[row.id] || {}
          const level = operations.read === 'allow' && operations.write === 'allow' ? 'manage' : operations.read === 'allow' && operations.write !== 'allow' ? 'view' :
            (!operations.read || operations.read === 'deny') && (!operations.write || operations.write === 'deny') ? 'deny' : 'legacy'
          const label = en ? row.id.split(':')[1] : row.name
          const state = row.source === 'plugin' ? !row.installed ? (en ? 'Not installed' : '未安装') : row.enabled === false ? (en ? 'Disabled' : '已禁用') : row.runtime_state === 'active' ? (en ? 'Running' : '运行中') : (en ? 'Installed' : '已安装') : row.source === 'mcp' ? (en ? 'MCP connection' : 'MCP 连接') : (en ? 'Built-in' : '内置工具')
          return <div key={row.id} className="py-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0"><p className="break-words text-sm font-medium">{label}</p><p className="mt-0.5 text-xs text-content-tertiary">{state}</p></div>
              <select className={permissionInput} aria-label={en ? `${label} authorization` : `${label}调用授权`} disabled={disabled} value={level} onChange={event => {
                const next = event.target.value
                changeCapability(row.id, next === 'deny' ? { read: 'deny', write: 'deny', delete: 'deny', external_send: 'deny', system_execute: 'deny' } : {
                  delete: 'confirm', external_send: 'confirm', system_execute: 'confirm', ...operations,
                  read: 'allow', write: next === 'manage' ? 'allow' : 'deny',
                })
              }}>
                <option value="deny">{en ? 'Deny' : '禁止'}</option><option value="view">{en ? 'View only' : '仅查看'}</option><option value="manage">{en ? 'Manage' : '允许管理'}</option>
                {level === 'legacy' && <option value="legacy" disabled>{en ? 'Existing restrictions' : '原有限制（待确认）'}</option>}
              </select>
            </div>
            {Object.keys(operations).length > 0 && <details className="mt-2 text-xs">
              <summary className="flex min-h-7 cursor-pointer items-center gap-1 text-content-secondary"><ChevronDown size={12} />{en ? 'Sensitive operations' : '敏感操作与预授权'}</summary>
              <div className="mt-2 grid gap-2 sm:grid-cols-3">{sensitive.map(operation => <label key={operation.id} className="grid min-w-0 gap-1 text-content-secondary">{en ? operation.en : operation.zh}
                <select className={`${permissionInput} w-full`} aria-label={`${label} ${en ? operation.en : operation.zh}`} disabled={disabled} value={operations[operation.id] || 'confirm'}
                  onChange={event => changeCapability(row.id, { ...operations, [operation.id]: event.target.value })}>
                  <option value="deny">{en ? 'Deny' : '禁止'}</option><option value="confirm">{en ? 'Ask each time' : '每次审批'}</option><option value="allow">{en ? 'Preauthorize' : '预授权'}</option>
                </select>
              </label>)}</div>
            </details>}
          </div>
        })}
        {!visible.length && <p className="py-4 text-sm text-content-secondary">{en ? 'No registered capabilities in this category.' : '此来源暂无已注册能力。'}</p>}
      </div>
    </div>
  </div>
}

export default function RunPolicyEditor({ kind, subject, agent, onSaved }: {
  kind: 'template' | 'session' | 'task'; subject: string; agent?: string; onSaved?: (policy: RunPolicy) => void
}) {
  const en = getLang() === 'en'
  const [state, setState] = useState<RunPolicyState | null>(null)
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
      const result = await apiClient.saveRunPolicy(kind, subject, draft, agent)
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
    {!!Object.keys(state?.compatibility_restrictions || {}).length && <p className="flex items-start gap-2 text-xs leading-relaxed text-content-secondary"><Shield size={14} className="mt-0.5 shrink-0" />{en ? 'Additional restrictions from your previous policy are preserved. Changing the file mode updates file restrictions only.' : '已保留旧策略的额外限制。切换文件模式只调整文件相关限制。'}</p>}
    {draft && state && <PolicyFields value={draft} onChange={value => { setDraft(value); setMessage('') }} catalog={state.capabilities} disabled={busy} />}
    <div className="flex flex-wrap items-center gap-3 border-t border-default pt-4">
      <button type="button" disabled={busy || !draft} onClick={() => void save()} className={saveButton}>{busy ? (en ? 'Saving…' : '处理中…') : (en ? 'Save permissions' : '保存权限')}</button>
      <button type="button" disabled={busy} onClick={() => void load()} className="flex min-h-10 items-center gap-1.5 text-sm text-content-secondary hover:text-content focus-visible:outline-2 focus-visible:outline-accent"><RefreshCw size={13} />{en ? 'Refresh' : '刷新'}</button>
      {draft && <span className="text-xs text-content-tertiary">{en ? `Revision ${draft.policy_revision}` : `权限版本 ${draft.policy_revision}`}</span>}
    </div>
  </div>
}
