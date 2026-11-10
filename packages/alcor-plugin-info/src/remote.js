import {z} from 'zod'
import {createTypertRemoteInfo} from '@deepseek-ai/dsh-typert-protocol/remote-info'
const scope=z.enum(['both','alcor','dsh'])
export const update=z.object({name:z.string(),value:scope,previous:scope}).strict()
const snapshot=z.record(z.string(),scope)
export const remote=createTypertRemoteInfo({package:'@alcor/plugin-info',methods:Object.fromEntries(
  Object.entries({readScopes:z.object({}).strict(),setScope:update}).map(([name,input])=>['alcorPluginModes/'+name,{
    service:'alcorPluginModes',invocation:{kind:'direct'},parameters:[{name:'request',source:'json',codec:{mode:'strict',typeSymbol:'@alcor/plugin-info#'+name,create:()=>input}}],
    result:{mode:'strict',typeSymbol:'@alcor/plugin-info#scopes',create:()=>snapshot},
  }]))},z)
