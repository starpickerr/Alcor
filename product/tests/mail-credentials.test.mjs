import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import Credentials from '@deepseek-ai/dsh-credentials-local'
import Authorization from '@deepseek-ai/dsh-authorization'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Tools from '@deepseek-ai/dsh-tools'
import * as Executor from '@deepseek-ai/dsh-tool-executor-native'
import MailConnection from '../../packages/stella-mail/src/connection.js'

test('mail owns native credential lifecycle, preserves other mailboxes and revokes stale MCP connections', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'stella-mail-credentials-'))
  t.after(() => rm(dir, { recursive: true, force: true }))
  const filename = join(dir, '.credentials.yaml'); const command = join(dir, 'synthetic-mail')
  await writeFile(command, '#!' + process.execPath + '\n' + `
import {createInterface} from 'node:readline';
const accounts=JSON.parse(process.env.STELLA_EMAIL_CREDENTIAL).accounts;
for await (const line of createInterface({input:process.stdin})) {
 const r=JSON.parse(line); if(r.id===undefined) continue; let result;
 if(r.method==='initialize') result={protocolVersion:r.params.protocolVersion,capabilities:{tools:{}},serverInfo:{name:'synthetic-mail',version:'1'}};
 else if(r.method==='tools/list') result={tools:['list_sources','test_connection','list_folders','get_cursors','fetch_messages'].map(name=>({name,inputSchema:{type:'object',additionalProperties:true}}))};
 else result={content:[],structuredContent:{value:r.params.name==='test_connection'?(accounts.find(a=>a.id===r.params.arguments.payload.id)?.folder==='AuthFailure'?{connected:false,folder_accessible:false,error_code:'auth_failed'}:{connected:true,folder_accessible:true}):accounts.map(a=>({user:a.user}))}};
 process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:r.id,result})+'\\n');
}`, { mode: 0o700 })
  const config = { command, cwd: dir, dataDir: dir }
  const make = async input => {
    const ctx = new Context(); ctx.provide('logger', { warn() {} })
    await ctx.plugin(Credentials, { path: filename, watch: false }).await(); await ctx.plugin(Authorization).await()
    await ctx.plugin(SystemPrompt, {}).await(); await ctx.plugin(Tools).await(); await ctx.plugin(Executor).await()
    await ctx.plugin(MailConnection, input ?? config).await()
    return ctx
  }
  let ctx = await make(); t.after(() => ctx.fiber.dispose())
  const account = { host: 'imap.example.com', user: 'one@example.com', password: 'first-synthetic-secret', port: 993, folder: 'INBOX', enabled: true }
  let state = await ctx.stellaMailConnection.state()
  assert.equal(state.configured, false)
  state = await ctx.stellaMailConnection.save({ expectedRevision: state.revision, account })
  assert.equal(state.connected, true)
  assert.equal(state.accounts.length, 1)
  const first = state.accounts[0]
  assert(!JSON.stringify(state).includes(account.password))
  assert(!JSON.stringify(config).includes(account.password))
  assert.equal((await ctx.stellaMailConnection.check({ id: first.id })).status, 'ready')
  // Every mailbox-reading tool must respect the same account switch, not only fetch_messages.
  state = await ctx.stellaMailConnection.save({ expectedRevision: state.revision, account: { ...first, password: '', enabled: false } })
  for (const [name, args] of [['test_connection', { payload: { id: first.id } }], ['list_folders', { account_id: first.id }], ['get_cursors', { account_id: first.id }], ['fetch_messages', { account_id: first.id }]]) {
    const result = await ctx.tools.execute({ signal: AbortSignal.timeout(5000), calls: [{ type: 'tool-call', id: 'disabled-' + name, name: 'mcp__email__' + name, arguments: JSON.stringify(args) }] })
    assert.equal(result.results[0].result.isError, true)
    assert(JSON.stringify(result).includes('邮箱未开启'))
  }
  state = await ctx.stellaMailConnection.save({ expectedRevision: state.revision, account: { ...first, password: '', enabled: true } })
  assert.equal((await ctx.stellaMailConnection.check({ id: first.id })).status, 'ready')
  for (const name of ['list_folders', 'get_cursors', 'fetch_messages']) {
    const result = await ctx.tools.execute({ signal: AbortSignal.timeout(5000), calls: [{ type: 'tool-call', id: 'enabled-' + name, name: 'mcp__email__' + name, arguments: JSON.stringify({ account_id: first.id }) }] })
    assert.equal(result.results[0].result.isError, false)
  }
  const stale = state.revision
  state = await ctx.stellaMailConnection.save({ expectedRevision: state.revision, account: { ...account, user: 'two@example.com', password: 'second-synthetic-secret', folder:'AuthFailure' } })
  await assert.rejects(ctx.stellaMailConnection.save({ expectedRevision: stale, account }), /已变化/)
  const failed=await ctx.stellaMailConnection.check({id:state.accounts.find(row=>row.user==='two@example.com').id})
  assert.deepEqual(failed,{status:'query_failed',code:'auth_failed'})
  assert(!JSON.stringify(failed).includes('synthetic-secret'))
  state = await ctx.stellaMailConnection.save({ expectedRevision: state.revision, account: { ...first, password: '', folder: 'Archive' } })
  assert.equal(state.accounts.length, 2)
  assert((await readFile(filename, 'utf8')).includes('first-synthetic-secret'))
  await ctx.fiber.dispose(); ctx = await make()
  assert.equal((await ctx.stellaMailConnection.state()).connected, true)
  state = await ctx.stellaMailConnection.state()
  for (const row of [...state.accounts]) state = await ctx.stellaMailConnection.remove({ id: row.id, expectedRevision: state.revision })
  assert.equal(state.connected, false)
  assert(!ctx.tools.executable('mcp__email__list_sources'))
  assert(!(await readFile(filename, 'utf8')).includes('synthetic-secret'))
  state = await ctx.stellaMailConnection.save({ expectedRevision: state.revision, account })
  await ctx.fiber.dispose(); ctx = await make({ ...config, command: join(dir, 'missing-command') })
  assert.equal((await ctx.stellaMailConnection.state()).configured, true)
  assert.equal((await ctx.stellaMailConnection.state()).connected, false)
})


test('mail checks classify timeouts, reject stale success and never expose arbitrary connector text', async () => {
  const subject={active:'saved-credential',generation:1,ctx:{tools:{}}}
  const check=()=>MailConnection.prototype.check.call(subject,{id:'synthetic'})
  subject.ctx.tools.execute=async()=>{throw new DOMException('private-code','TimeoutError')}
  assert.deepEqual(await check(),{status:'query_failed',code:'timeout'})
  const output=value=>({results:[{result:{isError:false,value:{structuredContent:{value}}}}]})
  subject.ctx.tools.execute=async()=>output({connected:true,folder_accessible:false,message:'private-code arbitrary server reply'})
  assert.deepEqual(await check(),{status:'query_failed',code:'folder_denied'})
  subject.ctx.tools.execute=async()=>output({connected:'yes',folder_accessible:'yes'})
  assert.deepEqual(await check(),{status:'query_failed',code:'query_failed'})
  subject.ctx.tools.execute=async()=>output({connected:false,error_code:'private-code'})
  assert.deepEqual(await check(),{status:'query_failed',code:'query_failed'})
  subject.ctx.tools.execute=async()=>{subject.generation++;return output({connected:true,folder_accessible:true})}
  assert.deepEqual(await check(),{status:'disconnected'})
})
