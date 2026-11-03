import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Check, Eye, Loader2, Shield, ArrowLeft } from 'lucide-react'
import apiClient from '../api/client'
import type { PermissionMode } from '../api/client'
import { Link, useLocation } from 'react-router-dom'
import { getLang } from '../i18n'
import ComposerChip from './ComposerChip'
import RunPolicyEditor from './RunPolicyEditor'
import { useSessionSettingsStore } from '../store/sessionSettingsStore'

const labels: Record<PermissionMode, { zh: string; en: string }> = {
  'read-only': { zh: '只读', en: 'Read Only' },
  'workspace-write': { zh: '工作区写入', en: 'Workspace Write' },
  'full-access': { zh: '完全访问', en: 'Full Access' },
  custom: { zh: '自定义', en: 'Custom' },
}

export default function PermissionSelector({ sessionId }: { sessionId: string }) {
  const en = getLang() === 'en'
  const location = useLocation()
  const cfg = useSessionSettingsStore(state => state.sessionId === sessionId ? state.cfg : null)
  const open = useSessionSettingsStore(state => state.openMenu === 'permission')
  const setOpenMenu = useSessionSettingsStore(state => state.setOpenMenu)
  const refresh = useSessionSettingsStore(state => state.refresh)
  const storeError = useSessionSettingsStore(state => state.error)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)
  useEffect(() => { generation.current++; setEditing(false); setBusy(false); setError('') }, [sessionId])
  useEffect(() => { if (open) void refresh(sessionId) }, [open, refresh, sessionId])
  const close = useCallback(() => setOpenMenu(null), [setOpenMenu])
  const permission = cfg?.permission
  const mode = permission?.policy?.permission_mode || permission?.mode
  const name = (value: PermissionMode) => labels[value][en ? 'en' : 'zh']
  const securityLink = `/security?${new URLSearchParams({ tab: 'agent-permissions', kind: 'session', subject: sessionId, returnTo: location.pathname + location.search })}`
  const select = async (next: PermissionMode) => {
    if (busy) return
    const token = generation.current
    setBusy(true); setError('')
    try {
      const current = await apiClient.getRunPolicy('session', sessionId)
      if (token !== generation.current) return
      await apiClient.saveRunPolicy('session', sessionId, { filesystem_mode: next === 'custom' ? current.policy.filesystem_mode : next,
        permission_mode: next, ...(next === 'custom' ? { rules: current.policy.rules || {} } : {}), policy_revision: current.policy.policy_revision })
      if (token !== generation.current) return
      await refresh(sessionId); close()
    } catch (cause) { if (token === generation.current) setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { if (token === generation.current) setBusy(false) }
  }
  return <div id="permission-selector-chip"><ComposerChip icon={!mode ? <Loader2 size={13} /> : mode === 'read-only' ? <Eye size={13} /> : <Shield size={13} />}
    label={mode ? name(mode) : (en ? 'Loading permissions…' : '正在读取权限…')}
    tip={en ? 'Agent permissions for this conversation' : '此对话的 Agent 权限'}
    menuClassName="w-[min(26rem,calc(100vw-2rem))] max-h-[70vh]"
    open={open} onToggle={() => { if (open) close(); else { setEditing(false); setOpenMenu('permission') } }} onClose={close}>
    <div className="min-w-0 space-y-3 p-2">
      {editing ? <>
        <button type="button" onClick={() => setEditing(false)} className="flex min-h-8 items-center gap-1 text-xs text-accent"><ArrowLeft size={13} />{en ? 'Conversation permissions' : '对话权限'}</button>
        <RunPolicyEditor kind="session" subject={sessionId} onSaved={() => { void refresh(sessionId) }} />
      </> : <>
        <div className="px-1"><h3 className="text-sm font-semibold text-content">{en ? 'Agent permissions' : 'Agent 权限'}</h3>
          <p className="mt-1 text-xs leading-relaxed text-content-secondary">{en ? 'Applies to this conversation. Project conversations share files and keep independent permissions.' : '仅作用于此对话。项目内对话共享文件，各自保存权限。'}</p></div>
        {(error || storeError || permission?.error) && <p role="alert" className="text-xs text-danger">{error || storeError || permission?.error}</p>}
        {(Object.keys(labels) as PermissionMode[]).map(value => <button key={value} type="button" disabled={busy || !permission} aria-pressed={mode === value} onClick={() => void select(value)}
          className={`flex min-h-10 w-full items-center justify-between rounded-btn px-3 py-2 text-left text-sm focus-visible:outline-2 focus-visible:outline-accent ${mode === value ? 'bg-accent-soft text-accent' : 'text-content hover:bg-surface-2'}`}>
          {name(value)}{mode === value && <Check size={14} />}
        </button>)}
        <div className="border-t border-default pt-2"><button type="button" onClick={() => setEditing(true)} className="min-h-10 w-full rounded-btn px-3 text-left text-sm text-accent hover:bg-surface-2">{en ? 'Set conversation permissions' : '设置对话权限'}</button>
          <Link to={securityLink} onClick={close} className="block rounded-btn px-3 py-2 text-xs text-content-secondary hover:text-content">{en ? 'Edit custom rules in Security Center' : '在安全中心编辑自定义规则'}</Link></div>
      </>}
    </div>
  </ComposerChip></div>
}
