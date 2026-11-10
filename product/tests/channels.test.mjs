import test from 'node:test'
import assert from 'node:assert/strict'
import {ChannelRegistry} from '../../packages/alcor-secretary/src/channels/registry.js'
import {ChannelDelivery} from '../../packages/alcor-secretary/src/channels/delivery.js'
const table=()=>{const data=new Map();return {get:k=>structuredClone(data.get(k)),put:async(k,v)=>data.set(k,structuredClone(v)),entries:()=>data.entries()}}
const route={sessionId:'inbox',channel:'other',account:'account1',kind:'private',peer:'owner1',label:'Owner',audience:'personal',owner:true}
function registry(){
 const rows=table(),r=new ChannelRegistry(rows,()=>['inbox'])
 const adapter={id:'other',account:'account1',label:'Other',sourceKind:'plugin:other',resolve:id=>id==='inbox'?route:undefined,
  actor:()=>({subject:'owner',audience:'personal'}),send:async()=>({status:'sent'}),canSend:()=>true,info:async()=>({online:true}),messages:()=>[]}
 const release=r.register(adapter)
 return {r,adapter,rows,release}
}
test('registered channel identities survive unload and reject a different account binding or forged source',async()=>{
 const {r,rows,release}=registry()
 assert.throws(()=>r.register({id:'other',account:'one:group',label:'Other',resolve(){},messages(){}}),/有效/)
 await r.catalog();assert.equal(r.resolve('inbox').account,'account1')
 assert.equal(r.actor({id:'inbox'},{kind:'user'}),undefined)
 assert.equal(r.actor({id:'inbox'},{kind:'plugin:other'}).subject,'owner')
 release();assert.equal(r.resolve('inbox').account,'account1');assert.equal(r.actor({id:'inbox'},{kind:'plugin:other'}),undefined)
 r.register({id:'other',account:'account2',label:'Other',messages:()=>[],resolve:()=>({...route,account:'account2'})})
 assert.equal(r.resolve('inbox').account,'account1');assert.equal(r.permitted(route),null)
 assert.ok(rows.get('inbox'))
})
test('external followups require their own grant, serialize sends, retain unknown receipts across restart, and respect revocation at the wire',async()=>{
 const {r,adapter}=registry();await r.catalog()
 const persistence=table(),notes=[{id:'n1',evidence:'memory1',content:'How was it?',due:Date.now()-1000}]
 const personal={initiative:true,reconsider:async n=>n,validReview:()=>true,pendingFollowups:()=>structuredClone(notes),nextFollowup:()=>notes[0],dismissFollowup:async id=>{const at=notes.findIndex(n=>n.id===id);if(at>=0)notes.splice(at,1)}}
 const ctx={on(){},effect(){},logger:{warn(){}},root:{fiber:{state:0}},workspaceRegistry:{list:()=>[{id:'home',path:'/synthetic'}]},
   schedule:{view:async()=>({code:'missing'}),create:async input=>({...input,id:'job',target:{...input.target,sessionId:'dispatch'}}),edit:async()=>({})}}
 const memory={personal,spaces:{info:async()=>({defaults:{alcor:'home'}})}}
 const delivery=new ChannelDelivery(ctx,r,memory,persistence);await delivery.initialize()
 let calls=0
 adapter.send=async(_,text,authorize)=>{assert.equal(text,'How was it?');if(!authorize())return {status:'deferred'};calls++;return {status:'sent',messageId:'one'}}
 await delivery.drain();assert.equal(calls,0)
 const grant={route,nonce:'g1',until:Date.now()+60000}
 await delivery.mutate(s=>s.grants.push(grant));await delivery.track('personal',notes[0])
 await Promise.all([delivery.drain(),delivery.drain()]);assert.equal(calls,1);assert.equal(notes.length,0)
 notes.push({id:'n2',evidence:'memory2',content:'How was it?',due:Date.now()-1000})
 await delivery.track('personal',notes[0]);adapter.send=async()=>{calls++;throw Error('lost response')}
 await delivery.drain();assert.equal(calls,2);assert.equal(delivery.state().receipts.at(-1).status,'unknown')
 const reopened=new ChannelDelivery(ctx,r,memory,persistence);await reopened.initialize();await reopened.drain();assert.equal(calls,2)
 notes.push({id:'n3',evidence:'memory3',content:'How was it?',due:Date.now()-1000})
 notes.shift();await reopened.track('personal',notes[0])
 adapter.send=async(_,text,authorize)=>{await reopened.mutate(s=>s.grants=[]);assert.equal(authorize(),false);return {status:'deferred'}}
 await reopened.drain();assert.equal(calls,2)
})
test('a crash while sending never automatically retries a possibly delivered message',async()=>{
 const {r}=registry();await r.catalog()
 const persistence=table()
 await persistence.put('state',{grants:[],receipts:[{id:'n',scope:'personal',content:'test',route,grant:'g',status:'sending',updatedAt:0}],job:null})
 const delivery=new ChannelDelivery({on(){},effect(){}},r,{personal:{}},persistence)
 await delivery.initialize();assert.equal(delivery.state().receipts[0].status,'unknown')
})

