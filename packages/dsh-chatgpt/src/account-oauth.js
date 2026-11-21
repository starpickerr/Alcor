import { createHash, randomBytes } from 'node:crypto'
import { createServer } from 'node:http'
import { once } from 'node:events'
import { upstreamError } from './account-errors.js'
import { createLocalJWKSet, jwtVerify } from 'jose'

export const issuer = 'https://auth.openai.com'
export const resource = 'https://api.openai.com/v1'
const tokenEndpoint = issuer + '/api/accounts/oauth/token'
const direct = 'chatgpt.tokens.use.direct'
const scopes = ['openid', 'profile', 'email', 'offline_access', 'resource.invoke', direct]
const random = () => randomBytes(32).toString('base64url')
async function authFetch(fetch, url, params, signal) {
  const response = await fetch(url, { method: 'POST', redirect: 'error', body: new URLSearchParams(params),
    signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]) })
  if (!response.ok) throw await upstreamError(response)
  return response
}

export async function exchange(fetch, params, signal) {
  return (await authFetch(fetch, tokenEndpoint, { ...params, resource }, signal)).json()
}

export function tokens(body, previous) {
  const granted = (body.scope ?? previous?.scope ?? '').split(/\s+/)
  if (!granted.includes(direct) || !granted.includes('resource.invoke')) return null
  if (String(body.token_type).toLowerCase() !== 'bearer' || typeof body.access_token !== 'string' || !body.access_token ||
      typeof body.refresh_token !== 'string' || !body.refresh_token || !Number.isFinite(body.expires_in) || body.expires_in <= 0) {
    throw new Error('OpenAI 返回的凭据不完整，请重新登录。')
  }
  return { access: body.access_token, refresh: body.refresh_token, expires: Date.now() + body.expires_in * 1000, scope: granted.join(' '),
    idToken: previous?.idToken ?? body.id_token }
}

export async function verifyIdentity(fetch, token, clientId, nonce, signal) {
  const response = await fetch(issuer + '/.well-known/jwks.json', { redirect: 'error', signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]) })
  if (!response.ok) throw new Error('无法验证 OpenAI 登录身份。')
  const { payload } = await jwtVerify(token, createLocalJWKSet(await response.json()), {
    issuer, audience: clientId, algorithms: ['RS256'], clockTolerance: 5, requiredClaims: ['sub', 'iat', 'exp', 'nonce'],
  })
  if (payload.nonce !== nonce || typeof payload.sub !== 'string' || !payload.sub ||
      (Array.isArray(payload.aud) && payload.aud.length > 1 && !payload.azp) ||
      (payload.azp !== undefined && payload.azp !== clientId)) throw new Error('OpenAI 登录身份不匹配。')
  return { subject: payload.sub, email: typeof payload.email === 'string' ? payload.email.slice(0, 320) : '' }
}

export async function revoke(fetch, session, signal) {
  if (!session.tokens) return true
  // Discovery is pinned to the production issuer before sending a refresh token anywhere.
  const discovery = await fetch(issuer + '/.well-known/openid-configuration', { redirect: 'error', signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]) })
  if (!discovery.ok) return false
  const metadata = await discovery.json()
  const url = new URL(metadata.revocation_endpoint)
  if (metadata.issuer !== issuer || url.origin !== issuer || url.username || url.password) return false
  for (let i = 0; i < 2; i++) {
    try {
      const result = await authFetch(fetch, url.href, { token: session.tokens.refresh, token_type_hint: 'refresh_token', client_id: session.clientId }, signal)
      return result.status === 200
    } catch (error) {
      if (i || signal.aborted || (error.status && error.status < 500)) return false
      await new Promise(resolve => setTimeout(resolve, 250))
    }
  }
}

export async function openLogin(attempt, registration, hostId, complete, finish) {
  const state = random(), nonce = random(), verifier = random(), launchPath = '/start/' + random()
  const controller = attempt.controller
  const signal = controller.signal
  let origin, authorization, claimed = false
  const server = createServer(async (req, res) => {
    const reply = (status, message, headers) => {
      res.writeHead(status, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store',
        'Content-Security-Policy': "default-src 'none'", 'Referrer-Policy': 'no-referrer', ...headers })
      res.end(message)
    }
    try {
      if (req.method !== 'GET' || req.headers.host !== new URL(origin).host || !req.url?.startsWith('/')) return reply(400, '无效的登录请求。')
      const url = new URL(req.url, origin)
      if (url.origin !== origin) return reply(400, '无效的登录地址。')
      if (url.pathname === launchPath) return reply(302, '', { Location: authorization.href })
      if (url.pathname !== '/auth/callback') return reply(404, '无此页面。')
      const p = url.searchParams
      if (p.getAll('state').length !== 1 || p.get('state') !== state || claimed || signal.aborted) return reply(400, '登录请求已失效。')
      claimed = true
      if (p.has('error')) {
        reply(400, '登录未完成，可关闭此页并返回 DSH。')
        return finish('未完成授权，可重新登录。')
      }
      const clientId = p.get('client_id') ?? registration?.clientId
      if (!clientId || clientId === 'dynamic_agent_client' || !/^[a-zA-Z0-9._-]{1,256}$/.test(clientId) ||
          (registration && registration.clientId !== clientId) || !p.get('code') || p.getAll('code').length !== 1 || p.getAll('client_id').length > 1) {
        throw new Error('invalid registration')
      }
      await complete({ clientId, nonce, code: p.get('code'), redirect: origin + '/auth/callback', verifier })
      reply(200, '授权流程已完成。请关闭此页，返回 DSH 查看 ChatGPT 账号。')
      finish()
    } catch {
      reply(400, '登录验证未完成。请关闭此页并返回 DSH。')
      finish('未能验证 OpenAI 授权，请重新登录。')
    }
  })
  const listening = once(server, 'listening', { signal })
  const timer = setTimeout(() => finish('登录已超时，请重试。'), 5 * 60 * 1000)
  timer.unref()
  signal.addEventListener('abort', () => { clearTimeout(timer); server.close(); server.closeIdleConnections() }, { once: true })
  server.listen({ host: '127.0.0.1', port: 0, signal })
  await listening
  origin = 'http://127.0.0.1:' + server.address().port
  authorization = new URL(issuer + '/api/accounts/authorize')
  authorization.search = new URLSearchParams({ client_id: registration?.clientId ?? 'dynamic_agent_client',
    ...(registration ? { ...(registration.tokens?.idToken && { id_token_hint: registration.tokens.idToken }),
      ...(registration.email && { login_hint: registration.email }),
      ...(registration.needsConsent && { prompt: 'consent' }) } : { agent_name_hint: 'DSH ChatGPT' }),
    ext_agent_host_id: hostId, response_type: 'code', scope: scopes.join(' '), resource, state, nonce,
    redirect_uri: origin + '/auth/callback', code_challenge_method: 'S256',
    code_challenge: createHash('sha256').update(verifier).digest('base64url'),
  }).toString()
  return origin + launchPath
}
