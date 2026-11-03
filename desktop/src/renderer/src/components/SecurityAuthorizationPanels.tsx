import React, { useCallback, useEffect, useState } from 'react'
import apiClient from '../api/client'
import type { RuntimeStatusState } from '../api/client'
import { getLang } from '../i18n'

const button = 'min-h-10 rounded-btn bg-accent px-4 py-2 text-sm font-medium text-accent-contrast hover:bg-accent-hover disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-accent'
const secondary = 'min-h-9 rounded-btn border border-default px-3 py-1.5 text-sm hover:bg-surface-2 disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-accent'
const loadRuntime = () => apiClient.runtimeStatus()
const loadMigration = async () => {
  const result = await apiClient.permissionMigration()
  if (result.status !== 'success') throw new Error(result.message || '无法核对历史权限')
  return result
}

export function useSecurityData<T>(loader: () => Promise<T>) {
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

export function Feedback({ error, notice, busy }: { error: string; notice: string; busy: boolean }) {
  return <>{error && <p role="alert" className="text-sm text-danger">{error}</p>}{notice && <p role="status" className="text-sm text-accent">{notice}</p>}{busy && <p className="text-xs text-content-secondary">{getLang() === 'en' ? 'Updating…' : '正在读取或更新…'}</p>}</>
}

function RuntimeStatus({ row, busy, onRebuild, onAdopt }: { row: RuntimeStatusState['runtimes'][number]; busy: boolean; onRebuild: (row: RuntimeStatusState['runtimes'][number]) => void; onAdopt: (row: RuntimeStatusState['runtimes'][number], source: string) => void }) {
  const en = getLang() === 'en'
  const [confirming, setConfirming] = useState(false)
  const [source, setSource] = useState('')
  const migration = row.storage_migration
  const mode = ({ isolated: 'OS 隔离', trusted: '旧进程需要重启', stopped: '未运行', unavailable: '不可用', remote: '远程连接' } as Record<string, string>)[row.mode] || row.mode
  const resume = Boolean(row.rebuild_operation)
  return <div className="space-y-2 border-b border-default py-4">
    <div className="flex flex-wrap justify-between gap-2"><p className="break-all text-sm font-semibold">{row.id}</p><span className="text-xs text-content-secondary">{en ? row.mode : mode}</span></div>
    <p className="text-xs leading-6 text-content-secondary">{row.reason}</p>
    {row.environment && <p className="break-all text-xs leading-5 text-content-secondary">{en ? 'Independent dependency environment' : '独立依赖环境'} · {row.environment.base.version} · {row.environment.state}</p>}
    {row.environment_error && <p className="text-xs leading-6 text-danger">{row.environment_error}</p>}
    {migration && (confirming ? <div className="space-y-3 rounded-btn border border-default p-3">
      <p className="text-sm leading-6">{en ? 'Confirm that the selected data belongs to this MCP. It will be copied to the current version; original files stay in place. Active processes will stop.' : '请确认所选数据属于此 MCP。数据将复制到当前版本，原文件保留；当前进程会停止。'}</p>
      <label className="block space-y-2 text-sm"><span>{en ? 'Retained data source' : '保留数据来源'}</span>
        <select className="min-h-10 w-full min-w-0 rounded-btn border border-default bg-surface px-2" value={source} disabled={busy} onChange={event => setSource(event.target.value)}>
          <option value="">{en ? 'Select a source' : '请选择来源'}</option>
          {migration.sources.map(item => <option key={item.id} value={item.id}>{item.path}</option>)}
        </select>
      </label>
      {migration.sources.find(item => item.id === source) && <p className="break-all text-xs text-content-secondary">{migration.sources.find(item => item.id === source)?.path}</p>}
      <div className="flex flex-wrap gap-2">
        <button type="button" className={button} disabled={busy || !migration.sources.some(item => item.id === source)} onClick={() => onAdopt(row, source)}>{en ? 'Confirm and copy' : '确认并复制'}</button>
        <button type="button" className={secondary} disabled={busy} onClick={() => { setConfirming(false); setSource('') }}>{en ? 'Cancel' : '取消'}</button>
      </div>
    </div> : <button type="button" className={secondary} disabled={busy} onClick={() => setConfirming(true)}>{en ? 'Confirm data ownership' : '确认数据归属'}</button>)}
    {(row.can_rebuild || resume) && <button type="button" className={secondary} disabled={busy}
      aria-label={en ? `${resume ? 'Resume rebuilding' : 'Rebuild'} dependency environment for ${row.id}` : `${resume ? '继续重建' : '重建'} ${row.id} 的依赖环境`}
      onClick={() => onRebuild(row)}>{resume ? (en ? 'Resume rebuild' : '继续重建') : (en ? 'Rebuild environment' : '重建环境')}</button>}
    {!row.can_rebuild && row.rebuild_reason && <p className="text-xs leading-6 text-content-secondary">{row.rebuild_reason}</p>}
  </div>
}

export function RuntimeAuthorizationPanel() {
  const en = getLang() === 'en'
  const state = useSecurityData(loadRuntime)
  const rebuild = (row: RuntimeStatusState['runtimes'][number]) => void state.run(
    () => apiClient.rebuildRuntime(row.kind, row.id, row.rebuild_operation),
    en ? 'Dependency environment rebuilt. A new worker will start on next use.' : '依赖环境已重建，后续使用时将启动新进程。',
  )
  const adopt = (row: RuntimeStatusState['runtimes'][number], source: string) => void state.run(
    () => apiClient.adoptRuntimeStorage(row.id, row.storage_migration!.token, source),
    en ? 'Data ownership confirmed. Original files are preserved; the next use starts the current version.' : '数据归属已确认，原文件已保留，下次使用时将启动当前版本。',
  )
  return <section className="min-w-0 space-y-4 text-content"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-base font-semibold text-content">{en ? 'Execution environment' : '执行环境'}</h2><button type="button" className={secondary} disabled={state.busy} onClick={() => void state.refresh()}>{en ? 'Refresh' : '刷新'}</button></div>
    <Feedback {...state} />
    {state.data?.application_isolation && <p role="status" className="rounded-btn border border-default p-3 text-sm leading-6">{en ? 'Core and Electron are trusted hosts. Each managed child process reports its actual isolation below.' : 'Core 和 Electron 是可信宿主。下方显示各受管子进程的实际隔离状态。'}</p>}
    {state.data && <><p className="text-sm leading-relaxed text-content-secondary">{state.data.isolation.status === 'enabled' ? (en ? 'OS isolation is available. Each runtime below reports its actual boundary.' : '当前平台支持 OS 隔离。各进程的实际边界如下。') : (en ? 'OS isolation is unavailable. Untrusted local extensions cannot start.' : '当前平台无法提供 OS 隔离，未受信任的本地扩展将拒绝启动。')}</p>
      {!state.data.runtimes.length && <p className="text-sm text-content-secondary">{en ? 'No installed local extensions or configured MCP connections.' : '暂无已安装扩展或已配置的 MCP 连接。'}</p>}
      {state.data.runtimes.some(row => row.can_rebuild) && <p className="text-xs leading-6 text-content-secondary">{en ? 'Rebuilding keeps accounts, settings and business data. Active processes for this provider stop; the next use starts the new environment.' : '重建保留账号、配置和业务数据。该能力当前的进程会停止，下次使用时将启动新环境。'}</p>}
      {state.data.runtimes.map(row => <RuntimeStatus key={`${row.kind}:${row.id}`} row={row} busy={state.busy} onRebuild={rebuild} onAdopt={adopt} />)}
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
