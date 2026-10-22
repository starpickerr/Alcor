import React, { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import apiClient from '../api/client'
import type { InheritedMcpGrant, Operation, ResourceGrantState, RuntimeTrustState } from '../api/client'
import { getLang } from '../i18n'
import { permissionInput } from './RunPolicyEditor'

const button = 'min-h-10 rounded-btn bg-accent px-4 py-2 text-sm font-medium text-accent-contrast hover:bg-accent-hover disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-accent'
const secondary = 'min-h-9 rounded-btn border border-default px-3 py-1.5 text-sm hover:bg-surface-2 disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-accent'
const operationNames: Record<Operation, string> = { read: '读取', write: '更新', delete: '删除', external_send: '对外发送', system_execute: '系统执行' }
const loadResources = () => apiClient.resourceGrants()
const loadRuntime = () => apiClient.runtimeTrust()
const loadMigration = async () => {
  const result = await apiClient.permissionMigration()
  if (result.status !== 'success') throw new Error(result.message || '无法核对历史权限')
  return result
}

function useSecurityData<T>(loader: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const refresh = useCallback(async () => {
    setBusy(true); setError('')
    try { setData(await loader()) }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy(false) }
  }, [loader])
  useEffect(() => { void refresh() }, [refresh])
  const run = async (action: () => Promise<T>, message: string) => {
    if (busy) return
    setBusy(true); setError(''); setNotice('')
    try { setData(await action()); setNotice(message) }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy(false) }
  }
  return { data, busy, error, notice, refresh, run }
}

function Feedback({ error, notice, busy }: { error: string; notice: string; busy: boolean }) {
  return <>{error && <p role="alert" className="text-sm text-danger">{error}</p>}{notice && <p role="status" className="text-sm text-accent">{notice}</p>}{busy && <p className="text-xs text-content-secondary">{getLang() === 'en' ? 'Updating…' : '正在读取或更新…'}</p>}</>
}