test('removing owner identity revokes a private destination even if the peer remains allowed to chat',async()=>{
 const {r,adapter}=registry();await r.catalog()
 assert.ok(r.permitted(route))
 adapter.resolve=()=>({...route,owner:false,audience:'channel:other:account1:private:owner1'})
 assert.equal(r.permitted(route),null)
})
test('a non-QQ adapter normalizes history once and cached history remains readable after unload',async()=>{
 const rows=table(),cache=table(),r=new ChannelRegistry(rows,()=>['inbox'],cache)
 const stop=r.register({id:'other',account:'account1',label:'Other',resolve:()=>route,
  messages:()=>[{seq:1,role:'Alcor',text:'Hello',time:100}]})
 await r.catalog()
 assert.equal((await r.messages('inbox',[{seq:1}]))[0].text,'Hello')
 stop()
 assert.equal((await r.messages('inbox',[{seq:1}]))[0].text,'Hello')
 assert.equal((await r.catalog())[0].available,false)
 assert.equal((await r.catalog())[0].channelName,'Other')
})

test('a dispatch wake requires both the native schedule source and this job’s actual receipt',async()=>{
 const {r}=registry(),persistence=table()
 await persistence.put('state',{grants:[],receipts:[],job:{id:'owned',prompt:'private nonce',sessionId:'dispatch'}})
 let hook,calls=0
 const ctx={on:(name,handler)=>{hook=handler},effect(){},logger:{warn(){}},schedule:{
   edit:async()=>({}),view:async()=>({id:'owned',prompt:'private nonce',target:{sessionId:'dispatch'},lastDelivery:{messageId:'authorized',targetSessionId:'dispatch'}})}}
 const delivery=new ChannelDelivery(ctx,r,{personal:{initiative:false}},persistence)
 await delivery.initialize();delivery.drain=async()=>{calls++}
 const agent={session:{id:'dispatch'}}
 await hook({agent,messages:[{id:'authorized',source:{kind:'user'}}]},()=>{})
 assert.equal(calls,0)
 await hook({agent,messages:[{id:'foreign-schedule',source:{kind:'schedule'}}]},()=>{})
 assert.equal(calls,0)
 await hook({agent,messages:[{id:'authorized',source:{kind:'schedule'}}]},()=>{})
 assert.equal(calls,1)
})

test('turning initiative off invalidates older scoped plans even after the master switch is reopened',async()=>{
 const {r}=registry();await r.catalog()
 const persistence=table(),oldNote={id:'old',content:'old plan',due:Date.now()-1000,generation:1}
 const personal={initiative:true,initiativeGeneration:2,pendingFollowups:()=>[oldNote]}
 const delivery=new ChannelDelivery({},r,{personal},persistence)
 await delivery.mutate(s=>s.grants.push({route,nonce:'allowed',until:Date.now()+60000}))
 await delivery.track('personal',oldNote)
 assert.deepEqual(delivery.state().receipts,[])
 const current={...oldNote,id:'new',generation:2}
 await delivery.track('personal',current)
 assert.equal(delivery.state().receipts[0].id,'new')
})

test('delivery names refresh from the adapter, are cached, and survive reconnect with no outbound operation',async()=>{
 const {r,adapter,rows,release}=registry()
 let reads=0
 adapter.resolve=()=>({...route,label:''})
 adapter.info=async()=>{reads++;return {online:true,name:'纸样讨论组'}}
 await r.catalog()
 assert.equal((await r.catalog(true))[0].label,'纸样讨论组')
 assert.equal((await r.catalog(true))[0].label,'纸样讨论组');assert.equal(reads,1)
 release()
 const reopened=new ChannelRegistry(rows,()=>['inbox'])
 assert.equal((await reopened.catalog(true))[0].label,'纸样讨论组')
})
test('a personal follow-up may choose an authorized desktop instead of sending to the private channel',async()=>{
 const {r,adapter}=registry();await r.catalog()
 const note={id:'desktop-note',content:'Ask later',due:Date.now()-10}
 const personal={initiative:true,desktopDelivery:true,pendingFollowups:()=>[note],nextFollowup:()=>note,
   async reconsider(n,allowed,options){assert.ok(allowed());assert.deepEqual(options,['channel','desktop']);return {...n,content:'For the desk',destination:'desktop'}},
   validReview:()=>true}
 const delivery=new ChannelDelivery({effect(){},on(){}},r,{personal},table())
 delivery.active=true
 await delivery.mutate(state=>state.grants.push({route,nonce:'desk',until:Date.now()+10000}))
 await delivery.track('personal',note)
 adapter.send=()=>assert.fail('Desktop choice sent to QQ')
 await delivery.drain()
 assert.equal(delivery.state().receipts[0].status,'desktop')
 assert.equal(delivery.assigned(note.id),false)
})

