import { authorityStore } from '../../packages/alcor-secretary/src/memory/aurabot/authority.js'
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm, readFile, writeFile, symlink } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createScopedMemory } from '../../packages/alcor-secretary/src/memory/scoped.js'
import { Store } from '../../packages/alcor-secretary/src/memory/engine/store.js'
import { AlcorMemory } from '../../packages/alcor-secretary/src/memory/service.js'
const owner={owner:true,subject:'owner',source:{kind:'desktop',sessionId:'chat',at:'2026-11-09T00:00:00Z'}}
const quiet={info(){},warn(){},error(){}}
async function root(t){const dir=await mkdtemp(join(tmpdir(),'alcor-memory-'));t.after(()=>rm(dir,{recursive:true,force:true}));return dir}
test('memory, correction, source and SOUL survive restart; forgetting does not revive old summaries',async t=>{
  const dir=await root(t)
  let memory=createScopedMemory({dataDir:dir,audience:'personal',logger:quiet})
  const item=await memory.remember({content:'Coffee should be warm'},owner)
  const other=await memory.remember({content:'Tea without sugar'},owner)
  await memory.setSoul('SAME_ALCOR_SOUL')
  await memory.change({id:item.id,content:'Coffee should be cold'},owner)
  assert.equal(memory.records().find(row=>row.id===other.id).content,'Tea without sugar')
  await memory.dispose()
  memory=createScopedMemory({dataDir:dir,audience:'personal',logger:quiet})
  assert.equal(memory.soul,'SAME_ALCOR_SOUL')
  assert.equal(memory.recall('Coffee')[0].content,'Coffee should be cold')
  assert.equal(memory.recall('Coffee')[0].source.sessionId,'chat')
  await memory.change({id:item.id},owner);await memory.dispose()
  memory=createScopedMemory({dataDir:dir,audience:'personal',logger:quiet})
  assert.ok(!memory.contextText().includes('Coffee'));assert.equal(memory.records().length,1)
  await memory.dispose()
})
test('forgetting a derived fact invalidates its profile and old journal while retaining chat files',async t=>{
  const dir=await root(t),store=new Store({dataDir:dir,logger:quiet});store.init({migrateV1:false})
  const fact=store.addFact({fact:'OBSOLETE_CANARY'});store.writeCompiled({facts:'OBSOLETE_CANARY'})
  store.appendJournal({id:'old-record',ts:Date.now()-1000,session:'chat',turn:1,user:'OBSOLETE_CANARY',assistant:'ok'});store.releaseLock()
  let memory=createScopedMemory({dataDir:dir,audience:'personal',logger:quiet})
  assert.ok(memory.contextText().includes('OBSOLETE_CANARY'))
  await memory.change({id:fact.id},owner);assert.ok(!memory.contextText().includes('OBSOLETE_CANARY'))
  await memory.dispose();memory=createScopedMemory({dataDir:dir,audience:'personal',logger:quiet})
  assert.deepEqual(memory.records(),[]);assert.ok(!memory.contextText().includes('OBSOLETE_CANARY'))
  await memory.dispose();assert.ok((await readFile(join(dir,'journal',new Date().toLocaleDateString('sv-SE')+'.jsonl'),'utf8')).includes('OBSOLETE_CANARY'))
})
test('group stores cannot read each other or edit another member, and prevent a second writer',async t=>{
  const dir=await root(t),a=createScopedMemory({dataDir:join(dir,'a'),audience:'group:1',logger:quiet}),b=createScopedMemory({dataDir:join(dir,'b'),audience:'group:2',logger:quiet})
  t.after(async()=>{await a.dispose();await b.dispose()})
  const member={owner:false,subject:'qq:123',source:{kind:'qq-group',at:'now'}}
  const entry=await a.remember({content:'GROUP_ONE_ONLY'},member)
  assert.deepEqual(b.records(),[])
  await assert.rejects(a.change({id:entry.id},{...member,subject:'qq:456'}),/自己的记忆/)
  await assert.rejects(b.change({id:entry.id},member),/不存在/)
  assert.throws(()=>createScopedMemory({dataDir:join(dir,'a'),audience:'group:1'}),/写入者/)
})
test('corrupt authority fails visibly and symlink memory is refused',async t=>{
  const dir=await root(t),memory=createScopedMemory({dataDir:join(dir,'data'),audience:'personal',logger:quiet});await memory.dispose()
  await rm(join(dir,'data','aurabot'),{recursive:true})
  await writeFile(join(dir,'data','alcor-authority.json'),'{broken')
  assert.throws(()=>createScopedMemory({dataDir:join(dir,'data'),audience:'personal',logger:quiet}))
  assert.equal(await readFile(join(dir,'data','alcor-authority.json'),'utf8'),'{broken')
  await symlink(join(dir,'data'),join(dir,'link'))
  assert.throws(()=>createScopedMemory({dataDir:join(dir,'link'),audience:'personal',logger:quiet}),/软链接/)
})
test('capture excludes the DSH workbench; actual turn identity and live owner revocation govern tools',async t=>{
  const dir=await root(t),events=new Map();let admins=['123'],boundary={openTurnStartSeq:5,lastTurn:1}
  const config={groupAllowFrom:['456'],get adminUsers(){return admins}}
  const ctx={get(name){return{profileContext:{dir},configEditor:{entries:()=>[{options:{id:'dsh-onebot',config},fiber:{state:2}}]},agentPresets:{presetForSession:session=>session.id==='workbench'?'default':'alcor'}}[name]},
    on(name,handler){events.set(name,handler)},effect(){},agents:{turnBoundaryOf:()=>boundary},logger:quiet}
  const memory=new AlcorMemory(ctx,{table:{get:()=>({sessions:{}})}});t.after(()=>memory.personal.dispose())
  const agent={session:{id:'assistant',header:{id:'assistant'}}},native={source:{kind:'user'},content:[{type:'text',text:'OWNER_PRIVATE_CANARY'}]}
  await events.get('agent/pre-step')({agent,messages:[native]},()=>Promise.resolve({kind:'enter'}))
  const item=await memory.use('remember',{content:'OWNER_PRIVATE_CANARY'},agent)
  assert.equal(item.source.sessionId,'assistant')
  assert.ok(memory.personalContext(agent).includes('OWNER_PRIVATE_CANARY'))
  const scheduled={session:{id:'scheduled-assistant',header:{}}}
  await events.get('agent/pre-step')({agent:scheduled,messages:[{source:{kind:'schedule'}}]},()=>{})
  assert.equal(memory.personalContext(scheduled),'')
  const workbench={session:{id:'workbench',header:{id:'workbench'}}}
  await events.get('agent/pre-step')({agent:workbench,messages:[native]},()=>Promise.resolve({kind:'enter'}))
  await assert.rejects(memory.use('recall',{query:'OWNER'},workbench),/有效/)
  const qq={session:{id:'onebot-private-123',header:{}}},message={source:{kind:'plugin:dsh-onebot',qqUserId:'123'}}
  await events.get('agent/pre-step')({agent:qq,messages:[message]},()=>Promise.resolve({kind:'enter'}))
  assert.ok((await memory.use('recall',{query:'OWNER'},qq)).length)
  admins=[];await assert.rejects(memory.use('recall',{query:'OWNER'},qq),/身份/)
  const rejected=await events.get('agent/pre-step')({agent:qq,messages:[native]},()=>{throw Error('Desktop input reached model')})
  assert.equal(rejected.kind,'reject')
  boundary={openTurnStartSeq:6,lastTurn:2};await assert.rejects(memory.use('recall',{query:'OWNER'},agent),/有效/)
})

