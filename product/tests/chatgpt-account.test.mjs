import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash, generateKeyPairSync } from 'node:crypto'
import { SignJWT } from 'jose'
import { createAccount } from '../../packages/dsh-chatgpt/src/account.js'
import { methods } from '../../packages/dsh-chatgpt/src/account-contract.js'

const issuer = 'https://auth.openai.com', resource = 'https://api.openai.com/v1'
const scope = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
const { publicKey, privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
const jwk = { ...publicKey.export({ format: 'jwk' }), alg: 'RS256', kid: 'test' }
const sample = { slug: 'sample', display_name: 'Sample', visibility: 'list' }
const response = (data, status = 200) => Response.json(data, { status })

function fixture(t, initial) {
  let record = initial, lock = Promise.resolve()
  const calls = [], events = [], cleanup = []
  const options = { scope, subject: 'user-1', email: 'test@example.invalid', handlers: {} }
  let authorization
  const ctx = {
    credentials: {
      async readRecord() { return structuredClone(record) },
      modifyRecord(_key, mutate) {
        const next = lock.then(async () => {
          const result = await mutate(structuredClone(record))
          if (result) { record = structuredClone(result); await ctx.changed?.(_key) }
          return structuredClone(record)
        })
        lock = next.catch(() => {})
        return next
      },
    },
    on(name, handler) { if (name === 'credentials/record-updated') this.changed = handler },
    emit(...args) { events.push(args) },
    effect(fn) { cleanup.push(fn()) },
  }
  const account = createAccount(ctx, { fetch: async (url, init) => {
    const path = new URL(url).pathname
    assert.equal(new URL(url).origin, path.startsWith('/v1/') ? 'https://api.openai.com' : issuer)
    assert.equal(init.redirect, 'error')
    calls.push({ path, init })
    if (options.handlers[path]) return options.handlers[path](init)
    if (path === '/.well-known/jwks.json') return response({ keys: [jwk] })
    if (path === '/.well-known/openid-configuration') return response({ issuer, revocation_endpoint: issuer + '/api/accounts/oauth/revoke' })
    if (path === '/api/accounts/oauth/revoke') return new Response('')
    if (path === '/v1/models') return response({ models: [sample, { slug: 'hidden', visibility: 'hide' }] })
    if (path === '/v1/responses') return new Response('synthetic stream')
    assert.equal(path, '/api/accounts/oauth/token')
    const form = init.body
    assert.equal(form.get('resource'), resource)
    const refresh = form.get('grant_type') === 'refresh_token'
    if (refresh) {
      assert.equal(form.get('scope'), null)
      assert.equal(form.get('refresh_token'), 'refresh-old')
    } else {
      assert.equal(form.get('redirect_uri'), authorization.get('redirect_uri'))
      assert.equal(createHash('sha256').update(form.get('code_verifier')).digest('base64url'), authorization.get('code_challenge'))
    }
    const idToken = await new SignJWT({ nonce: authorization?.get('nonce'), email: options.email, ...options.claims })
      .setProtectedHeader({ alg: 'RS256', kid: 'test' }).setIssuedAt().setExpirationTime('1h').setSubject(options.subject)
      .setIssuer(issuer).setAudience(options.audience ?? form.get('client_id')).sign(privateKey)
    return response({ access_token: refresh ? 'access-new' : 'access-old', refresh_token: refresh ? 'refresh-new' : 'refresh-old',
      id_token: idToken, token_type: 'Bearer', expires_in: options.expires ?? 3600, ...(refresh ? {} : { scope: options.scope }) })
  } })
  t.after(() => cleanup.forEach(fn => fn()))
  async function start(accountId) {
    const result = await account.beginLogin(accountId ? { accountId } : {})
    methods.beginLogin[1].parse(result)
    const opened = await fetch(result.browserUrl, { redirect: 'manual' })
    assert.equal(opened.status, 302)
    const url = new URL(opened.headers.get('location'))
    assert.equal(url.origin + url.pathname, issuer + '/api/accounts/authorize')
    authorization = url.searchParams
    const callback = async (fields = {}) => {
      const target = new URL(authorization.get('redirect_uri'))
      target.search = new URLSearchParams({ state: authorization.get('state'), code: 'test-code',
        ...(authorization.get('client_id') === 'dynamic_agent_client' ? { client_id: 'oaiapp_fixture' } : {}), ...fields })
      return fetch(target)
    }
    return { params: authorization, callback, browserUrl: result.browserUrl }
  }
  return { account, calls, ctx, events, options, start, record: () => structuredClone(record) }
}

test('owner browser authorization is fresh, loopback-only, PKCE bound and publishes only verified account metadata', async t => {
  const f = fixture(t)
  assert.deepEqual(await f.account.listModels(), [])
  await assert.rejects(f.account.respond({}), /连接 ChatGPT/)
  const a = await f.start()
  assert.equal(a.params.get('agent_name_hint'), 'DSH ChatGPT')
  assert.equal(a.params.get('resource'), resource)
  assert.deepEqual(new Set(a.params.get('scope').split(' ')), new Set(scope.split(' ')))
  assert.match(a.params.get('ext_agent_host_id'), /^urn:uuid:/)
  assert.equal((await a.callback({ state: 'wrong-state' })).status, 400)
  assert.equal((await f.account.accountState()).pending, true)
  assert.equal(f.calls.length, 0)
  assert.equal((await a.callback()).status, 200)
  const state = await f.account.accountState()
  methods.accountState[1].parse(state)
  assert.equal(state.accounts[0].label, 'test@example.invalid')
  assert.equal(state.activeId, state.accounts[0].id)
  assert.doesNotMatch(JSON.stringify(state), /access-old|refresh-old|eyJ|oaiapp_fixture|user-1/)
  assert.deepEqual(f.events, [['llm/models-updated', 'alcor-chatgpt']])
  assert.deepEqual(await f.account.listModels(), [sample])
  await f.account.respond({ model: 'sample', store: true, stream: false })
  const request = f.calls.at(-1)
  assert.equal(request.path, '/v1/responses')
  assert.equal(request.init.headers.Authorization, 'Bearer access-old')
  assert.ok(new Headers(request.init.headers).get('User-Agent'))
  assert.deepEqual(JSON.parse(request.init.body), { model: 'sample', store: false, stream: true })

  const restored = fixture(t, f.record())
  assert.deepEqual(await restored.account.listModels(), [sample])
  assert.deepEqual(restored.events, [])
  const b = await f.start(state.activeId)
  assert.equal(b.params.get('client_id'), 'oaiapp_fixture')
  assert.equal(b.params.get('agent_name_hint'), null)
  assert.equal(b.params.get('ext_agent_host_id'), a.params.get('ext_agent_host_id'))
  assert.notEqual(b.params.get('state'), a.params.get('state'))
  assert.ok(b.params.get('id_token_hint'))
  f.options.subject = 'other-user'
  assert.equal((await b.callback()).status, 400)
  assert.equal((await f.account.accountState()).activeId, state.activeId)
  assert.equal(f.record().payload.accounts[0].subject, 'user-1')
})

test('a declined, cancelled or unverified attempt never authorizes inference', async t => {
  const f = fixture(t)
  const first = await f.start()
  await assert.rejects(f.account.beginLogin(), /正在进行/)
  await f.account.cancelLogin()
  await assert.rejects(fetch(first.browserUrl))
  const denied = await f.start()
  assert.equal((await denied.callback({ error: 'access_denied' })).status, 400)
  assert.equal(f.calls.length, 0)
  const mismatched = await f.start()
  f.options.claims = { nonce: 'wrong' }
  assert.equal((await mismatched.callback()).status, 400)
  assert.deepEqual((await f.account.accountState()).accounts, [])
  f.options.claims = {}
  f.options.audience = 'wrong-app'
  assert.equal((await (await f.start()).callback()).status, 400)
  f.options.audience = undefined
  f.options.scope = 'openid email'
  assert.equal((await (await f.start()).callback()).status, 200)
  const state = await f.account.accountState()
  assert.equal(state.activeId, null)
  assert.equal(state.accounts[0].connected, false)
  assert.deepEqual(await f.account.listModels(), [])
  const again = await f.start(state.accounts[0].id)
  assert.equal(again.params.get('prompt'), 'consent')
})

test('rotating refresh is serialized and temporary upstream failures preserve the account', async t => {
  const f = fixture(t)
  f.options.expires = 1
  await (await f.start()).callback()
  f.options.expires = 3600
  assert.deepEqual(await Promise.all([f.account.listModels(), f.account.listModels()]), [[sample], [sample]])
  const refreshes = f.calls.filter(call => call.init.body?.get?.('grant_type') === 'refresh_token')
  assert.equal(refreshes.length, 1)
  assert.equal(f.record().payload.accounts[0].tokens.refresh, 'refresh-new')
  assert.equal(f.calls.at(-1).init.headers.Authorization, 'Bearer access-new')
  f.options.handlers['/v1/responses'] = () => response({ error: { code: 'subscription_sharing_usage_limit_exceeded', message: 'secret' } }, 429)
  await assert.rejects(f.account.respond({}), error => error.status === 429 && !error.message.includes('secret'))
  assert.equal((await f.account.accountState()).accounts[0].connected, true)
})

test('an upstream authorization rejection pauses use, aborts in-flight work, refreshes native models and allows reconnection', async t => {
  const f = fixture(t)
  await (await f.start()).callback()
  const active = (await f.account.accountState()).activeId
  await f.account.respond({})
  const streamSignal = f.calls.at(-1).init.signal
  f.options.handlers['/v1/models'] = () => response({ detail: 'private upstream text' }, 401)
  await assert.rejects(f.account.listModels(), error => error.status === 401 && !error.message.includes('private'))
  assert.ok(streamSignal.aborted)
  assert.equal((await f.account.accountState()).activeId, null)
  assert.deepEqual(await f.account.listModels(), [])
  await assert.rejects(f.account.respond({}), /连接 ChatGPT/)
  assert.equal(f.events.length, 2)
  const again = await f.start(active)
  assert.ok(again.params.get('id_token_hint'))
  await again.callback()
  assert.equal((await f.account.accountState()).activeId, active)
})

test('terminal refresh failure invalidates only its session, and sign-out confirms or reports revocation', async t => {
  const f = fixture(t)
  f.options.expires = 1
  await (await f.start()).callback()
  const active = (await f.account.accountState()).activeId
  f.options.handlers['/api/accounts/oauth/token'] = () => response({ error: 'invalid_grant' }, 400)
  assert.deepEqual(await f.account.listModels(), [])
  assert.equal((await f.account.accountState()).accounts[0].connected, false)
  delete f.options.handlers['/api/accounts/oauth/token']
  f.options.expires = 3600
  await (await f.start(active)).callback()
  await f.account.respond({})
  const signal = f.calls.at(-1).init.signal
  const signedOut = await f.account.signOut({ accountId: active })
  assert.ok(signal.aborted)
  const revoked = f.calls.find(call => call.path === '/api/accounts/oauth/revoke')
  assert.deepEqual(Object.fromEntries(revoked.init.body), { token: 'refresh-old', token_type_hint: 'refresh_token', client_id: 'oaiapp_fixture' })
  assert.equal(signedOut.activeId, null)
  assert.doesNotMatch(JSON.stringify(f.record()), /access-old|refresh-old|eyJ/)
  await (await f.start(active)).callback()
  f.options.handlers['/api/accounts/oauth/revoke'] = () => response({}, 503)
  const local = await f.account.signOut({ accountId: active })
  assert.match(local.notice, /未确认远程撤销/)
  assert.equal(local.accounts[0].connected, false)
})

test('two workspaces with the same email keep distinct grants and signing out the other leaves the active stream running', async t => {
  const f = fixture(t)
  await (await f.start()).callback()
  const first = (await f.account.accountState()).activeId
  await (await f.start()).callback({ client_id: 'oaiapp_second' })
  const second = (await f.account.accountState()).activeId
  assert.notEqual(first, second)
  assert.equal((await f.account.accountState()).accounts.length, 2)
  await f.account.respond({})
  const signal = f.calls.at(-1).init.signal
  await f.account.signOut({ accountId: first })
  assert.equal(signal.aborted, false)
  assert.equal((await f.account.accountState()).activeId, second)
  assert.deepEqual(await f.account.listModels(), [sample])
  const returning = await f.start(first)
  assert.equal((await returning.callback({ client_id: 'oaiapp_second' })).status, 400)
  assert.equal((await f.account.accountState()).activeId, second)
})

test('stream admission errors pause only the exact session that made the request and preserve explicit revocation', async t => {
  const f = fixture(t)
  await (await f.start()).callback()
  const old = await f.account.respond({})
  await (await f.start()).callback({ client_id: 'oaiapp_second' })
  const active = (await f.account.accountState()).activeId
  await f.account.invalidateResponse(old, 'subscription_sharing_invalid_user')
  assert.equal((await f.account.accountState()).activeId, active)
  const live = await f.account.respond({})
  await f.account.invalidateResponse(live, 'subscription_sharing_usage_limit_exceeded')
  assert.equal((await f.account.accountState()).activeId, active)
  await f.account.invalidateResponse(live, 'chatpass_v2_invalid_authorization_context')
  const state = await f.account.accountState()
  assert.equal(state.activeId, null)
  const row = state.accounts.find(r => r.id === active)
  assert.equal(row.connected, false)
  assert.equal(row.canSignOut, true)
  assert.deepEqual(await f.account.listModels(), [])
  await f.account.signOut({ accountId: active })
  assert.equal(f.calls.at(-1).init.body.get('client_id'), 'oaiapp_second')
  assert.equal(f.calls.at(-1).init.body.get('token'), 'refresh-old')
})
