import {z} from 'zod'
import {createTypertRemoteInfo} from '@deepseek-ai/dsh-typert-protocol/remote-info'

export const remote=createTypertRemoteInfo({package:'@alcor/launcher',methods:{
  'alcorLauncher/open':{service:'alcorLauncher',invocation:{kind:'direct'},parameters:[],
    result:{mode:'strict',typeSymbol:'@alcor/launcher#opened',create:()=>z.object({opened:z.boolean()}).strict()}},
}},z)