test('revoking a QQ group immediately removes cached group context and owner recall',async t=>{
  const dir=await root(t),listeners=new Map(),config={adminUsers:['123'],groupAllowFrom:['456']}
  const ctx={get(name){return {profileContext:{dir},configEditor:{entries:()=>[{options:{id:'dsh-onebot',config},fiber:{state:2}}]},agentPresets:{presetForSession:()=>'alcor'}}[name]},
    on(name,handler){listeners.set(name,handler)},effect(){},agents:{turnBoundaryOf:()=>({openTurnStartSeq:1,lastTurn:1})},logger:quiet}
  const m=new AlcorMemory(ctx,{table:{get:()=>({sessions:{}})}})
  t.after(()=>Promise.all([m.personal,...m.groups.values()].map(store=>store.dispose())))
  const group=m.group('456'),session={id:'onebot-group-456',header:{}},source={kind:'plugin:dsh-onebot',qqGroupId:'456',qqUserId:'123'}
  listeners.get('session/event')(session,{type:'user/message',data:{source}})
  const qa={session};await listeners.get('agent/pre-step')({agent:qa,messages:[{source}]},()=>{})
  await group.remember({content:'REVOKED_GROUP_CANARY'},qa)
  const desktop={session:{id:'assistant',header:{}}}
  await listeners.get('agent/pre-step')({agent:desktop,messages:[{source:{kind:'user'}}]},()=>{})
  assert.ok((await m.use('recall',{query:'REVOKED_GROUP_CANARY'},desktop)).some(row=>row.audience==='group:456'))
  config.groupAllowFrom=[]
  assert.equal((await m.use('recall',{query:'REVOKED_GROUP_CANARY'},desktop)).length,0)
  assert.ok(!group.contextText().includes('REVOKED_GROUP_CANARY'))
  await assert.rejects(group.remember({content:'after removal'},qa))
})