export function ResourceAuthorizationPanel({ preferredSubject }: { preferredSubject?: string }) {
  const en = getLang() === 'en'
  const state = useSecurityData(loadResources)
  const [subject, setSubject] = useState('')
  const [resource, setResource] = useState('')
  const [operations, setOperations] = useState<Operation[]>(['read'])
  const [purpose, setPurpose] = useState('interactive')
  const [expiry, setExpiry] = useState('')
  useEffect(() => {
    const match = state.data?.subjects.find(row => row.subject === preferredSubject)
    if (match) setSubject(match.id)
  }, [preferredSubject, state.data?.subjects])
  const selected = state.data?.available.find(row => `${row.kind}:${row.id}` === resource)
  const subjectName = (id: string) => {
    const row = state.data?.subjects.find(item => item.id === id)
    return row ? `${en ? row.kind : ({ session: '对话', task: '任务', plugin: '插件' } as Record<string, string>)[row.kind]} ${row.subject}` : id
  }
  return <section className="min-w-0 space-y-4 text-content">
    <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-base font-semibold">{en ? 'Specific resource grants' : '特定资源授权'}</h2><button type="button" className={secondary} disabled={state.busy} onClick={() => void state.refresh()}>{en ? 'Refresh' : '刷新'}</button></div>
    <p className="text-sm leading-relaxed text-content-secondary">{en ? 'Grant only the accounts and data sources this conversation, task or plugin needs. Capabilities do not grant access to their data.' : '为对话、任务或插件选择可访问的账号和数据源。获得调用能力后，仍需此处的资源授权。'}</p>
    <Feedback {...state} />
    {state.data && <>
      <div className="grid gap-3 rounded-card border border-default p-4 sm:grid-cols-2">
        <label className="grid gap-1.5 text-sm">{en ? 'Execution subject' : '授权主体'}<select className={permissionInput} disabled={state.busy} value={subject} onChange={event => setSubject(event.target.value)}>
          <option value="">{en ? 'Select a subject' : '选择对话、任务或插件'}</option>{state.data.subjects.map(row => <option key={row.id} value={row.id}>{subjectName(row.id)}</option>)}
        </select></label>
        <label className="grid gap-1.5 text-sm">{en ? 'Specific resource' : '特定资源'}<select className={permissionInput} disabled={state.busy} value={resource} onChange={event => { setResource(event.target.value); setOperations(['read']) }}>
          <option value="">{en ? 'Select an account or source' : '选择账号或数据源'}</option>{state.data.available.map(row => <option key={`${row.kind}:${row.id}`} value={`${row.kind}:${row.id}`}>{row.name} ({row.kind})</option>)}
        </select></label>
        <label className="grid gap-1.5 text-sm">{en ? 'Purpose' : '使用场景'}<select aria-label={en ? 'Purpose' : '使用场景'} className={permissionInput} disabled={state.busy} value={purpose} onChange={event => setPurpose(event.target.value)}>
          <option value="interactive">{en ? 'Interactive' : '交互式对话'}</option><option value="background">{en ? 'Background' : '后台任务'}</option><option value="both">{en ? 'Interactive and background' : '对话与后台'}</option>
        </select></label>
        <label className="grid gap-1.5 text-sm">{en ? 'Expiry (optional)' : '到期时间（可选）'}<input type="datetime-local" className={permissionInput} disabled={state.busy} value={expiry} onChange={event => setExpiry(event.target.value)} /></label>
        <fieldset disabled={state.busy} className="flex flex-wrap gap-3 sm:col-span-2"><legend className="mb-2 text-sm">{en ? 'Allowed operations' : '允许的操作'}</legend>
          {(selected?.operations || ['read'] as Operation[]).map(operation => <label key={operation} className="flex min-h-8 items-center gap-2 text-sm"><input type="checkbox" checked={operations.includes(operation)}
            onChange={event => setOperations(previous => event.target.checked ? [...previous, operation] : previous.filter(value => value !== operation))} />{en ? operation.replace('_', ' ') : operationNames[operation]}</label>)}
        </fieldset>
        <button type="button" className={`${button} justify-self-start sm:col-span-2`} disabled={state.busy || !subject || !selected || !operations.length}
          onClick={() => { if (selected) void state.run(() => apiClient.resourceGrants({ action: 'issue', subject, resource: { kind: selected.kind, id: selected.id }, operations,
            purposes: purpose === 'both' ? ['interactive', 'background'] : [purpose], valid_until: expiry ? new Date(expiry).toISOString() : null }), en ? 'Resource access granted.' : '资源授权已保存。') }}>{en ? 'Grant resource access' : '授予资源访问'}</button>
      </div>
      {!state.data.subjects.length && <p className="text-sm text-content-secondary">{en ? 'Create a conversation or task before granting its resource access.' : '先创建对话或任务，再为它选择资源。'}</p>}
      {!state.data.available.length && <p className="text-sm text-content-secondary">{en ? 'No connected resources. Configure a connection and discover its sources first.' : '暂无已连接资源。请先配置连接，并在数据管理中发现数据源。'} <Link className="text-accent" to="/data">{en ? 'Data management' : '前往数据管理'}</Link></p>}
      <div className="divide-y divide-default">{state.data.grants.map(grant => <div key={grant.grant_id} className="flex flex-wrap items-start justify-between gap-3 py-4">
        <div className="min-w-0 space-y-1"><p className="break-words text-sm font-medium">{state.data?.available.find(row => row.kind === grant.resource.kind && row.id === grant.resource.id)?.name || grant.resource.id}</p>
          <p className="break-all text-xs text-content-secondary">{subjectName(grant.subject)}</p>
          {grant.legacy_scope === 'all_owned_resources' && <p className="text-xs text-content-secondary">{en ? 'Migrated scope: all owned resources on this connection. Revoke to replace it with specific resources.' : '迁移范围：此连接下的全部自有资源。可撤销后改为指定资源。'}</p>}
          <p className="text-xs text-content-secondary">{grant.operations.map(operation => en ? operation : operationNames[operation]).join(' / ')} · {grant.purposes.includes('background') ? (en ? 'Background authorized' : '包含后台执行') : (en ? 'Interactive' : '交互式对话')}{grant.valid_until ? ` · ${new Date(grant.valid_until).toLocaleString()}` : ''}</p>
          <p className={`text-xs ${grant.effective ? 'text-accent' : 'text-content-tertiary'}`}>{grant.revoked ? (en ? 'Revoked' : '已撤销') : grant.effective ? (en ? 'Active' : '有效') : (en ? 'Expired or parent revoked' : '已过期或父授权失效')}</p>
        </div><button type="button" className={secondary} disabled={state.busy || grant.revoked} onClick={() => void state.run(() => apiClient.resourceGrants({ action: 'revoke', grant_id: grant.grant_id, revision: grant.revision }), en ? 'Grant revoked.' : '授权已撤销。')}>{en ? 'Revoke grant' : '撤销授权'}</button>
      </div>)}</div>
      {!!state.data.inherited.length && <details className="border-t border-default pt-4"><summary className="cursor-pointer text-sm font-medium">{en ? 'Preserved connection grants' : '保留的连接授权'}</summary>
        <p className="mt-2 text-xs text-content-secondary">{en ? 'Existing scopes and revocations remain authoritative.' : '原有范围、有效期和撤销状态继续生效。'}</p>
        {state.data.inherited.map(grant => <LegacyGrantChoice key={`${grant.id}:${grant.revision}`} grant={grant} available={state.data!.available} busy={state.busy}
          save={body => void state.run(async () => { const result = await apiClient.updateMcpAccess(body); if (result.status !== 'success') throw new Error(result.message); return loadResources() }, en ? 'Preserved grant saved.' : '旧授权已保存。')} />)}
      </details>}
    </>}
  </section>
}

