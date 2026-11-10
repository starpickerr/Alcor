// Explicit integration check: official DSH host + isolated Profile + synthetic QQ/model.
import http from 'node:http'
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, mkdirSync, existsSync, unlinkSync } from 'node:fs'
import { createRequire } from 'node:module'
import { spawn } from 'node:child_process'
import { join, resolve } from 'node:path'
import { randomUUID } from 'node:crypto'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { parse } from 'yaml'
import { Store } from '../../packages/alcor-secretary/src/memory/engine/store.js'
const root = resolve(process.env.ALCOR_ASSISTANT_CHECK_ROOT ?? '/private/tmp/alcor-assistant-implementation')
assert.ok(root.startsWith('/private/tmp/alcor-assistant-'), 'Use a dedicated synthetic test directory')
const app = process.env.ALCOR_OFFICIAL_APP ?? join(homedir(), 'Applications/DeepSeek Harness.app')
const profile = join(root, 'official-dsh/profiles/spaces-check'), require = createRequire(join(profile, 'package.json')), WebSocket = require('ws')
const restart = process.argv.includes('--restart'), keep = process.argv.includes('--keep')
let plan = { reply: 'READY' }, serial = 0, socket, log = '', controlPort
const requests = [], outgoing = [], checks = [], events = [], platformMessages=[]
const model = http.createServer(async (req, res) => {
  let raw = ''; for await (const chunk of req) raw += chunk
  const body = JSON.parse(raw); requests.push(body)
  assert.equal(req.headers.authorization, 'Bearer synthetic-test-key')
  const firstText=JSON.stringify(body.messages[0])
  const groupGate=firstText.includes('只输出 JOIN 或 PASS')
  const internal=firstText.includes('ALCOR_OBSERVE') || firstText.includes('ALCOR_REVIEW')
  const draft=body.messages[0].content
  const prompt=typeof draft==='string'?draft:draft[0].text
  const fixture=internal ? JSON.parse(prompt.split('以下是对话资料：\n').at(-1)) : {}
  const choice=firstText.includes('ALCOR_OBSERVE') ?
    (fixture.speech?.includes('I prefer piano') ? {mood:'高兴',preference:{topic:'music',quote:'I prefer piano'},explore:null} : {mood:null,preference:null,explore:null}) :
    fixture.basis==='ASK_PROGRESS' && fixture.updates?.some(row=>row.text.includes('already resolved')) ? {action:'cancel'} : {action:'send',text:fixture.draft}
  const choices = groupGate ? [{delta:{role:'assistant',content:'PASS'},finish_reason:'stop'}] : internal ? [{delta:{role:'assistant',content:JSON.stringify(choice)},finish_reason:'stop'}] : plan.tool && plan.calls++ === 0 ? [
    { delta: { role: 'assistant' }, finish_reason: null },
    { delta: { tool_calls: [{ index: 0, id: 'test-' + serial, type: 'function', function: { name: plan.tool, arguments: JSON.stringify(plan.args) } }] }, finish_reason: null },
    { delta: {}, finish_reason: 'tool_calls' },
  ] : [{ delta: { role: 'assistant', content: plan.reply }, finish_reason: null }, { delta: {}, finish_reason: 'stop' }]
  res.setHeader('Content-Type', 'text/event-stream')
  for (const choice of choices) res.write('data: ' + JSON.stringify({ id: 'synthetic', choices: [{ index: 0, ...choice }] }) + '\n\n')
  res.end('data: [DONE]\n\n')
})
await new Promise(resolve => model.listen(0, '127.0.0.1', resolve))
const config = parse(readFileSync(join(profile, 'cordis.patch.yml'), 'utf8')).filter(entry => !['agent-default-model', 'llm-pi-ai'].includes(entry.id))
const controlEntry = config.flatMap(entry => entry.insert ?? []).find(entry => entry.id === 'synthetic-control')
if (!controlEntry) throw Error('Initialize the synthetic Profile with the companion assistant-host-control fixture')
controlEntry.name = fileURLToPath(new URL('assistant-host-control.mjs', import.meta.url))
config.push({ id: 'agent-default-model', config: { provider: 'synthetic', model: 'test' } },
  { id: 'llm-pi-ai', config: { providers: { synthetic: { api: 'openai-completions', apiKeyEnv: 'ALCOR_SYNTHETIC_KEY', baseURL: 'http://127.0.0.1:' + model.address().port + '/v1',
    models: ['test','test-b'].map(id => ({ id, name:id, contextWindow:32000, maxTokens:2048, input:['text'] })) } } } })
