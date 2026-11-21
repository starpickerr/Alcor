import React, { useEffect, useRef, useState } from 'react'
import { Button, Modal } from '@deepseek-ai/dsh-client-ui-primitives'
import { remote } from './account-contract.js'

const welcomeKey = 'alcor-chatgpt-welcome-shown'
async function unwrap(promise) {
  const result = await promise
  if (!result.ok) throw new Error(result.error?.message || '账号操作未完成。')
  return result.value
}

export function ChatgptSettings({ api, subscribe }) {
  const [state, setState] = useState(null)
  const [selection, setSelection] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [browserUrl, setBrowserUrl] = useState('')
  const [welcome, setWelcome] = useState(false)
  const [revision, setRevision] = useState(0)
  const version = useRef(0)
  const pending = !!state?.pending
  useEffect(() => subscribe(() => setRevision(v => v + 1)), [subscribe])

  useEffect(() => {
    let active = true, timer
    const current = version.current
    async function refresh() {
      try {
        const value = await unwrap(api.accountState({}))
        if (!active || current !== version.current) return
        setError('')
        setState(value)
        if (!value.pending) setBrowserUrl('')
        setSelection(selected => selected === 'new' || value.accounts.some(row => row.id === selected) ? selected : value.activeId || value.accounts[0]?.id || 'new')
        if (value.pending) timer = setTimeout(refresh, 900)
      } catch { if (active) setError('无法读取 ChatGPT 账号，请重新加载。') }
    }
    refresh()
    return () => { active = false; clearTimeout(timer) }
  }, [api, pending, revision])

  const activeId = state?.activeId
  useEffect(() => {
    if (!activeId) return
    setSelection(activeId)
    try {
      if (!localStorage.getItem(welcomeKey)) {
        localStorage.setItem(welcomeKey, '1')
        setWelcome(true)
      }
    } catch { /* Welcome is optional when the client disallows local storage. */ }
  }, [activeId])
  async function act(operation) {
    setBusy(true); setError(''); version.current++
    try { await operation() } catch (failure) { setError(failure.message || '账号操作未完成。') }
    finally { setRevision(v => v + 1); setBusy(false) }
  }
  const chosen = state?.accounts.find(row => row.id === selection)
  const current = state?.accounts.find(row => row.id === activeId)
  async function connect() {
    const { browserUrl } = await unwrap(api.beginLogin(chosen ? { accountId: chosen.id } : {}))
    setBrowserUrl(browserUrl)
    // The signed desktop opens HTTP links in the system browser. Keep a link for blocked browser pop-ups.
    window.open(browserUrl, '_blank', 'noopener,noreferrer')
  }

  return <section className="alcor-chatgpt-account" aria-label="ChatGPT 套餐">
    <h3>ChatGPT 套餐</h3>
    <p>{current?.connected ? `当前使用 ${current.label} 的 ChatGPT 套餐。` : '连接 ChatGPT 后，可在聊天的模型列表中选择账号可用的模型。'}</p>
    <p>所选模型的对话和工具数据会发送给 OpenAI，仅使用 ChatGPT 套餐授权。只有在 ChatGPT 中另行允许使用额外积分时，才可能消耗积分。 <a href="https://chatgpt.com/settings/usage" target="_blank" rel="noreferrer">管理用量</a></p>
    {state?.accounts.length > 0 && <label>ChatGPT 账号 <select disabled={busy || pending} value={selection} onChange={e => setSelection(e.target.value)}>
      {state.accounts.map(row => <option key={row.id} value={row.id}>
        {row.label}{state.accounts.some(other => other.id !== row.id && other.label === row.label) ? ` · ${row.id.slice(0, 8)}` : ''}{row.id === activeId && row.connected ? '（使用中）' : ''}
      </option>)}
      <option value="new">添加账号或工作区</option>
    </select></label>}
    <div className="alcor-chatgpt-actions">
      {pending ? <>
        {browserUrl && <a href={browserUrl} target="_blank" rel="noreferrer">打开登录页面</a>}
        <Button disabled={busy} onClick={() => act(() => unwrap(api.cancelLogin({})))}>取消登录</Button>
      </> : <>
        {state && (!chosen?.connected || chosen.id !== activeId) &&
          <button type="button" className="alcor-chatgpt-login" data-oai-siwc-button="" disabled={busy} onClick={() => act(connect)}>Continue with ChatGPT</button>}
        {(chosen?.canSignOut ?? chosen?.connected) && <Button disabled={busy} variant="outline" onClick={() => act(() => unwrap(api.signOut({ accountId: chosen.id })))}>退出此账号</Button>}
      </>}
      {error && <Button disabled={busy} onClick={() => { setError(''); setRevision(v => v + 1) }}>重新加载</Button>}
    </div>
    <p aria-live="polite">{pending ? '等待浏览器授权…' : state?.notice || (!state && !error ? '正在读取账号…' : '')}</p>
    {error && <p role="alert">{error}</p>}
    <Modal open={welcome} title="已开始使用 ChatGPT 套餐" onClose={() => setWelcome(false)}>
      <p>通过 ChatGPT 模型发送的请求将使用你的套餐额度，可在 ChatGPT 用量设置中管理。</p>
      <Button variant="primary" onClick={() => setWelcome(false)}>知道了</Button>
    </Modal>
  </section>
}
export const inject = ['slots', 'remote']
export async function apply(ctx) {
  await ctx.remote.$mount(remote)
  ctx.effect(() => {
    const style = document.createElement('style')
    style.textContent = '.alcor-chatgpt-account{padding:16px;min-width:0;color:inherit}.alcor-chatgpt-account h3{margin:0 0 8px}.alcor-chatgpt-account p{margin:6px 0;overflow-wrap:anywhere}.alcor-chatgpt-account select{max-width:100%;font:inherit}.alcor-chatgpt-actions{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin-top:12px}.alcor-chatgpt-login{background:#111;color:#fff;border:1px solid #666;border-radius:8px;padding:8px 14px;font:inherit;cursor:pointer}.alcor-chatgpt-login:disabled{opacity:.6}'
    document.head.append(style)
    return () => style.remove()
  })
  ctx.inject(['remote.alcorChatgpt'], inner => {
    const subscribe = fn => inner.remote.$on('llm/models-updated', fn)
    const inject = () => ({ api: inner.remote.alcorChatgpt, subscribe })
    inner.slots.inject('plugins.bundle.config', () => inner.slots.register({ name: 'plugins.bundle.config', key: '@alcor/chatgpt', inject }, ChatgptSettings))
    inner.slots.inject('settings.models.footer', () => inner.slots.register({ name: 'settings.models.footer', id: 'alcor-chatgpt-account', inject }, ChatgptSettings))
  })
}