test('new trusted QQ destinations stay enabled until revoked; old grants still expire', async () => {
  const peer = { ...route, channel:'qq', owner:false, audience:'qq:account1:private:10002', peer:'10002' }
  const rows = table()
  const r = new ChannelRegistry(rows, () => ['inbox'])
  let trusted = true
  r.register({
    id:'qq', account:'account1', label:'QQ', resolve:() => peer, messages:() => [],
    send:async () => ({ status:'sent' }), canSend:() => true, trustedForDelivery:() => trusted,
  })
  const scoped = { allowInitiative:async () => {}, pendingFollowups:() => [] }
  const delivery = new ChannelDelivery({}, r, { personal:{initiative:false}, storeForAudience:() => scoped }, table())
  await delivery.configure(peer.sessionId, true)
  const grant = delivery.state().grants[0]
  assert.equal(grant.until, null)
  assert.ok(delivery.valid(grant, Date.now() + 30 * 86400000))
  assert.equal(delivery.valid({...grant, until:Date.now()-1}), false)
  const { deliveryStateSchema } = await import('../../packages/alcor-secretary/src/channels/schema.js')
  assert.deepEqual(deliveryStateSchema.parse(delivery.state()), delivery.state())
  assert.equal((await delivery.settings()).targets[0].until, null)
  trusted = false
  assert.equal(delivery.valid(grant), false)
  await assert.rejects(delivery.configure(peer.sessionId, true), /允许交流/)
  await delivery.configure(peer.sessionId, false)
  assert.equal(delivery.hasActiveGrants(), false)
})

test('quiet-time greetings use their original QQ grant and never switch to desktop', async () => {
  const route = {sessionId:'onebot-private-5001',channel:'qq',account:'20001',kind:'private',
    peer:'5001',label:'Friend',audience:'personal',owner:true}
  const channels=new ChannelRegistry(table(),()=>[route.sessionId])
  const messages=[], outcomes=[]
  const note = {id:'hello',kind:'idle',idleGrant:'original',content:'聊聊吗？',due:Date.now()}
  let pending = true
  const personal = {
    initiative:true, desktopDelivery:true,
    considerIdle:async ({grant,sessionId,destination,allowed}) => {
      assert.equal(sessionId,route.sessionId)
      assert.equal(destination,'channel')
      assert.equal(allowed(),true)
      assert.ok(['original','replacement'].includes(grant))
    },
    pendingFollowups:()=>pending ? [note] : [],
    nextFollowup:(_,accepts)=>pending && accepts(note) ? note : null,
    reconsider:async (n,allowed,destinations) => {
      assert.ok(allowed());assert.deepEqual(destinations,['channel'])
      return {...n,stamp:0,destination:'channel'}
    },
    validReview:()=>true,
    recordIdleOutcome:async (id,status)=>{outcomes.push({id,status});pending=false},
  }
  channels.register({id:'qq',account:'20001',label:'QQ',resolve:()=>route,messages:()=>[],
    canSend:()=>true,trustedForDelivery:()=>true,send:async (_,text,allowed)=>{
      assert.ok(allowed());messages.push(text);return {status:'sent',messageId:'synthetic'}
    }})
  await channels.catalog()
  const delivery = new ChannelDelivery({},channels,{personal},table())
  delivery.active=true
  const grant = nonce => delivery.mutate(state=>{state.grants=[{route,nonce,until:null}]})
  await grant('replacement');await delivery.drain()
  assert.deepEqual(delivery.state().receipts,[])
  await grant('original');await delivery.drain();await delivery.drain()
  assert.deepEqual(messages,['聊聊吗？'])
  assert.deepEqual(outcomes,[{id:'hello',status:'sent'}])
})