writeFileSync(join(profile, 'cordis.patch.yml'), JSON.stringify(config, null, 2) + '\n')
writeFileSync(join(root, 'official-dsh/.credentials.yaml'), 'version: 1\nrefs:\n  ALCOR_SYNTHETIC_KEY: synthetic-test-key\n', { mode: 0o600 })
mkdirSync(join(root, 'home'), { recursive: true })
mkdirSync(join(root, 'workspaces/qq/groups/30001'), { recursive: true })
for (const path of ['personal', 'groups/30001']) {
  const store = new Store({ dataDir: join(profile, 'alcor/memory', path) }); store.init({ migrateV1: false })
  if (store.readOnly) throw Error('Synthetic Profile is still running')
  store.saveMeta({ settings: { autoCompile: false, autoCompileThreshold: 999, compileIdleMinutes: 999 } }); store.releaseLock()
}
// Reset only this fixture's opt-ins and send ledger while the isolated host is stopped.
for(const folder of ['personal','groups/30001']){
  const file=join(profile,'alcor/memory',folder,'alcor-authority.json')
  if(existsSync(file)){
    const value=JSON.parse(readFileSync(file,'utf8'))
    Object.assign(value,{initiative:false,lastDismissed:0,followups:[]})
    writeFileSync(file,JSON.stringify(value),{mode:0o600})
  }
}
const channelFile=join(root,'official-dsh/storages/alcor_channels.json')
if(existsSync(channelFile)){
  const value=JSON.parse(readFileSync(channelFile,'utf8')),state=value.tables?.delivery?.state
  if(state){state.grants=[];state.receipts=[];writeFileSync(channelFile,JSON.stringify(value),{mode:0o600})}
}
if (existsSync(join(root, 'control-ready.json'))) unlinkSync(join(root, 'control-ready.json'))
// Choose business hours in this isolated host independently of when the test is run.
const offset = new Date().getUTCHours() - 12
const testZone = offset ? 'Etc/GMT' + (offset > 0 ? '+' : '') + offset : 'UTC'
const env = { TZ:testZone, PATH: process.env.PATH, LANG: 'en_US.UTF-8', HOME: join(root, 'home'), DSH_HOME: join(root, 'official-dsh'), SHELL: '/bin/zsh', DSH_TELEMETRY_DISABLED: '1' }
const host = spawn(join(app, 'Contents/Resources/runtime/cli/bin/dsh'), ['--profile', 'spaces-check', '--no-open', '--port', '0'], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] })
const record = chunk => { log += chunk; writeFileSync(join(root, 'official-check.log'), log.replace(/token=\S+/g, 'token=[redacted]'))
  const url = /dsh web: (http:\S+)/.exec(log)?.[1]; if (url) writeFileSync(join(root, 'browser-url.json'), JSON.stringify({ url }), { mode: 0o600 }) }
