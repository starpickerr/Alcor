import React, { useEffect, useRef, useState } from 'react'
import apiClient from '../api/client'
import { getLang } from '../i18n'
import { notifySecurityPolicyChanged } from '../store/securityPolicyEvents'

/** Reuse the protected owner API. No stored value is fetched into this form. */
export default function CredentialEditor({ name, configured = false }: { name: string; configured?: boolean }) {
  const en = getLang() === 'en'
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [present, setPresent] = useState(configured)
  const owner = useRef({ name, generation: 0, busy: false })
  if (owner.current.name !== name) owner.current = { name, generation: owner.current.generation + 1, busy: false }
  useEffect(() => { setValue(''); setOpen(false); setError(''); setNotice(''); setBusy(false) }, [name])
  useEffect(() => { setPresent(configured) }, [name, configured])
  const save = async (remove = false) => {
    if (owner.current.busy || !remove && !value) return
    const request = owner.current
    request.busy = true
    setBusy(true); setError(''); setNotice('')
    try {
      const result = remove ? await apiClient.deleteSecuritySecret(name) : await apiClient.saveSecuritySecret(name, value)
      if (result.status !== 'success') throw new Error(result.message || (en ? 'Credential update failed' : '凭据更新失败'))
      notifySecurityPolicyChanged()
      if (owner.current === request) {
        setValue(''); setPresent(!remove); setNotice(en ? 'Credential saved.' : '凭据已保存。')
      }
    } catch (cause) { if (owner.current === request) setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { request.busy = false; if (owner.current === request) setBusy(false) }
  }
  return <div className="min-w-0 space-y-3 text-content">
    <div className="flex flex-wrap items-center gap-3 text-sm"><span className="text-content-secondary">{present ? (en ? 'Credential configured' : '已配置凭据') : (en ? 'Credential not configured' : '尚未配置凭据')}</span>
      <button type="button" className="min-h-10 text-accent" onClick={() => { setOpen(!open); setValue('') }}>{en ? 'Configure credential' : '配置凭据'}</button></div>
    {open && <div className="space-y-3">
      <label className="block text-sm">{en ? 'Key or password' : '密钥或密码'}<input className="mt-2 block min-h-10 w-full rounded-btn border border-default bg-surface px-3 py-2 text-content" type="password" autoComplete="new-password" disabled={busy} value={value} onChange={event => setValue(event.target.value)} /></label>
      <div className="flex flex-wrap gap-3"><button type="button" disabled={busy || !value} className="min-h-10 rounded-btn bg-accent px-3 text-sm text-accent-contrast disabled:opacity-50" onClick={() => void save()}>{en ? 'Save credential' : '保存凭据'}</button>
        {present && <button type="button" disabled={busy} className="min-h-10 px-3 text-sm text-danger" onClick={() => void save(true)}>{en ? 'Delete credential' : '删除凭据'}</button>}</div>
    </div>}
    {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    {notice && <p role="status" className="text-sm text-accent">{notice}</p>}
  </div>
}
