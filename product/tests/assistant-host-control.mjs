import http from 'node:http'
import { writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
const root=dirname(process.env.DSH_HOME || '')
if (!root.startsWith('/private/tmp/alcor-assistant-')) throw Error('Synthetic test control requires an isolated DSH home')
export const inject=['alcorSecretary','agents','sessions','sessionController','typertGateway','connection','workspaceRegistry','agentPresets']
export function apply(ctx) {
 const peer=ctx.connection.createPeer({principal:{kind:'owner'},interactions:'none'})
 const server=http.createServer(async(req,res)=>{
  res.setHeader('Content-Type','application/json')
  if(req.headers.authorization!=='Bearer synthetic-control-only'){res.writeHead(403);res.end('{}');return}
  try {
   let body='';for await(const chunk of req) body+=chunk
   const request=JSON.parse(body||'{}')
   let result
   if(req.url==='/rpc') result=await ctx.typertGateway.invoke({namespace:request.namespace,method:request.method,args:request.args??{},peer,signal:AbortSignal.timeout(15000)})
   else if(req.url==='/shared-project' && dirname(request.path)===root) result=(await ctx.workspaceRegistry.create(request.path,'Shared native folder')).id
   else if(req.url==='/delivery-plan') {
     const service=ctx.alcorSecretary,store=service.memory.storeForAudience(request.scope || 'personal')
     const evidence=await store.remember({content:request.content},{owner:true,subject:'owner',source:{kind:'desktop',at:new Date().toISOString(),sessionId:'synthetic-plan'}})
     result=await store.planFollowup({evidence:evidence.id,content:request.content,dueAt:new Date(Date.now()+1500).toISOString(),generation:service.memory.personal.initiativeGeneration})
     await service.delivery.track(request.scope || 'personal',result)
   }
   else if(req.url==='/delivery-debug') result={receipts:ctx.alcorSecretary.delivery.state().receipts.map(r=>({scope:r.scope,status:r.status,authorized:ctx.alcorSecretary.delivery.authorized(r),next:ctx.alcorSecretary.delivery.store(r.scope).nextFollowup()?.id,expected:r.id,manageable:ctx.alcorSecretary.memory.canManageAudience(r.scope)}))}
   else if(req.url==='/delivery-run') result=await ctx.get('schedule').run({id:ctx.alcorSecretary.delivery.state().job.id})
   else if(req.url==='/state') result={workspaces:ctx.workspaceRegistry.list().map(row=>({id:row.id,path:row.path,sessionIds:row.sessionIds})),sessions:ctx.sessions.list().map(row=>({id:row.id,preset:ctx.agentPresets.presetForSession(row)})),memory:ctx.alcorSecretary.memory.personal.status()}
   else {res.writeHead(404);res.end('{}');return}
   res.end(JSON.stringify({ok:true,value:result}))
  } catch(error) {res.writeHead(500);res.end(JSON.stringify({ok:false,error:error.message}))}
 })
 server.listen(0,'127.0.0.1',()=>writeFileSync(root+'/control-ready.json',JSON.stringify({port:server.address().port})))
 ctx.effect(()=>()=>new Promise(resolve=>server.close(resolve)))
}
