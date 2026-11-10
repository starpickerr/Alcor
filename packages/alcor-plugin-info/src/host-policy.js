import z from '@deepseek-ai/schemastery'
import {permits,sessionMode} from './assignments.js'

export function modeConfig(initial) {
  return z.object({productScope:z.union(['alcor','dsh','both'].map(v=>z.const(v))).default(initial).volatile()})
}

// Limit both the next model catalogue and dispatch from a previously prepared catalogue.
export function guardSource(ctx,config,prefix) {
  const allowed=agent=>permits(config.productScope.get(),sessionMode(ctx,agent))
  ctx.tools.guard(call=>call.agent && call.name.startsWith(prefix) && !allowed(call.agent)
    ? '这个来源没有分配给当前会话。' : undefined)
  const masks=new Map()
  function remove(agent){masks.get(agent)?.dispose();masks.delete(agent)}
  ctx.on('agent/pre-step',({agent},next)=>{
    const names=allowed(agent)?[]:ctx.tools.definitions().map(t=>t.name).filter(n=>n.startsWith(prefix)).sort()
    const key=JSON.stringify(names)
    if(masks.get(agent)?.key!==key){
      remove(agent)
      if(names.length)masks.set(agent,{key,dispose:agent.ctx.tools.restrict({deny:names})})
    }
    return next()
  },{prepend:true})
  ctx.on('agent/disposed',({agent})=>remove(agent))
  ctx.effect(()=>()=>{for(const agent of masks.keys())remove(agent)})
}
