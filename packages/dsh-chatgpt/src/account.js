import { randomUUID } from 'node:crypto'
import { credentialKey } from '@deepseek-ai/dsh-credentials'
import { attributionHeaders } from '@deepseek-ai/dsh-llm'
import { exchange, openLogin, resource, revoke, tokens, verifyIdentity } from './account-oauth.js'
import { upstreamError } from './account-errors.js'

const key = credentialKey('alcor-chatgpt', 'accounts')
const admissionErrors = new Set(['subscription_sharing_invalid_user', 'chatpass_v2_scope_not_authorized', 'chatpass_v2_invalid_authorization_context'])
const disconnected = () => new Error('请先在 DSH 设置 → 模型中连接 ChatGPT 套餐。')
function payload(record) {
  if (!record) return { accounts: [], activeId: null }
  if (record.kind !== 'grant' || !Array.isArray(record.payload?.accounts) || typeof record.payload.hostId !== 'string') {
    throw new Error('ChatGPT 账号凭据无法读取。')
  }
  return record.payload
}
const grant = value => ({ kind: 'grant', payload: value })
const selected = data => data.accounts.find(item => item.id === data.activeId)
const tag = data => selected(data)?.session ?? null

export function createAccount(ctx, { fetch: fetcher = globalThis.fetch } = {}) {
  const responseAccounts = new WeakMap()
  const credentials = ctx.credentials
  const lifetime = new AbortController()
  let accountLife = new AbortController(), currentTag
  let pending = null, notice = '', signingOut = null
  const read = async () => payload(await credentials.readRecord(key))
  function observe(data) {
    const next = tag(data)
    const previous = currentTag
    currentTag = next
    if (previous === next || previous === undefined) return
    accountLife.abort()
    accountLife = new AbortController()
    ctx.emit('llm/models-updated', 'alcor-chatgpt')
  }
  function finish(attempt, message) {
    if (pending !== attempt) return
    pending = null
    attempt.controller.abort()
    if (message) notice = message
  }
  ctx.on('credentials/record-updated', async changed => {
    if (changed === key && !lifetime.signal.aborted) observe(await read())
  })
  ctx.effect(() => () => { lifetime.abort(); accountLife.abort(); if (pending) finish(pending) })
  async function accountState() {
    const data = await read()
    observe(data)
    return { activeId: data.activeId, accounts: data.accounts.map(item => ({
      id: item.id, label: item.email || 'ChatGPT 账号', connected: !!item.session && !!item.tokens, canSignOut: !!item.tokens,
    })), pending: !!pending, notice }
  }
  async function beginLogin({ accountId } = {}) {
    lifetime.signal.throwIfAborted()
    if (pending || signingOut) throw new Error('账号操作正在进行。')
    notice = ''
    const attempt = { controller: new AbortController() }
    const signal = attempt.controller.signal
    pending = attempt
    try {
      const record = await credentials.modifyRecord(key, async current => current ? undefined :
        grant({ ...payload(current), hostId: 'urn:uuid:' + randomUUID() }))
      signal.throwIfAborted()
      const data = payload(record)
      observe(data)
      const registration = accountId && data.accounts.find(row => row.id === accountId)
      if (accountId && !registration) throw new Error('所选 ChatGPT 账号已移除。')
      const browserUrl = await openLogin(attempt, registration, data.hostId, async ({ clientId, code, redirect, nonce, verifier }) => {
        const body = await exchange(fetcher, { grant_type: 'authorization_code', code, client_id: clientId, redirect_uri: redirect, code_verifier: verifier }, signal)
        const identity = await verifyIdentity(fetcher, body.id_token, clientId, nonce, signal)
        const tokenSet = tokens(body)
        const result = await credentials.modifyRecord(key, async current => {
          signal.throwIfAborted()
          const live = payload(current)
          const previous = live.accounts.find(row => row.clientId === clientId)
          if (live.hostId !== data.hostId || (registration && (previous?.id !== registration.id || previous.session !== registration.session)) ||
              (previous && previous.subject !== identity.subject)) throw new Error('ChatGPT 账号身份已改变。')
          const id = previous?.id ?? randomUUID()
          const updated = { id, clientId, ...identity, tokens: tokenSet, session: tokenSet ? randomUUID() : null, needsConsent: !tokenSet }
          return grant({ ...live, accounts: [...live.accounts.filter(row => row.id !== id), updated],
            activeId: tokenSet ? id : (live.activeId === id ? null : live.activeId) })
        })
        observe(payload(result))
        notice = tokenSet ? '已连接 ChatGPT 套餐。' : '已登录，但未授权使用 ChatGPT 套餐。可重新连接并授权。'
      }, message => finish(attempt, message))
      return { browserUrl }
    } catch (error) {
      finish(attempt, '登录未能启动，请重试。')
      throw error
    }
  }
  async function cancelLogin() {
    if (pending) finish(pending, '已取消登录。')
    return accountState()
  }
  async function signOut({ accountId }) {
    if (signingOut) throw new Error('正在退出账号。')
    signingOut = accountId
    if (pending) finish(pending)
    try {
      if ((await read()).activeId === accountId) {
        accountLife.abort()
        accountLife = new AbortController()
      }
      let revoked = true
      const record = await credentials.modifyRecord(key, async current => {
        const live = payload(current), row = live.accounts.find(item => item.id === accountId)
        if (!row) throw new Error('所选 ChatGPT 账号已移除。')
        revoked = await revoke(fetcher, row, lifetime.signal).catch(() => false)
        return grant({ ...live, accounts: live.accounts.map(item => item.id === accountId ? { ...item, tokens: null, session: null } : item),
          activeId: live.activeId === accountId ? null : live.activeId })
      })
      observe(payload(record))
      notice = revoked ? '已退出 ChatGPT。' : '已退出本机账号。未确认远程撤销，请在 ChatGPT 的安全与登录设置中断开连接。'
      return await accountState()
    } finally { signingOut = null }
  }
  async function access(signal) {
    lifetime.signal.throwIfAborted()
    signal?.throwIfAborted()
    const data = await read()
    observe(data)
    const row = selected(data)
    if (!row?.tokens || !row.session || signingOut === row.id) return null
    const activeSignal = AbortSignal.any([accountLife.signal, lifetime.signal, ...(signal ? [signal] : [])])
    let tokenSet = row.tokens
    if (tokenSet.expires - Date.now() < 60000) {
      let fresh
      const recorded = await credentials.modifyRecord(key, async current => {
        activeSignal.throwIfAborted()
        const live = payload(current), now = selected(live)
        if (now?.id !== row.id || now?.session !== row.session || !now.tokens || signingOut === now.id) throw disconnected()
        fresh = now.tokens
        if (fresh.expires - Date.now() >= 60000) return undefined
        try {
          // A caller's cancelled generation must not lose an already rotating refresh token.
          const body = await exchange(fetcher, { grant_type: 'refresh_token', refresh_token: fresh.refresh, client_id: now.clientId }, lifetime.signal)
          fresh = tokens(body, fresh)
        } catch (error) {
          if (!error.terminal) throw error
          fresh = null
        }
        return grant({ ...live, activeId: fresh ? live.activeId : null, accounts: live.accounts.map(item => item.id === row.id ? { ...item, tokens: fresh, session: fresh ? item.session : null } : item) })
      })
      observe(payload(recorded))
      if (!fresh) { notice = 'ChatGPT 授权已失效，请重新连接。'; return null }
      tokenSet = fresh
    }
    activeSignal.throwIfAborted()
    return { token: tokenSet.access, session: row.session, signal: activeSignal }
  }
  async function invalidateResponse(response, code) {
    const auth = responseAccounts.get(response)
    if (!auth || (response.status !== 401 && !admissionErrors.has(code))) return
    const record = await credentials.modifyRecord(key, async current => {
      const live = payload(current), row = selected(live)
      if (!row || row.session !== auth.session || row.tokens?.access !== auth.token) return undefined
      // A rejected request pauses use. Keep the renewable grant available for explicit revocation.
      return grant({ ...live, activeId: null, accounts: live.accounts.map(item => item.id === row.id ?
        { ...item, session: null, needsConsent: code === 'chatpass_v2_scope_not_authorized' } : item) })
    })
    const live = payload(record)
    observe(live)
    if (!live.activeId) notice = 'OpenAI 未接受本次授权，已暂停使用此账号。请检查 ChatGPT 登录连接。'
  }
  async function request(path, body, auth) {
    const response = await fetcher(resource + path, { method: body ? 'POST' : 'GET', redirect: 'error', signal: auth.signal,
      headers: { ...attributionHeaders(), Authorization: 'Bearer ' + auth.token, ...(body && { 'Content-Type': 'application/json' }) },
      ...(body && { body: JSON.stringify(body) }) })
    responseAccounts.set(response, auth)
    if (!response.ok) {
      const error = await upstreamError(response)
      await invalidateResponse(response, error.code)
      throw error
    }
    return response
  }
  async function listModels({ signal } = {}) {
    const auth = await access(signal)
    if (!auth) return []
    const response = await request('/models', null, auth)
    const catalog = await response.json()
    auth.signal.throwIfAborted()
    if (!Array.isArray(catalog.models)) throw new Error('无法读取 ChatGPT 模型列表。')
    return catalog.models.filter(row => row?.visibility === 'list' && typeof row.slug === 'string' && row.slug)
  }
  async function respond(body, { signal } = {}) {
    const auth = await access(signal)
    if (!auth) throw disconnected()
    return request('/responses', { ...body, store: false, stream: true }, auth)
  }
  return { accountState, beginLogin, cancelLogin, signOut, listModels, respond, invalidateResponse }
}

export async function registerAccount(ctx, account) {
  const { register } = await import('./account-remote.js')
  return register(ctx, account)
}
