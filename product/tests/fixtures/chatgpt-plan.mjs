// Only loaded by the disposable desktop fixture. Never forward these test tokens to OpenAI.
import assert from 'node:assert/strict'
import { createHash, generateKeyPairSync, sign } from 'node:crypto'
import { appendFileSync, existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

const directory = process.env.DSH_HOME
const marker = join(directory, 'synthetic-plan.json')
assert.ok(existsSync(marker), 'Expected an isolated ChatGPT fixture')
const issuer = 'https://auth.openai.com'
const resource = 'https://api.openai.com'
const client = 'oaiapp_synthetic_dsh'
const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
const json = value => Buffer.from(JSON.stringify(value)).toString('base64url')
const log = message => appendFileSync(join(directory, 'synthetic-events'), message + '\n')
const realFetch = globalThis.fetch
globalThis.fetch = async (url, init) => {
  const { origin, pathname } = new URL(url instanceof Request ? url.url : url)
  if (origin !== issuer && origin !== resource) return realFetch(url, init)
  if (origin === issuer) {
    if (pathname === '/.well-known/jwks.json') return Response.json({ keys: [{...publicKey.export({format:'jwk'}), alg:'RS256', kid:'test'}] })
    if (pathname === '/.well-known/openid-configuration') return Response.json({issuer, revocation_endpoint: issuer + '/revoke'})
    if (pathname === '/revoke') return Response.json({})
    assert.equal(pathname, '/api/accounts/oauth/token')
    const { nonce, challenge } = JSON.parse(readFileSync(marker))
    assert.equal(init.body.get('code'), 'synthetic')
    assert.equal(createHash('sha256').update(init.body.get('code_verifier')).digest('base64url'), challenge)
    const now = Math.floor(Date.now() / 1000)
    const token = json({alg:'RS256', kid:'test'}) + '.' + json({iss:issuer, aud:client, sub:'test-user', nonce, exp:now+3600, iat:now})
    const id_token = token + '.' + sign('RSA-SHA256', Buffer.from(token), privateKey).toString('base64url')
    return Response.json({id_token, token_type:'Bearer', scope:'openid resource.invoke chatgpt.tokens.use.direct',
      expires_in:3600, access_token:'synthetic-access', refresh_token:'synthetic-refresh'})
  }
  assert.equal(new Headers(init.headers).get('Authorization'), 'Bearer synthetic-access')
  if (pathname === '/v1/models') return Response.json({models:[
    {slug:'plan-test', display_name:'Plan test', visibility:'list'},
    {slug:'hidden-test', display_name:'Hidden test', visibility:'hide'},
  ]})
  assert.equal(pathname, '/v1/responses')
  const body = JSON.parse(init.body)
  assert.ok(body.stream && body.store === false && body.model === 'plan-test')
  const latest = body.input.filter(i => i.role === 'user').at(-1)
  const prompt = JSON.stringify(latest || '')
  if (/STOP-(standard|alcor)/.test(prompt)) {
    const label = prompt.match(/STOP-(standard|alcor)/)[0]
    log(label)
    return new Promise((_, reject) => init.signal.addEventListener('abort', () => {
      log('aborted-' + label)
      reject(init.signal.reason)
    }, {once:true}))
  }
  let text = 'Synthetic title', output
  const label = prompt.match(/TOOL-(standard|alcor)/)?.[0]
  if (label && body.tools?.length) {
    const tool = body.tools[0].tools.find(t => t.name === 'skill')
    assert.ok(tool, 'Native DSH skill tool missing')
    const id = 'call_' + label
    const result = body.input.find(item => item.type === 'function_call_output' && item.call_id === id)
    if (result) {
      assert.ok(JSON.stringify(result.output).includes('list_todos'), 'Native skill result missing')
      log('result-' + label)
      text = 'Verified ' + label
    } else {
      output = {type:'function_call',id:'fc_' + label,call_id:id,namespace:'dsh',name:'skill',arguments:'{"name":"alcor-tasks"}'}
    }
  }
  output ??= {type:'message',role:'assistant',id:'msg_test',content:[{type:'output_text',text}]}
  const events = [{type:'response.output_item.added',output_index:0,item:{...output,arguments:'',content:[]}},
    ...(output.type === 'message' ? [{type:'response.output_text.delta',output_index:0,delta:text}] : []),
    {type:'response.completed',response:{status:'completed',output:[output]}}]
  return new Response(events.map(e => 'data: ' + JSON.stringify(e) + '\n\n').join(''), {headers:{'content-type':'text/event-stream'}})
}
export function apply() {}
