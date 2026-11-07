import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp,rm,readFile,mkdir,writeFile} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {randomUUID,createHash} from 'node:crypto'
import {execFile} from 'node:child_process'
import {promisify} from 'node:util'
import {fileURLToPath} from 'node:url'
import {HubWorker} from '../../packages/stella-agent-hub/src/worker.js'
import {BusinessWorker} from '../../packages/stella-organizer/src/business.js'
import {assertStellaDetached} from '../../packages/stella-agent-hub/src/legacy.js'
import Hub from '../../packages/stella-agent-hub/lib/index.js'
import {Service} from '@deepseek-ai/cordis'

const hash = async path => createHash('sha256').update(await readFile(path)).digest('hex')
test('legacy owner guard checks selected packages before service initialization order can hide Stella',async t=>{
  const directory=await mkdtemp(join(tmpdir(),'hub-owner-guard-'))
  t.after(()=>rm(directory,{recursive:true,force:true}))
  const packageDirectory=join(directory,'node_modules','@stella','secretary')
  await mkdir(packageDirectory,{recursive:true})
  const anchor=join(directory,'package.json')
  await writeFile(anchor,JSON.stringify({name:'fixture',private:true}))
  const profile={dir:directory,installAnchor:anchor,startedBundles:['@stella/secretary','@stella/agent-hub']}
  await writeFile(join(packageDirectory,'package.json'),JSON.stringify({name:'@stella/secretary',version:'0.1.55'}))
  await assert.rejects(assertStellaDetached(profile),/支持独立 AgentHub/)
  await assert.rejects(Hub.prototype[Service.init].call({ctx:{get:name=>name==='profileContext'?profile:undefined}}),/支持独立 AgentHub/)
  await writeFile(join(packageDirectory,'package.json'),JSON.stringify({name:'@stella/secretary',version:'0.1.55',stella:{agentHubDetached:true}}))
  await assert.doesNotReject(assertStellaDetached(profile))
  await assert.doesNotReject(assertStellaDetached({...profile,startedBundles:['@stella/agent-hub']}))
})
test('independent Hub imports legacy data once, preserves source and receipts, and cannot access secretary data',async t=>{
  const directory=await mkdtemp(join(tmpdir(),'hub-independent-'))
  let old,hub
  t.after(async()=>{await old?.close();await hub?.close();await rm(directory,{recursive:true,force:true})})
  const legacy=join(directory,'stella','business'),target=join(directory,'agent-hub')
  old=new HubWorker(legacy);await old.call('initialize')
  const invoke=(worker,method,args)=>worker.call('invoke',{domain:'agent_hub',method,arguments:args})
  const payload={title:'Retained task',goal:'Retained goal',participants:[{nativeAgentId:'stella',displayName:'Existing role',persona:'Retained persona'}]},request_id=randomUUID()
  const task=await invoke(old,'create_task',{payload,request_id})
  await invoke(old,'set_workflow',{task_id:task.taskId,steps:[{stepId:'work',title:'Keep original task',participantId:task.participants[0].participantId,dependsOn:[]}],expected_revision:task.revision,request_id:randomUUID()})
  await invoke(old,'claim_step',{task_id:task.taskId,step_id:'work'})
  await old.close()
  const original=await hash(join(legacy,'agent-hub.sqlite3'))
  hub=new HubWorker(target,{legacyDirectory:legacy});await hub.call('initialize')
  const migrated=await invoke(hub,'get_task',{task_id:task.taskId})
  assert.equal(migrated.status,'outcome_unknown')
  assert.equal(migrated.steps[0].attempts.length,1)
  assert.deepEqual(migrated.participants,task.participants)
  assert.equal((await invoke(hub,'create_task',{payload,request_id})).taskId,task.taskId)
  assert.equal((await invoke(hub,'list_tasks',{})).length,1)
  const job={id:randomUUID(),title:'Old KV job',prompt:'Original prompt',parentSessionId:'old-parent',createdAt:new Date().toISOString(),status:'running',output:'Keep output',diagnostic:''}
  await hub.call('import_jobs',{jobs:[job]});await hub.call('import_jobs',{jobs:[job]})
  const jobs=await hub.call('legacy_jobs');assert.equal(jobs.length,1);assert.equal(jobs[0].status,'interrupted');assert.equal(jobs[0].output,'Keep output')
  await invoke(hub,'post_message',{task_id:task.taskId,body:'New owner message',recipient_ids:[],intent:'message',expected_revision:null,request_id:randomUUID()})
  await hub.close();hub=new HubWorker(target,{legacyDirectory:legacy});await hub.call('initialize')
  const restored=await invoke(hub,'get_task',{task_id:task.taskId})
  assert.ok(restored.messages.some(message=>message.body==='New owner message'))
  assert.equal((await hub.call('legacy_jobs')).length,1)
  assert.equal(await hash(join(legacy,'agent-hub.sqlite3')),original,'Migration must never rewrite the old database')
  await assert.rejects(hub.call('invoke',{domain:'tasks',method:'list_todos'}),/不提供 Stella/)
  const business=new BusinessWorker(join(directory,'secretary'),{worker:new URL('../../packages/stella-secretary/business/worker.py',import.meta.url)})
  try {await assert.rejects(business.call('invoke',{domain:'agent_hub',method:'list_tasks'}),/agent_hub/)} finally {await business.close()}
})

