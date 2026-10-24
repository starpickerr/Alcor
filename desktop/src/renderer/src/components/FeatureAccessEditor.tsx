import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import apiClient from '../api/client'
import type { FeatureAccessContext, FeatureAccessState, InheritedMcpGrant, Operation } from '../api/client'
import { getLang } from '../i18n'
import { permissionInput } from './permissionStyles'
import { Feedback, useSecurityData } from './SecurityAuthorizationPanels'

const button = 'min-h-10 rounded-btn bg-accent px-4 py-2 text-sm font-medium text-accent-contrast disabled:opacity-40'
const secondary = 'min-h-9 rounded-btn border border-default px-3 py-1.5 text-sm hover:bg-surface-2 disabled:opacity-40'
const operationNames: Record<Operation, string> = { read: '读取', write: '更新', delete: '删除', external_send: '对外发送', system_execute: '系统执行' }

export default function FeatureAccessEditor({ context, restrictionsOnly = false }: { context: FeatureAccessContext; restrictionsOnly?: boolean }) {
  // A different owner must never reuse another feature's draft or pending reply.
  const key = JSON.stringify([context.module, context.kind, context.id, context.agent])
  return <AccessEditor key={key} context={context} restrictionsOnly={restrictionsOnly} />
}

function AccessEditor({ context, restrictionsOnly = false }: { context: FeatureAccessContext; restrictionsOnly?: boolean }) {
  const { module, id, kind, agent } = context
  const loadResources = useCallback(() => apiClient.featureAccess({ module, id, kind, agent }), [module, id, kind, agent])
  const en = getLang() === 'en'
  const state = useSecurityData(loadResources)
  const [subject, setSubject] = useState('')
  const [resource, setResource] = useState('')
  const [operations, setOperations] = useState<Operation[]>(['read'])
  const [purpose, setPurpose] = useState('interactive')
  const [expiry, setExpiry] = useState('')
  useEffect(() => { if (state.data?.subjects.length === 1) setSubject(state.data.subjects[0].id) }, [state.data?.subjects])
  const selected = state.data?.available.find(row => `${row.kind}:${row.id}` === resource)
  const subjectName = (id: string) => {
    const row = state.data?.subjects.find(item => item.id === id)
    return row ? `${en ? row.kind : ({ session: '对话', task: '任务', plugin: '插件' } as Record<string, string>)[row.kind]} ${row.subject}` : id
  }
  return <section className="min-w-0 space-y-4 text-content">
    <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-base font-semibold">{en ? 'Access permissions' : '访问权限'}</h2><button type="button" className={secondary} disabled={state.busy} onClick={() => void state.refresh()}>{en ? 'Refresh' : '刷新'}</button></div>
    <p className="text-sm leading-relaxed text-content-secondary">{en ? 'Manage access to this feature here. Existing restrictions remain effective.' : '在当前功能中配置访问范围，已有范围、有效期和撤销状态继续生效。'}</p>
    <Feedback {...state} />
    {state.data && <>
      {state.data.connection && <ConnectionAccess connection={state.data.connection} busy={state.busy} save={body => void state.run(() => apiClient.featureAccess(context, { action: 'connection', connection_id: state.data!.connection!.id, ...body }), en ? 'Connection access saved.' : '连接访问范围已保存。')} />}
      {(state.data.restrictions || []).filter(row => row.id !== state.data?.connection?.id).map(row => <ConnectionAccess key={row.id} connection={row} busy={state.busy} save={body => void state.run(() => apiClient.featureAccess(context, { action: 'connection', connection_id: row.id, ...body }), en ? 'Connection access saved.' : '连接访问范围已保存。')} />)}
      {state.data.retired && <p className="text-sm text-content-secondary">{en ? 'Connection removed. Existing access can only be viewed or revoked.' : '连接已移除，现有访问范围仅可查看或撤销。'}</p>}
      {restrictionsOnly && !state.data.connection && !state.data.restrictions?.length && <p className="text-sm text-content-secondary">{en ? 'No additional connection limits.' : '没有额外的连接限制。'}</p>}
      {!restrictionsOnly && <>
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
          onClick={() => { if (selected) void state.run(() => apiClient.featureAccess(context, { action: 'issue', subject, resource: { kind: selected.kind, id: selected.id }, operations,
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
        </div><button type="button" className={secondary} disabled={state.busy || grant.revoked} onClick={() => void state.run(() => apiClient.featureAccess(context, { action: 'revoke', grant_id: grant.grant_id, revision: grant.revision }), en ? 'Grant revoked.' : '授权已撤销。')}>{en ? 'Revoke grant' : '撤销授权'}</button>
      </div>)}</div>
      {!!state.data.inherited.length && <details className="border-t border-default pt-4"><summary className="cursor-pointer text-sm font-medium">{en ? 'Preserved connection grants' : '保留的连接授权'}</summary>
        <p className="mt-2 text-xs text-content-secondary">{en ? 'Existing scopes and revocations remain authoritative.' : '原有范围、有效期和撤销状态继续生效。'}</p>
        {state.data.inherited.map(grant => <LegacyGrantChoice key={`${grant.id}:${grant.revision}`} grant={grant} available={state.data!.available} busy={state.busy}
          save={body => void state.run(() => apiClient.featureAccess(context, { action: 'legacy', ...body, id: undefined, legacy_id: body.id }), en ? 'Preserved grant saved.' : '旧授权已保存。')} />)}
      </details>}
      </>}
    </>}
  </section>
}

function LegacyGrantChoice({ grant, available, busy, save }: {
  grant: InheritedMcpGrant; available: FeatureAccessState['available']; busy: boolean
  save: (body: Pick<InheritedMcpGrant, 'id' | 'revision'> & Partial<Pick<InheritedMcpGrant, 'revoked' | 'purposes' | 'resources' | 'valid_until'>>) => void
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
    <button type="button" className={secondary} disabled={busy || grant.scoped && !resources.length && !revoked} onClick={() => save(revoked ? { id: grant.id, revision: grant.revision, revoked } : { id: grant.id, revision: grant.revision, revoked, purposes, resources, valid_until: expiry || null })}>{en ? 'Save preserved grant' : '保存旧授权'}</button>
  </div>
}

function ConnectionAccess({ connection, busy, save }: { connection: NonNullable<FeatureAccessState['connection']>; busy: boolean; save: (body: { allowed: boolean; scopes: string[]; revision: number }) => void }) {
  const en = getLang() === 'en'
  const [allowed, setAllowed] = useState(connection.allowed)
  const [scopes, setScopes] = useState(connection.scopes.length ? connection.scopes : ['*'])
  useEffect(() => { setAllowed(connection.allowed); setScopes(connection.scopes.length ? connection.scopes : ['*']) }, [connection])
  return <div className="space-y-3 border-b border-default pb-4">
    <h3 className="text-sm font-medium">{connection.id} · {en ? 'Connection access scope' : '连接访问范围'}</h3>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={allowed} disabled={busy} onChange={event => setAllowed(event.target.checked)} />{en ? 'Allow connection access' : '允许访问此连接'}</label>
    <div className="flex flex-wrap gap-3">{[...new Set(['*', ...Object.keys(operationNames), ...connection.scopes])].map(operation => <label key={operation} className="flex items-center gap-2 text-sm"><input type="checkbox" disabled={busy} checked={scopes.includes(operation)} onChange={event => setScopes(previous => event.target.checked ? [...previous, operation] : previous.filter(value => value !== operation))} />{operation === '*' ? (en ? 'All operations' : '所有操作') : en ? operation : operationNames[operation as Operation] || operation}</label>)}</div>
    <button type="button" className={secondary} disabled={busy || allowed && !scopes.length} onClick={() => save({ allowed, scopes, revision: connection.revision })}>{en ? 'Save connection access' : '保存连接访问范围'}</button>
  </div>
}