test('cold import preserves explicit personal memory and group history without importing workbench journals',async t=>{
  const { migrateAssistantMemory }=await import('../../scripts/product/migrate-assistant-memory.mjs')
  const { mkdir }=await import('node:fs/promises')
  const dir=await root(t),personal=join(dir,'legacy'),groups=join(dir,'old-groups'),profile=join(dir,'profile')
  let store=new Store({dataDir:personal,logger:quiet});store.init({migrateV1:false});store.savePersona({soul:'IMPORTED_SOUL'});store.addMemory({content:'EXPLICIT_PERSONAL'});store.appendJournal({id:'workbench-raw',ts:Date.now(),user:'WORKBENCH_HISTORY',session:'workbench'});store.releaseLock()
  store=new Store({dataDir:join(groups,'123'),logger:quiet});store.init({migrateV1:false});store.addMemory({content:'OLD_GROUP_MEMORY'});store.releaseLock()
  await mkdir(profile,{recursive:true})
  const result=await migrateAssistantMemory({profile,personal,groups,participants:{123:['456']}})
  const a=createScopedMemory({dataDir:join(result.target,'personal'),audience:'personal',logger:quiet}),g=createScopedMemory({dataDir:join(result.target,'groups','123'),audience:'group:123',logger:quiet})
  try {assert.ok(!a.soul.includes('IMPORTED_SOUL'));assert.ok(a.contextText().includes('IMPORTED_SOUL'));assert.ok(a.contextText().includes('EXPLICIT_PERSONAL'));assert.ok(!a.contextText().includes('WORKBENCH_HISTORY'));assert.ok(g.contextText().includes('OLD_GROUP_MEMORY'));assert.equal(g.hasParticipant('456'),true)}finally{await a.dispose();await g.dispose()}
  await assert.rejects(migrateAssistantMemory({profile,personal,groups}),/already exists/)
  assert.ok((await readFile(join(personal,'memory.jsonl'),'utf8')).includes('EXPLICIT_PERSONAL'))
})

test('learned habits require independent experiences, survive restart and retire with their evidence',async t=>{
  const dir=await root(t)
  let memory=createScopedMemory({dataDir:dir,audience:'personal',logger:quiet})
  try {
    const a=await memory.remember({content:'A small first step helped with the essay'},owner)
    const b=await memory.remember({content:'A small first step helped with the prototype'},{...owner,source:{...owner.source,sessionId:'other-chat'}})
    const content='I like to offer one small experiment when we are stuck'
    await assert.rejects(memory.reflect({content,evidence:[a.id,a.id]},owner),/不同/)
    await assert.rejects(memory.reflect({content,evidence:[a.id,'foreign']},owner),/不同/)
    const habit=await memory.reflect({content,evidence:[a.id,b.id]},owner)
    await memory.dispose()
    memory=createScopedMemory({dataDir:dir,audience:'personal',logger:quiet})
    assert.ok(memory.contextText().includes(content))
    assert.deepEqual(memory.records().find(row=>row.id===habit.id).evidence,[a.id,b.id])
    await memory.change({id:b.id,content:'A small experiment did not help'},owner)
    assert.ok(!memory.contextText().includes(content))
    assert.ok(!memory.records().some(row=>row.id===habit.id))
  }finally{await memory.dispose()}
})