function LegacyGrantChoice({ grant, available, busy, save }: {
  grant: InheritedMcpGrant; available: ResourceGrantState['available']; busy: boolean
  save: (body: Parameters<typeof apiClient.updateMcpAccess>[0]) => void
}) {
  const en = getLang() === 'en'
  const [revoked, setRevoked] = useState(grant.revoked)
  const [purposes, setPurposes] = useState(grant.purposes)
  const [resources, setResources] = useState(grant.resources)
  const [expiry, setExpiry] = useState(grant.valid_until || '')
  const choices = [...new Map([...grant.resources.map(row => ({ ...row, name: row.id })), ...available.filter(row =>
    row.kind === (grant.server === 'email' ? 'folder' : 'course'))].map(row => [JSON.stringify({ kind: row.kind, id: row.id }), row])).values()]
  const purpose = purposes.length === 2 && purposes.includes('interactive') && purposes.includes('background') ? 'both' : purposes.length === 1 && ['interactive', 'background'].includes(purposes[0]) ? purposes[0] : 'retained'
  return <div data-testid={`mcp-grant-${grant.id}`} className="space-y-3 border-b border-default py-4">
    <p className="text-sm font-medium">{grant.server} / {grant.consumer || (en ? 'Owner' : '用户')}</p>
    <p className="text-xs text-content-secondary">{grant.capabilities.join(', ')} · {grant.revoked ? (en ? 'Revoked' : '已撤销') : (en ? 'Preserved' : '已保留')}</p>
    <label className="flex min-h-8 items-center gap-2 text-sm"><input type="checkbox" disabled={busy} checked={!revoked}
      aria-label={`${en ? 'Restore grant' : '恢复旧授权'} ${grant.id}`} onChange={event => setRevoked(!event.target.checked)} />{en ? 'Allow this preserved grant' : '允许此项旧授权'}</label>
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="grid gap-1 text-xs text-content-secondary">{en ? 'Purpose' : '使用场景'}<select className={permissionInput} disabled={busy} value={purpose}
        aria-label={`${en ? 'Preserved grant purpose' : '旧授权使用场景'} ${grant.id}`} onChange={event => setPurposes(event.target.value === 'both' ? ['interactive', 'background'] : [event.target.value])}>
        <option value="interactive">{en ? 'Interactive' : '交互式对话'}</option><option value="background">{en ? 'Background' : '后台任务'}</option><option value="both">{en ? 'Interactive and background' : '对话与后台'}</option>
        {purpose === 'retained' && <option value="retained" disabled>{en ? 'Original purpose scope' : '原有用途范围'}</option>}
      </select></label>
      {grant.scoped && <label className="grid gap-1 text-xs text-content-secondary">{en ? 'Expiry' : '到期时间'}<input className={permissionInput} type="datetime-local" disabled={busy}
        aria-label={`${en ? 'Preserved grant expiry' : '旧授权到期时间'} ${grant.id}`} value={expiry ? new Date(new Date(expiry).getTime() - new Date(expiry).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : ''}
        onChange={event => setExpiry(event.target.value ? new Date(event.target.value).toISOString() : '')} /></label>}
    </div>
    {grant.scoped && <fieldset disabled={busy} className="flex flex-wrap gap-3"><legend className="mb-2 text-xs text-content-secondary">{en ? 'Allowed resources' : '授权资源'}</legend>{choices.map(row =>
      <label key={`${row.kind}:${row.id}`} className="flex min-h-8 items-center gap-2 text-sm"><input type="checkbox" checked={resources.some(item => item.kind === row.kind && item.id === row.id)}
        onChange={event => setResources(previous => event.target.checked ? [...previous, { kind: row.kind, id: row.id }] : previous.filter(item => item.kind !== row.kind || item.id !== row.id))} />{row.name}</label>)}</fieldset>}
    <button type="button" className={secondary} disabled={busy || grant.scoped && !resources.length} onClick={() => save({ id: grant.id, revision: grant.revision, revoked, purposes, resources, valid_until: expiry || null })}>{en ? 'Save preserved grant' : '保存旧授权'}</button>
  </div>
}

function RuntimeChoice({ row, busy, save }: { row: RuntimeTrustState['runtimes'][number]; busy: boolean; save: (trusted: boolean) => void }) {
  const en = getLang() === 'en'
  const [trusted, setTrusted] = useState(!!row.trusted)
  useEffect(() => setTrusted(!!row.trusted), [row.trusted, row.digest])
  const mode = ({ isolated: 'OS 隔离', trusted: '受信任运行', stopped: '未运行', unavailable: '不可用', remote: '远程连接' } as Record<string, string>)[row.mode] || row.mode
  return <div className="space-y-3 border-b border-default py-4">
    <div className="flex flex-wrap justify-between gap-2"><p className="break-all text-sm font-semibold">{row.id}</p><span className="text-xs text-content-secondary">{en ? row.mode : mode}</span></div>
    <p className="text-xs leading-relaxed text-content-secondary">{row.reason}</p>
    {row.digest && <><p className="break-all text-xs text-content-tertiary">SHA256 {row.digest}</p>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={trusted} disabled={busy} aria-label={en ? `Trusted runtime ${row.id}` : `受信任运行 ${row.id}`} onChange={event => setTrusted(event.target.checked)} />{en ? 'Trust this exact package' : '允许此版本受信任运行'}</label>
      {trusted && <p className="rounded-btn bg-surface-2 p-3 text-xs leading-relaxed">{en ? 'Trusted code can access host files and network. Package changes invalidate this choice. Revoking trust stops trusted workers; enabling trust takes effect on the next launch.' : '受信任代码可访问宿主文件和网络。选择绑定当前包版本，内容改变后失效。撤销会停止受信任进程；启用在下次启动生效。'}</p>}
      <button type="button" className={secondary} disabled={busy || trusted === !!row.trusted} onClick={() => save(trusted)}>{en ? 'Save runtime choice' : '保存运行选择'}</button>
    </>}
  </div>
}

export function RuntimeAuthorizationPanel() {
  const en = getLang() === 'en'
  const state = useSecurityData(loadRuntime)
  return <section className="min-w-0 space-y-4 text-content"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-base font-semibold">{en ? 'Execution environment' : '执行环境'}</h2><button type="button" className={secondary} disabled={state.busy} onClick={() => void state.refresh()}>{en ? 'Refresh' : '刷新'}</button></div>
    <Feedback {...state} />
    {state.data && <><p className="text-sm leading-relaxed text-content-secondary">{state.data.isolation.status === 'enabled' ? (en ? 'OS isolation is available. Each runtime below reports its actual boundary.' : '当前平台支持 OS 隔离。各进程的实际边界如下。') : (en ? 'OS isolation is unavailable. Untrusted local extensions cannot start.' : '当前平台无法提供 OS 隔离，未受信任的本地扩展将拒绝启动。')}</p>
      {!state.data.runtimes.length && <p className="text-sm text-content-secondary">{en ? 'No installed local extensions or configured MCP connections.' : '暂无已安装扩展或已配置的 MCP 连接。'}</p>}
      {state.data.runtimes.map(row => <RuntimeChoice key={`${row.kind}:${row.id}`} row={row} busy={state.busy} save={trusted => { if (row.digest) void state.run(() => apiClient.runtimeTrust({ kind: row.kind, id: row.id, digest: row.digest!, trusted }), en ? 'Runtime choice saved.' : '运行选择已保存。') }} />)}
    </>}
  </section>
}

export function PermissionMigrationPanel() {
  const en = getLang() === 'en'
  const state = useSecurityData(loadMigration)
  const command = (action: string) => state.run(async () => {
    const result = await apiClient.permissionMigration({ action, fingerprint: state.data?.preview?.fingerprint })
    if (result.status !== 'success') throw new Error(result.message || '权限迁移失败')
    return loadMigration()
  }, en ? 'Migration status updated.' : '迁移状态已更新。')
  return <details className="rounded-card border border-default p-4 text-content">
    <summary className="cursor-pointer text-sm font-medium">{en ? 'Historical permissions and recovery' : '历史权限迁移与恢复'}</summary>
    <div className="mt-4 space-y-3"><Feedback {...state} />
      {state.data?.verification && <p className="text-sm">{state.data.verification.valid ? (en ? 'Migration verified.' : '迁移结果已验证。') : (en ? 'Migration has not been verified.' : '迁移结果尚未验证。')}</p>}
      <p className="text-xs leading-relaxed text-content-secondary">{en ? 'Recovery restores unchanged RunPolicy snapshots only. Resource settings and revocations remain in the unified store. Later policy edits prevent recovery. Credentials and business data stay in place.' : '恢复仅撤回未被修改的 RunPolicy 快照。资源授权与撤销记录继续保留在统一存储中；后续策略修改会阻止恢复。账号凭证与业务数据保留原位。'}</p>
      {state.data?.preview && <p className="text-xs text-content-secondary">{en ? `${state.data.preview.pending} subjects pending; ${state.data.preview.needs_confirmation} need confirmation.` : `${state.data.preview.pending} 个主体待迁移，${state.data.preview.needs_confirmation} 个需要确认。`}</p>}
      <div className="flex flex-wrap gap-2"><button type="button" className={secondary} disabled={state.busy} onClick={() => void state.refresh()}>{en ? 'Preview and verify' : '核对状态'}</button>
        <button type="button" className={button} disabled={state.busy || !state.data?.preview || state.data.verification?.status === 'applied'} onClick={() => void command('apply')}>{en ? 'Apply migration' : '核对并迁移'}</button>
        <button type="button" className={secondary} disabled={state.busy || state.data?.verification?.status !== 'applied'} onClick={() => void command('recover')}>{en ? 'Recover RunPolicy snapshots' : '恢复运行策略快照'}</button>
      </div>
    </div>
  </details>
}