test('AgentHub package and compiled entrypoints have no secretary dependency',async()=>{
  const pkg=JSON.parse(await readFile(new URL('../../packages/stella-agent-hub/package.json',import.meta.url),'utf8'))
  assert.ok(!JSON.stringify(pkg).includes('@stella/secretary'))
  for(const name of ['index.js','client.js']){
    const built=await readFile(new URL('../../packages/stella-agent-hub/lib/'+name,import.meta.url),'utf8')
    assert.doesNotMatch(built,/(?:from\s*|import\(|require\()\s*['"]@stella\/secretary(?:\/|['"])/)
  }
  const business=await readFile(new URL('../../packages/stella-secretary/src/business.jsx',import.meta.url),'utf8')
  assert.ok(!business.includes("id: 'stella-agents'"))
  assert.ok(!business.includes('return <AgentHub'))
})

test('migration conflicts roll back the entire import without overwriting either database',async t=>{
  const directory=await mkdtemp(join(tmpdir(),'hub-conflict-'))
  t.after(()=>rm(directory,{recursive:true,force:true}))
  await promisify(execFile)(process.env.STELLA_BUSINESS_PYTHON || 'python3',['-B','-c',`
import hashlib, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from business.agent_hub.domain.store import AgentHubStore
from business.migration import import_stella
root = Path(sys.argv[2])
source = AgentHubStore(root / 'legacy/agent-hub.sqlite3')
target = AgentHubStore(root / 'new/agent-hub.sqlite3')
source.upsert_provider({'providerConfigId':'old-provider','providerLocalId':'codex-cli','displayName':'Original provider'})
with source._connect() as db:
    db.execute('INSERT INTO hub_tasks VALUES (?,?,?)', ('hub-conflict','{"goal":"source"}','original'))
with target._connect() as db:
    db.execute('INSERT INTO hub_tasks VALUES (?,?,?)', ('hub-conflict','{"goal":"target"}','original'))
before = hashlib.sha256(source.db_path.read_bytes()).digest()
try:
    import_stella(target, source.db_path.parent)
except ValueError as error:
    assert '冲突' in str(error)
else:
    raise AssertionError('Conflicting IDs must fail without replacing existing records')
with target._connect() as db:
    assert db.execute('SELECT COUNT(*) FROM provider_configs').fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM hub_imports').fetchone()[0] == 0
    assert db.execute('SELECT document_json FROM hub_tasks').fetchone()[0] == '{"goal":"target"}'
assert hashlib.sha256(source.db_path.read_bytes()).digest() == before
`,fileURLToPath(new URL('../../packages/stella-agent-hub',import.meta.url)),directory])
})