host.stdout.on('data', record); host.stderr.on('data', record)
const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
async function until(check, label, timeout = 30000) {
  const start = Date.now()
  while (Date.now() - start < timeout) { if (host.exitCode !== null) throw Error('Host exited: ' + log.slice(-3000)); if (await check()) return; await pause(100) }
  throw Error('Timed out: ' + label)
}
async function control(path, body = {}) {
  const response = await fetch('http://127.0.0.1:' + controlPort + path, { method: 'POST', headers: { authorization: 'Bearer synthetic-control-only', 'content-type': 'application/json' }, body: JSON.stringify(body) })
  const data = await response.json(); if (!response.ok || !data.ok) throw Error(data.error || JSON.stringify(data)); return data.value
}
const rpc = (namespace, method, args = {}) => control('/rpc', { namespace, method, args })
function message(user, kind, content, group = 30001) {
  return { post_type: 'message', message_type: kind, sub_type: kind === 'private' ? 'friend' : 'normal', self_id: 20001, user_id: user, message_id: ++serial, time: Math.floor(Date.now() / 1000),
    raw_message: content, message: [...kind === 'group' ? [{ type: 'at', data: { qq: '20001' } }] : [], { type: 'text', data: { text: content } }], sender: { user_id: user, nickname: user===10001?'林舟':'阿棠' }, ...kind === 'group' ? { group_id: group } : {} }
}
async function send(user, kind, next) {
  plan = { calls: 0, ...next }; const before = requests.length, count = outgoing.length
  const incoming=message(user,kind,next.text ?? next.reply)
  if(kind==='group')platformMessages.push({...incoming,message_seq:serial})
  socket.send(JSON.stringify(incoming))
  await until(() => outgoing.slice(count).some(row => JSON.stringify(row).includes(next.reply)), next.reply)
  return requests.slice(before)
}
try {
  await until(() => existsSync(join(root, 'control-ready.json')) && log.includes('bridge ready ('), 'host and mapping')
  controlPort = JSON.parse(readFileSync(join(root, 'control-ready.json'))).port
  socket = new WebSocket('ws://127.0.0.1:36433/ws', { headers: { authorization: 'Bearer synthetic-only' } })
  socket.on('message', raw => {
    const data = JSON.parse(raw); if (data.params) { events.push(data.action); if (['send_msg', 'send_group_msg', 'send_private_msg'].includes(data.action)) outgoing.push(data.params) }
    if(data.action?.startsWith('send_') && data.params?.group_id)platformMessages.push({group_id:data.params.group_id,user_id:20001,time:Math.floor(Date.now()/1000),message_seq:++serial,message:Array.isArray(data.params.message)?data.params.message:[{type:'text',data:{text:String(data.params.message)}}]})
    const value = data.action==='get_group_msg_history' ? {messages:platformMessages.filter(row=>row.group_id===data.params.group_id && (data.params.message_seq==null || row.message_seq<data.params.message_seq)).slice(-80)} : data.action?.startsWith('send_') ? { message_id: ++serial } : data.action === 'get_group_info' ? { group_name: '纸样讨论组', member_count:3 } : data.action === 'get_stranger_info' ? { nickname: '林舟' } : {}
    socket.send(JSON.stringify({ status: 'ok', retcode: 0, data: value, echo: data.echo }))
  })
  await new Promise((resolve, reject) => { socket.once('open', resolve); socket.once('error', reject) })
  socket.send(JSON.stringify({ post_type: 'meta_event', meta_event_type: 'lifecycle', sub_type: 'connect', self_id: 20001, time: Math.floor(Date.now() / 1000) }))
  await rpc('alcorSecretary', 'updateSoul', { request: { soul: 'SINGLE_PUBLIC_ALCOR_SOUL_CANARY' } })
  let body = await send(10001, 'private', { reply: 'PRIVATE_READY', tool: 'alcor_memory_remember', args: { content: 'PRIVATE_MEMORY_CANARY' } })
  assert.ok(body.some(row => JSON.stringify(row).includes('SINGLE_PUBLIC_ALCOR_SOUL_CANARY')))
  const memory = await rpc('alcorSecretary', 'memoryState', { request: {} })
  assert.ok(memory.entries.some(row => row.content === 'PRIVATE_MEMORY_CANARY'), 'Private memory tool failed')
  checks.push('PASS private QQ uses the built-in Alcor memory and public SOUL')
  const identity = await rpc('alcorSecretary','exportIdentity',{ request:{} })
  await rpc('session','selectModel',{ request:{ sessionId:'onebot-private-10001', provider:'synthetic', model:'test-b' } })
  const afterSwitch = await send(10001,'private',{reply:'SECOND_MODEL_READY',text:'PRIVATE_MEMORY_CANARY'})
  assert.ok(afterSwitch.some(row=>row.model==='test-b' && JSON.stringify(row.messages).includes(identity.soul)))
  assert.deepEqual(await rpc('alcorSecretary','exportIdentity',{request:{}}),identity)
  assert.ok(afterSwitch.some(row=>row.messages.some(m=>JSON.stringify(m).includes('以下是历史资料') && JSON.stringify(m).includes('PRIVATE_MEMORY_CANARY'))),'Authorized QQ owner should receive private context')
  checks.push('PASS model switch uses the same persisted identity and public persona')
  await rpc('alcorSecretary','allowInitiative',{request:{enabled:true}})
    await rpc('alcorSecretary','allowDesktopDelivery',{request:{enabled:true}})
  const fact=memory.entries.find(row=>row.content==='PRIVATE_MEMORY_CANARY')
  const follow = await send(10001,'private',{reply:'DESKTOP_FOLLOWUP_PLANNED', tool:'alcor_memory_followup',args:{
    evidence:fact.id,content:'How did the first paragraph go?',dueAt:new Date(Date.now()+3600000).toISOString(),
  }})
  assert.ok(follow.some(row=>row.messages.some(item=>item.role==='tool' && JSON.stringify(item).includes(fact.id))))
  await rpc('alcorSecretary','allowInitiative',{request:{enabled:false}})
  assert.equal(await rpc('alcorSecretary','nextFollowup',{request:{}}),null)
  checks.push('PASS opted-in personal follow-up can be planned by Alcor and revoked by its owner')
  const tasks=(method,args)=>rpc('alcorOrganizer','business',{request:{domain:'tasks',method,arguments:args}})
  const today=new Date().toISOString().slice(0,10)
  const taskTitle='Synthetic due task '+randomUUID()
  const task=await tasks('create_todo',{payload:{title:taskTitle,due_on:today,timezone:'UTC',priority:3}})
  try {
    await rpc('alcorSecretary','allowInitiative',{request:{enabled:true}})
    await rpc('alcorSecretary','allowDesktopDelivery',{request:{enabled:true}})
    assert.equal(await rpc('alcorSecretary','nextFollowup',{request:{}}),null)
    checks.push('PASS a due task alone does not create a personal follow-up')
  } finally {
    await rpc('alcorSecretary','allowInitiative',{request:{enabled:false}})
    await tasks('complete_todo',{todo_id:task.id,completed:true})
  }
  body = await send(10002, 'group', { reply: 'GROUP_READY', tool: 'qq_group_remember', args: { content: 'GROUP_MEMORY_CANARY' } })
  assert.ok(body.some(row => JSON.stringify(row).includes('SINGLE_PUBLIC_ALCOR_SOUL_CANARY')))
  assert.ok(!JSON.stringify(body).includes('PRIVATE_MEMORY_CANARY'), 'Private memory leaked to group')
  body = await send(10001, 'group', { reply: 'OWNER_GROUP_READY', tool: 'qq_group_recall', args: { query: 'GROUP_MEMORY_CANARY' } })
  assert.ok(body.some(row => row.messages.some(message => message.role === 'tool' && JSON.stringify(message).includes('GROUP_MEMORY_CANARY'))), 'Group memory did not persist')
  checks.push('PASS group uses the same SOUL and its own attributed memory')
  body = await send(10001, 'private', { reply: 'CROSS_ENTRY_READY', tool: 'alcor_memory_recall', args: { query: 'GROUP_MEMORY_CANARY' } })
  assert.ok(body.some(row => row.messages.some(message => message.role === 'tool' && JSON.stringify(message).includes('GROUP_MEMORY_CANARY'))), 'Owner could not recall their group experience')
  checks.push('PASS owner private conversation recalls a group they participated in')
  const info = await rpc('alcorSecretary', 'spaces', { request: {} })
  assert.equal(info.qq?.['onebot-group-30001'], '纸样讨论组')
  const privateChat=await rpc('session','create',{request:{workspaceId:info.defaults.alcor,agentPreset:'alcor'}})
  for (const [text,expectMemory] of [['Hello from the synthetic observatory',false],['PRIVATE_MEMORY_CANARY',true]]) {
    const from=requests.length
    plan={calls:0,reply:'SYNTHETIC_CHAT_READY'}
    await rpc('session','prompt',{request:{sessionId:privateChat.sessionId,requestId:randomUUID(),content:[{type:'text',text}],mode:'queue'}})
    await until(()=>requests.length>from,'fresh desktop memory query')
    const supplied=requests.slice(from).some(row=>row.messages.some(m=>JSON.stringify(m).includes('以下是历史资料') && JSON.stringify(m).includes('PRIVATE_MEMORY_CANARY')))
    assert.equal(supplied,expectMemory)
  }
  checks.push('PASS new private chat receives relevant experiences without unrelated memory dumps')
  const projectDir=join(root,'shared-fixture-'+randomUUID())
  mkdirSync(projectDir,{recursive:true})
  writeFileSync(join(projectDir,'sample.txt'),'WORKSPACE_FILE_PROOF')
  const groupFolder=(await control('/state')).workspaces.find(row=>row.sessionIds.includes('onebot-group-30001'))
  assert.ok(groupFolder)
  if (!groupFolder.sessionIds.some(id=>!id.startsWith('onebot-'))) {
    await rpc('session','create',{request:{workspaceId:groupFolder.id,agentPreset:'alcor'}})
  }
  const shared=await control('/shared-project',{path:projectDir})
  const projectEdit=(space,title)=>rpc('alcorSecretary','editProject',{request:{action:'create',workspaceId:shared,space,title}})
  await projectEdit('alcor','助手项目')
  await projectEdit('dsh','工作台项目')
  const create=(agentPreset)=>rpc('session','create',{request:{workspaceId:shared,agentPreset}})
  const a=await create('alcor'), d=await create('standard')
  for(const [sessionId,space] of [[a.sessionId,'alcor'],[d.sessionId,'dsh']]) await rpc('alcorSecretary','claimSpace',{request:{space,sessionId}})
  await assert.rejects(rpc('alcorSecretary','claimSpace',{request:{sessionId:d.sessionId,space:'alcor'}}))
  const mark='shared-search-'+randomUUID()
  plan={calls:0,reply:'READ_COMPLETE',tool:'read',args:{file_path:'sample.txt'}}
  const beforeRead=requests.length
  await rpc('session','prompt',{request:{sessionId:a.sessionId,requestId:randomUUID(),content:[{type:'text',text:mark}],mode:'queue'}})
  await until(()=>requests.slice(beforeRead).some(row=>row.messages.some(m=>m.role==='tool' && JSON.stringify(m).includes('WORKSPACE_FILE_PROOF'))),'authorized workspace read',12000).catch(error=>{const after=requests.slice(beforeRead); throw Error(error.message+' offered='+after[0]?.tools?.map(x=>x.function?.name).join(',')+' toolResults='+JSON.stringify(after.flatMap(x=>x.messages.filter(m=>m.role==='tool'))).slice(0,1200))})
  plan={calls:0,reply:'DSH_PROJECT_READY'}
  await rpc('session','prompt',{request:{sessionId:d.sessionId,requestId:randomUUID(),content:[{type:'text',text:mark}],mode:'queue'}})
  await until(()=>requests.some(row=>row.messages.some(m=>m.role==='user' && JSON.stringify(m).includes(mark)) && !JSON.stringify(row).includes('SINGLE_PUBLIC_ALCOR_SOUL_CANARY')),'workbench project',10000)
  for(const [space,want] of [['alcor',a.sessionId],['dsh',d.sessionId]]) {
    await until(async()=>{const res=await rpc('alcorSecretary','searchSpace',{request:{query:mark,space,sessionIds:[a.sessionId,d.sessionId,'onebot-private-10001']}})
      assert.ok(res.items.every(row=>row.sessionId===want));return res.items.length===1},'scoped native search',10000)
  }
  const sharedState=await control('/state')
  assert.deepEqual(new Set(sharedState.workspaces.find(row=>row.id===shared).sessionIds),new Set([a.sessionId,d.sessionId]))
  checks.push('PASS a shared working directory has isolated projects, a native file read in its original chat, and host-filtered search')
  const work = await rpc('session', 'create', { request: { workspaceId: info.defaults.dsh, agentPreset: 'standard' } })
  plan = { calls: 0, reply: 'WORKBENCH_READY' }; const before = requests.length
  await rpc('session', 'prompt', { request: { sessionId: work.sessionId, requestId: randomUUID(), content: [{ type: 'text', text: 'DSH workbench synthetic prompt' }], mode: 'queue' } })
  await until(() => requests.length > before, 'workbench model')
  assert.ok(!JSON.stringify(requests.slice(before)).includes('PRIVATE_MEMORY_CANARY'))
  assert.ok(!JSON.stringify(requests.slice(before)).includes('SINGLE_PUBLIC_ALCOR_SOUL_CANARY'))
  checks.push('PASS DSH workbench has neither Alcor persona nor private memory')
  plan = {calls:0,reply:'SCHEDULE_READY',tool:'read',args:{file_path:join(projectDir,'sample.txt')}}
  const marker='Synthetic scheduled turn '+randomUUID()
  await rpc('alcorOrganizer','automation',{request:{scope:'alcor',action:'create',request:{title:'Synthetic privacy check',prompt:marker,timing:{kind:'after',after_seconds:2}}}})
  const scheduledRequest=()=>requests.find(row=>row.messages.some(message=>JSON.stringify(message).includes(marker)))
  await until(scheduledRequest,'native schedule reaches model',30000)
  assert.ok(JSON.stringify(scheduledRequest()).includes('SINGLE_PUBLIC_ALCOR_SOUL_CANARY'))
  assert.ok(!JSON.stringify(scheduledRequest()).includes('PRIVATE_MEMORY_CANARY'))
  const scheduledToolResult=()=>requests.filter(row=>row.messages.some(m=>JSON.stringify(m).includes(marker))).flatMap(row=>row.messages.filter(m=>m.role==='tool'))
  await until(()=>scheduledToolResult().length,'scheduled file tool result',12000)
  assert.match(JSON.stringify(scheduledToolResult()),/项目文件读取仅供主人发起的 Alcor 桌面对话使用/)
  assert.ok(!JSON.stringify(scheduledToolResult()).includes('WORKSPACE_FILE_PROOF'))
  checks.push('PASS scheduled Alcor turn uses its public identity without private memories or desktop file access')
  const due=await tasks('create_todo',{payload:{title:'SCHEDULE_TASK_CANARY',due_on:today,timezone:'UTC',notes:'SECRET_NOTE_MUST_STAY_PRIVATE'}})
  const trigger='Scheduled review '+randomUUID()
  const job=await rpc('alcorOrganizer','automation',{request:{scope:'alcor',action:'create',request:{
    title:'Synthetic authorized task review',prompt:trigger,timing:{kind:'after',after_seconds:6},target:{kind:'new'}}}})
  try {
    plan={calls:0,reply:'SCOPED_REVIEW_DONE',tool:'alcor_review_tasks',args:{}}
    const reviews=()=>requests.filter(row=>row.messages.some(m=>JSON.stringify(m).includes(trigger)))
    await until(()=>reviews().some(row=>row.messages.some(m=>m.role==='tool')), 'native task review tool result',30000)
    const output=reviews().flatMap(row=>row.messages.filter(m=>m.role==='tool')).map(m=>JSON.stringify(m)).join(' ')
    assert.ok(output.includes('事务回顾仅供用户') && !output.includes('SCHEDULE_TASK_CANARY'))
    checks.push('PASS a DSH automation cannot read personal tasks via retired review grants')
  } finally {
    await rpc('alcorSecretary','allowInitiative',{request:{enabled:false}})
    await tasks('complete_todo',{todo_id:due.id,completed:true})
  }
  const state = await control('/state'), ids = state.sessions.map(row => row.id).filter(id => id.startsWith('onebot-'))
  const count = outgoing.length, modelCount = requests.length
  for (const sessionId of ids) await rpc('session', 'prompt', { request: { sessionId, requestId: randomUUID(), content: [{ type: 'text', text: 'DESKTOP_MUST_NOT_SEND' }], mode: 'queue' } })
  await pause(1200)
  assert.equal(outgoing.length, count, 'Desktop triggered a QQ message')
  assert.equal(requests.length, modelCount, 'Desktop QQ history started a model turn')
  checks.push('PASS desktop QQ input is blocked before model and emits no QQ reply')
  const ignored=message(10002,'group','PLATFORM_SILENT_HISTORY')
  ignored.message=ignored.message.filter(segment=>segment.type!=='at')
  ignored.raw_message='PLATFORM_SILENT_HISTORY'
  platformMessages.push({...ignored,message_seq:serial})
  const countBeforeHistory=outgoing.length
  socket.send(JSON.stringify(ignored))
  await pause(2500)
  const fullHistory=await rpc('alcorSecretary','channelHistory',{request:{sessionId:'onebot-group-30001'}})
  assert.ok(fullHistory.messages.some(row=>row.text.includes('PLATFORM_SILENT_HISTORY')))
  assert.equal(outgoing.length,countBeforeHistory,'Reading ignored group messages must not send replies')
  checks.push('PASS unaddressed group messages are recorded and visible even when the model chooses silence')
  const groupInfo=await rpc('alcorSecretary','qqConversation',{request:{sessionId:'onebot-group-30001'}})
  assert.equal(groupInfo.name,'纸样讨论组'); assert.equal(groupInfo.members,3);assert.equal(groupInfo.online,true)
  const person=await rpc('alcorSecretary','qqConversation',{request:{sessionId:'onebot-private-10001'}})
  assert.equal(person.name,'林舟');assert.equal(person.admin,true)
  const actionCount=events.length
  assert.equal(await rpc('alcorSecretary','qqConversation',{request:{sessionId:'onebot-group-99999'}}),null)
  assert.equal(events.length,actionCount);assert.equal(outgoing.length,count)
  checks.push('PASS the read-only channel provides actual private and group attributes only for known sessions')
  const read = await rpc('session', 'events', { request: { address: { kind: 'session', sessionId: ids.find(id => id.startsWith('onebot-group-')) }, maxMessages: 30 } })
  assert.ok(read.records.some(row => row.type === 'event' && row.event.type === 'user/message'))
  checks.push('PASS QQ history is available through the passive official history API')
  await send(10001,'private',{text:'周末要带哪两样材料？',reply:'一张硬纸板和一把直尺。我们先试尺寸，再决定要不要打样。'})
  await send(10001,'private',{text:'好，直尺我已经放进包里。',reply:'那就只剩硬纸板了。周末见。'})
  await send(10002,'group',{text:'展台的纸样做好了吗？',reply:'外壳的纸样已经剪好了。周末可以一起试握持距离。'})
  await send(10001,'private',{text:'记住，我们周末先试外壳。',reply:'已记住周末的试样安排。',tool:'alcor_memory_remember',args:{content:'周末一起试展台外壳的纸样，先量尺寸，再摸握持的位置。'}})
  await send(10001,'private',{text:'记住这个合作约定。',reply:'会保留这个约定。',tool:'alcor_memory_remember',args:{content:'我们约定，每次讨论动手项目时先把目标和手边的材料说清楚。意见不一致时，可以提出不同判断，说明理由，再一起选择一个成本低、能看见结果的小实验。完成实验后记录具体观察，区分共同经历、助手自己的习惯和用户明确说过的偏好。如果观察推翻了原来的判断，就及时更正，并保留更正的原因。讨论结束时只需要确认接下来的一件小事，避免一次安排太多。'}})
  await rpc('alcorSecretary','allowInitiative',{request:{enabled:true}})
  await rpc('alcorSecretary','allowChannelDelivery',{request:{sessionId:'onebot-private-10001',enabled:true}})
  await rpc('alcorSecretary','allowChannelDelivery',{request:{sessionId:'onebot-group-30001',enabled:true}})
  const groupNote=await control('/delivery-plan',{scope:'group:30001',content:'GROUP_PROACTIVE_DELIVERY'})
  const groupPlan={evidence:groupNote.evidence,content:'GROUP_PROACTIVE_DELIVERY',dueAt:new Date(Date.now()+300000).toISOString()}
  const ownerPlan=await send(10001,'group',{reply:'GROUP_FOLLOWUP_PLANNED',tool:'qq_group_followup',args:groupPlan})
  assert.ok(ownerPlan.some(row=>row.messages.some(message=>message.role==='tool' && JSON.stringify(message).includes(groupNote.id))),'Group owner could not plan the grounded followup '+JSON.stringify(ownerPlan.flatMap(row=>row.messages.filter(message=>message.role==='tool'))).slice(-1400))
  const memberPlan=await send(10002,'group',{reply:'MEMBER_GROUP_FOLLOWUP_PLANNED',tool:'qq_group_followup',args:groupPlan})
  const memberResult=memberPlan.at(-1).messages.filter(message=>message.role==='tool').at(-1)
  assert.ok(JSON.stringify(memberResult).includes(groupNote.id),'An admitted group conversation cannot form a scoped followup')
  await send(10002,'group',{text:'I prefer piano',reply:'GOT_PREFERENCE'})
  const groupFile=join(profile,'alcor/memory/groups/30001/alcor-authority.json')
  await until(()=>JSON.parse(readFileSync(groupFile)).entries.some(e=>e.subject==='qq:10002' && e.content==='I prefer piano'),'attributed group preference')
  const own=await send(10002,'group',{text:'Hello again',reply:'MEMBER_AGAIN'})
  assert.ok(JSON.stringify(own).includes('qq:10002 亲口说过：I prefer piano'))
  const another=await send(10001,'group',{text:'Hello again',reply:'OTHER_MEMBER'})
  assert.ok(JSON.stringify(another).includes('本轮发言人：qq:10001'))
  assert.ok(!JSON.stringify(another).includes('qq:10001 亲口说过：I prefer piano'))
  checks.push('PASS group relationship context follows the authenticated member')
  const privateEvidence=(await rpc('alcorSecretary','memoryState',{request:{}})).entries.find(row=>row.content==='PRIVATE_MEMORY_CANARY').id
  const forbidden=await send(10002,'group',{reply:'PRIVATE_SCOPE_DENIED',tool:'qq_group_followup',args:{...groupPlan,evidence:privateEvidence}})
  const denied=forbidden.at(-1).messages.filter(message=>message.role==='tool').at(-1)
  assert.ok(/记忆已经不可用/.test(JSON.stringify(denied)),'A group turn used private evidence')
  const labels=await rpc('alcorSecretary','deliverySettings',{request:{}})
  assert.equal(labels.targets.find(row=>row.route.kind==='group').route.label,'纸样讨论组')
  assert.equal(labels.targets.find(row=>row.route.kind==='private').route.label,'林舟')
  const proactiveStart=outgoing.length
  await control('/delivery-plan',{content:'ASK_PROGRESS'})
  await send(10001,'private',{text:'The issue is already resolved',reply:'RESOLVED'})
  await pause(1700);await control('/delivery-run')
  await until(async()=> (await control('/delivery-debug')).receipts.some(r=>r.scope==='personal' && r.status==='cancelled'),'cancelled due question',75000).catch(async error=>{throw Error(error.message+' '+JSON.stringify(await control('/delivery-debug')))})
  assert.ok(!outgoing.slice(proactiveStart).some(row=>JSON.stringify(row).includes('ASK_PROGRESS')))
  checks.push('PASS new QQ information cancels the pending question before outbound delivery')
  const modelsBeforeDelivery=requests.length
  await control('/delivery-plan',{content:'PRIVATE_PROACTIVE_DELIVERY'})
  await pause(1700)
  await control('/delivery-run')
  await until(()=>outgoing.slice(proactiveStart).some(row=>JSON.stringify(row).includes('PRIVATE_PROACTIVE_DELIVERY')),'scheduled private proactive delivery',75000).catch(async err=>{throw Error(err.message+' '+JSON.stringify(await control('/delivery-debug')))})
  await control('/delivery-run');await pause(400)
  assert.equal(outgoing.slice(proactiveStart).filter(row=>JSON.stringify(row).includes('PRIVATE_PROACTIVE_DELIVERY')).length,1)
  assert.ok(requests.slice(modelsBeforeDelivery).every(row=>!row.tools?.length),'A proactive decision must not invoke agent tools')
  await control('/delivery-plan',{content:'REVOKED_MUST_NOT_SEND'})
  await rpc('alcorSecretary','allowChannelDelivery',{request:{sessionId:'onebot-private-10001',enabled:false}})
  await pause(1700);await control('/delivery-run');await pause(400)
  assert.ok(!outgoing.slice(proactiveStart).some(row=>JSON.stringify(row).includes('REVOKED_MUST_NOT_SEND')))
  await pause(1700);await control('/delivery-run')
  await until(()=>outgoing.slice(proactiveStart).some(row=>row.group_id===30001 && JSON.stringify(row).includes('GROUP_PROACTIVE_DELIVERY')),'scheduled group-scoped proactive delivery',75000).catch(async err=>{throw Error(err.message+' '+JSON.stringify(await control('/delivery-debug')))})
  const transcript=await rpc('alcorSecretary','channelHistory',{request:{sessionId:'onebot-group-30001'}})
  assert.ok(transcript.messages.some(row=>row.text==='GROUP_PROACTIVE_DELIVERY'))
  await rpc('alcorSecretary','allowChannelDelivery',{request:{sessionId:'onebot-group-30001',enabled:false}})
  await rpc('alcorSecretary','allowInitiative',{request:{enabled:false}})
  await rpc('alcorSecretary','allowChannelDelivery',{request:{sessionId:'onebot-private-10001',enabled:true}})
  checks.push('PASS real DSH Schedule sends one private and one scoped group followup through the registered adapter, suppresses duplicates and revocation, and reviews delivery without agent tools')
  const result = { restart, checks, modelCalls: requests.length, outgoing: outgoing.length, versions: { secretary: JSON.parse(readFileSync(join(profile, 'node_modules/@alcor/secretary/package.json'))).version, qq: JSON.parse(readFileSync(join(profile, 'node_modules/dsh-onebot-qq/package.json'))).version } }
  writeFileSync(join(root, restart ? 'restart-result.json' : 'result.json'), JSON.stringify(result, null, 2) + '\n')
  const url = /dsh web: (http:\S+)/.exec(log)?.[1]
  if (url) writeFileSync(join(root, 'browser-url.json'), JSON.stringify({ url }), { mode: 0o600 })
  console.log(checks.join('\n'))
  if (keep) { console.log('READY_FOR_UI'); await new Promise(resolve => process.once('SIGTERM', resolve)) }
} catch (error) {
  console.error(error.message); console.error(log.replace(/token=\S+/g, 'token=[redacted]').slice(-5000)); process.exitCode = 1
} finally {
  socket?.close(); host.kill('SIGTERM'); await Promise.race([new Promise(resolve => host.once('exit', resolve)), pause(5000)])
  if (host.exitCode === null) host.kill('SIGKILL')
  await new Promise(resolve => model.close(resolve))
}