test('identity transfers between empty Profiles, retains ancestry and refuses divergent edits',async t=>{
  const dir=await root(t)
  const a=createScopedMemory({dataDir:join(dir,'a'),audience:'personal',logger:quiet})
  const b=createScopedMemory({dataDir:join(dir,'b'),audience:'personal',logger:quiet})
  try {
    await a.remember({content:'PRIVATE_TRANSFER_CANARY'},owner)
    await a.editIdentity({name:'Alcor',soul:'I like small experiments',expectedRevision:a.identity().revision})
    const transfer=a.identity()
    assert.ok(!JSON.stringify(transfer).includes('PRIVATE_TRANSFER_CANARY'))
    await b.importIdentity(transfer,b.identity().revision,()=>true)
    assert.deepEqual(b.identity(),transfer)
    await a.setSoul('New preference from device A')
    await b.importIdentity(a.identity(),b.identity().revision,()=>true)
    await a.setSoul('A edit');await b.setSoul('B edit')
    await assert.rejects(b.importIdentity(a.identity(),b.identity().revision,()=>true),/版本已分开/)
    assert.equal(b.soul,'B edit')
  }finally{await a.dispose();await b.dispose()}
})

test('a private initiative requires opt-in and live evidence, obeys quiet hours and is cancelled on correction',async t=>{
  const dir=await root(t), m=createScopedMemory({dataDir:dir,audience:'personal',logger:quiet})
  try {
    const evidence=await m.remember({content:'We talked about a first draft'},owner)
    const due=new Date();due.setDate(due.getDate()+1);due.setHours(10,0,0,0)
    const request={evidence:evidence.id,content:'How did the first paragraph turn out?',dueAt:due.toISOString()}
    await assert.rejects(m.planFollowup(request),/开启/)
    await m.allowInitiative(true)
    const note=await m.planFollowup(request)
    assert.equal((await m.planFollowup(request)).id,note.id)
    assert.equal(m.nextFollowup(new Date(due-1000)),null)
    assert.equal(m.nextFollowup(due).id,note.id)
    const quietTime=new Date(due);quietTime.setHours(22)
    assert.equal(m.nextFollowup(quietTime),null)
    await m.change({id:evidence.id,content:'The draft is finished'},owner)
    assert.equal(m.nextFollowup(due),null)
    await m.planFollowup(request);await m.allowInitiative(false);await m.allowInitiative(true)
    assert.equal(m.nextFollowup(due),null)
  }finally{await m.dispose()}
})

test('encrypted experience transfer restores the same identity, entries and habits; rejects bad keys, scope, conflicts and grants',async t=>{
  const {sealExperiences,openExperiences} = await import('../../packages/alcor-secretary/src/memory/experience-file.js')
  const dir=await root(t), a=createScopedMemory({dataDir:join(dir,'a'),audience:'personal',logger:quiet})
  let b=createScopedMemory({dataDir:join(dir,'b'),audience:'personal',logger:quiet})
  try {
    const one=await a.remember({content:'CANARY private first draft'},owner)
    const two=await a.remember({content:'CANARY next experiment'},{...owner,source:{...owner.source,sessionId:'second'}})
    await a.reflect({content:'try a small experiment',evidence:[one.id,two.id]},owner)
    await a.allowInitiative(true)
    const doc=await a.exportExperiences(()=>a.identity().id)
    const key='synthetic passphrase'
    const bytes=await sealExperiences(doc,key)
    assert.ok(!Buffer.from(bytes).includes('CANARY'))
    assert.notDeepEqual(bytes,await sealExperiences(doc,key))
    await assert.rejects(openExperiences(bytes,key+'x'),/口令/)
    const modified=Uint8Array.from(bytes); modified[modified.length-1]^=1
    await assert.rejects(openExperiences(modified,key),/损坏/)
    const decoded=await openExperiences(bytes,key)
    await assert.rejects(b.restoreExperiences(decoded,()=>b.identity().id),/同一人格/)
    await b.importIdentity(a.identity(),b.identity().revision,()=>true)
    await assert.rejects(b.restoreExperiences({...decoded,initiative:true},()=>b.identity().id))
    await assert.rejects(b.restoreExperiences({...decoded,audience:'group:123'},()=>b.identity().id))
    await b.restoreExperiences(decoded,()=>b.identity().id)
    await b.dispose();b=createScopedMemory({dataDir:join(dir,'b'),audience:'personal',logger:quiet})
    assert.equal(b.initiative,false)
    assert.ok(b.contextText().includes('try a small experiment'))
    assert.equal(b.records().find(row=>row.id===one.id).source.sessionId,'chat')
    await b.change({id:one.id},owner)
    await assert.rejects(b.restoreExperiences(decoded,()=>b.identity().id),/空白/)
    assert.ok(!b.contextText().includes('try a small experiment'))
  }finally{await a.dispose();await b.dispose()}
})


test('reflection can learn from two recent conversations without an explicit remember command',async t=>{
  const dir=await root(t),store=new Store({dataDir:dir,logger:quiet})
  store.init({migrateV1:false})
  for(const id of ['first','second']) store.appendJournal({id,session:id,ts:Date.now(),turn:1,user:'Trying a small experiment helped in '+id})
  store.releaseLock()
  let memory=createScopedMemory({dataDir:dir,audience:'personal',logger:quiet})
  try {
    const result=await memory.reflect({content:'I like to start with one experiment',evidence:['journal:first','journal:second']},owner)
    assert.equal(memory.records().filter(row=>row.id==='journal:first').length,1)
    await memory.dispose();memory=createScopedMemory({dataDir:dir,audience:'personal',logger:quiet})
    assert.ok(memory.contextText().includes(result.content))
    await memory.change({id:'journal:first'},owner)
    assert.ok(!memory.contextText().includes(result.content))
  }finally{await memory.dispose()}
})

test('spontaneous context selects relevant experiences and habits, while explicit recall still finds other topics',async t=>{
  const dir=await root(t),m=createScopedMemory({dataDir:dir,audience:'personal',logger:quiet})
  try {
    const a=await m.remember({content:'展台 A 用纸样测试握持距离。'},owner)
    const b=await m.remember({content:'展台 B 用纸样检查尺寸。'},{...owner,source:{...owner.source,sessionId:'second'}})
    await m.remember({content:'完全无关的牙科预约。'},owner)
    await m.reflect({content:'遇到成本高的制作问题，先试小实验。',evidence:[a.id,b.id]},owner)
    assert.match(m.turnContext('纸样可靠吗？'),/我的做事习惯：.*先试小实验/)
    assert.match(m.recall('小实验').find(row=>row.kind==='habit').attribution,/助手自己的习惯/)
    assert.ok(!m.turnContext('纸样可靠吗？').includes('牙科'))
    assert.equal(m.turnContext('今天就是想说一句：总算把纸样装上了。'), '')
    assert.equal(m.turnContext('太开心啦'), '')
    assert.equal(m.turnContext(''), '')
    assert.match(m.recall('牙科')[0].content,/牙科/)
  }finally{await m.dispose()}
})

test('renaming the assistant leaves one public name and keeps custom personality text verbatim',async t=>{
  const m=createScopedMemory({dataDir:await root(t),audience:'personal',logger:quiet})
  try {
    const {DEFAULT_SOUL}=await import('../../packages/alcor-secretary/src/memory/engine/defaults.js')
    const service={personal:m}
    await m.editIdentity({name:'岚舟',soul:DEFAULT_SOUL})
    assert.ok(AlcorMemory.prototype.publicPersona.call(service).startsWith('名字：岚舟'))
    assert.ok(!AlcorMemory.prototype.publicPersona.call(service).includes('你是 Alcor'))
    const custom='我和 Alcor 项目有关，喜欢耐心观察。'
    await m.editIdentity({name:'岚舟',soul:custom});assert.ok(AlcorMemory.prototype.publicPersona.call(service).includes(custom))
  }finally{await m.dispose()}
})

test('retired task grants are discarded without changing identity or queued personal follow-ups',async t=>{
  const dir=await root(t), path=join(dir,'alcor-authority.json')
  let memory=createScopedMemory({dataDir:dir,audience:'personal',logger:quiet})
  try {
    memory.identity()
    await memory.allowInitiative(true)
    const evidence=await memory.remember({content:'Ask about the recording'},owner)
    await memory.planFollowup({evidence:evidence.id,content:'How did it sound?',dueAt:new Date(Date.now()+3600000).toISOString()})
    await memory.dispose()
    const original=authorityStore(dir).load()
    await rm(join(dir,'aurabot'),{recursive:true})
    await writeFile(path,JSON.stringify({...original,taskGrant:{until:Date.now()+60000},backgroundGrant:{jobId:'old'},taskShown:{id:'task'},taskDismissedDay:'2026-11-10'}))
    memory=createScopedMemory({dataDir:dir,audience:'personal',logger:quiet})
    assert.deepEqual(authorityStore(dir).load(),original)
    assert.equal(JSON.parse(await readFile(path,'utf8')).taskGrant.until>0,true)
    assert.equal(memory.desktopFollowup(),null)
  } finally {await memory.dispose()}
})

test('delivery can be disabled and reopened without cancelling a grounded follow-up across restart',async t=>{
  const dir=await root(t)
  let m=createScopedMemory({dataDir:dir,audience:'personal',logger:quiet})
  try{
    const evidence=await m.remember({content:'Discuss the sample next week'},owner)
    const due=new Date();due.setDate(due.getDate()+1);due.setHours(10,0,0,0)
    await m.allowInitiative(true)
    assert.equal(m.desktopDelivery,false)
    const note=await m.planFollowup({evidence:evidence.id,content:'How was the sample?',dueAt:due.toISOString()})
    assert.equal(m.nextFollowup(due).id,note.id)
    assert.equal(await m.desktopFollowup(due),null)
    await m.allowDesktopDelivery(true)
    assert.equal((await m.desktopFollowup(due)).id,note.id)
    await m.allowDesktopDelivery(false)
    await m.dispose();m=createScopedMemory({dataDir:dir,audience:'personal',logger:quiet})
    assert.equal(m.initiative,true);assert.equal(m.desktopDelivery,false)
    assert.equal(m.nextFollowup(due).id,note.id)
    await m.allowDesktopDelivery(true)
    assert.equal((await m.desktopFollowup(due)).id,note.id)
  }finally{await m.dispose()}
})

test('a newly registered non-QQ group obtains a separate memory store before its first catalog refresh',async t=>{
 const dir=await root(t),route={sessionId:'other-group',channel:'other',account:'one',kind:'group',audience:'channel:other:one:group:room'}
 const ctx={get:name=>name==='profileContext'?{dir}:undefined,on(){},effect(){}}
 const memory=new AlcorMemory(ctx,{channels:{resolve:()=>route,adapter:()=>({}),table:{entries:()=>[]}}})
 try{
   const group=memory.storeForAudience(route.audience,route)
   const actor={owner:true,subject:'other:one:member',source:{kind:'channel',sessionId:route.sessionId,at:new Date().toISOString()}}
   await group.remember({content:'Only in this room'},actor)
   assert.equal(group.records()[0].audience,route.audience)
   assert.ok(!memory.personal.records().some(row=>row.content==='Only in this room'))
 }finally{await memory.personal.dispose();await Promise.all([...memory.groups.values()].map(store=>store.dispose()))}
})

test('search retained conversations after compilation, including Alcor replies beyond the recent preview', async t => {
  const dir = await root(t), store = new Store({dataDir:dir,logger:quiet})
  store.init({migrateV1:false})
  const now = Date.now()
  store.appendJournal({id:'old',ts:now-100000,session:'group-test',user:'What went wrong?',assistant:'Cinnabar pigment was unavailable'})
  store.setJournalStatus('old','compiled')
  for(let i=0;i<24;i++) store.appendJournal({id:'new-'+i,ts:now-2000+i,session:'group-test',user:'Later message '+i})
  store.releaseLock()
  const group = createScopedMemory({dataDir:dir,audience:'group:456',logger:quiet})
  t.after(() => group.dispose())
  assert.ok(!group.records().some(row => row.id==='journal:old'))
  const hit = group.recall('cinnabar')[0]
  assert.equal(hit.id,'journal:old')
  assert.match(hit.content,/助手回复：Cinnabar/)
  await group.change({id:hit.id},owner)
  assert.equal(group.recall('cinnabar').length,0)
})

test('trusted contact memory follows its own route and owner context can recall an enabled group without a prior owner post', async t => {
  const events = new Map(), dir = await root(t)
  let enabled = true
  const route = {sessionId:'onebot-private-222',kind:'private',peer:'222',account:'999',channel:'qq',owner:false,audience:'qq:999:private:222'}
  const channels = {
    resolve: id => id===route.sessionId ? route : null, adapter:()=>({}),
    actor: (session,source) => enabled && source.kind==='plugin:dsh-onebot' && source.qqUserId===route.peer
      ? {owner:false,audience:route.audience,subject:'qq:222',source:{kind:'qq-private',sessionId:session.id,at:new Date().toISOString()}} : undefined,
  }
  const config = {adminUsers:['111'],groupAllowFrom:['456'],groupPolicy:'allowlist'}
  const ctx = {get:name=>({profileContext:{dir},agentPresets:{presetForSession:()=>'alcor'},configEditor:{entries:()=>[{options:{id:'dsh-onebot',config},fiber:{state:2}}]}}[name]),
    agents:{turnBoundaryOf:()=>({openTurnStartSeq:1,lastTurn:1})},on:(name,handler)=>events.set(name,handler),effect(){},logger:quiet}
  const m=new AlcorMemory(ctx,{channels});t.after(()=>Promise.all([m.personal,...m.groups.values()].map(s=>s.dispose())))
  const me = {session:{id:'owner-chat',header:{}}}, person = {session:{id:route.sessionId,header:{}}}
  const input = (source,text)=>({source,content:[{type:'text',text}]})
  const run = (agent,...messages) => events.get('agent/pre-step')({agent,messages},()=>{})
  await m.personal.remember({content:'Festival owner private flight'},owner)
  const group=m.storeForAudience('group:456')
  await group.remember({content:'Festival group workshop starts at ten'},{owner:false,subject:'qq:777',source:{kind:'qq-group',sessionId:'group456',at:new Date().toISOString()}})
  await run(person,input({kind:'plugin:dsh-onebot',qqUserId:'222'},'Festival'))
  await m.use('remember',{content:'Festival contact brings a camera'},person)
  assert.match(m.personalContext(person),/brings a camera/)
  assert.doesNotMatch(m.personalContext(person),/private flight|starts at ten/)
  await run(me,input({kind:'user'},'Festival'))
  assert.match(m.personalContext(me),/group:456.*starts at ten/)
  assert.doesNotMatch(m.personalContext(me),/brings a camera/)
  config.groupAllowFrom=[]
  assert.doesNotMatch(m.personalContext(me),/starts at ten/)
  enabled=false
  assert.equal(m.personalContext(person),'')
  await assert.rejects(m.use('recall',{query:'Festival'},person),/身份/)
})

test('group transcript pages preserve platform cursors, search earlier turns and recheck the audience after awaiting I/O', async () => {
  let audience='group:456', duringRead
  const agent = {session:{id:'room'}}, calls = []
  const service = {
    trustedActor:()=>({audience,source:{kind:'qq-group'}}),
    spaces:{channels:{platformHistory:async(id,cursor)=>{
      calls.push([id,cursor])
      const hits=[{seq:1,cursor:-819,role:'QQ 777',nickname:'同学',text:'上星期的蓝色提案',time:123}]
      duringRead?.()
      return {messages:hits,hasMore:true,beforeSeq:-819}
    }}},
  }
  const read=(args)=>AlcorMemory.prototype.groupHistory.call(service,'456',args,agent)
  const page=await read({query:'蓝色'})
  assert.equal(page.nextBefore,-819);assert.equal(page.messages[0].speaker,'QQ 777')
  assert.deepEqual(await read({query:'不存在',before:page.nextBefore}),{messages:[],nextBefore:-819})
  assert.deepEqual(calls,[['room',undefined],['room',-819]])
  duringRead=()=>{audience='group:123'}
  await assert.rejects(read({}),/当前 QQ 群/)
  await assert.rejects(read({before:-819}),/当前 QQ 群/)
  assert.equal(calls.length,3)
})
