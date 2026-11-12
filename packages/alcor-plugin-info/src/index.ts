import {Service} from '@deepseek-ai/cordis'
import {Remote,TypertRemoteService} from '@deepseek-ai/dsh-typert-protocol'
import {assignments,applicationEnvironment,publishApplication} from './assignments.js'
import {remote,update} from './remote.js'

export default class PluginModes extends TypertRemoteService {
  static inject=['configEditor','typert','pluginManager','profileContext']
  constructor(ctx){super(ctx,'alcorPluginModes',{namespace:'alcorPluginModes'})}
  async [Service.init](){
    this.ctx.typert.register({package:'@alcor/plugin-info',face:'host',schemas:[],invocations:remote.descriptors,model:{services:[],events:[],objects:[]}})
    publishApplication(this.ctx)
  }
  async entries(){
    if(applicationEnvironment())return []
    const all=this.ctx.configEditor.entries()
    const providers=new Map(Object.entries(assignments))
    return [...providers].flatMap(([name,spec])=>{
      const entry=all.find(row=>row.options.id===spec.entry && row.options.name===name)
      return entry?.fiber?.effectiveConfig && 'productScope' in entry.fiber.effectiveConfig ? [{name,entry,spec}] : []
    })
  }
  @Remote
  async readScopes(request){
    return Object.fromEntries((await this.entries()).map(({name,entry,spec})=>[name,entry.options.config?.productScope ?? spec.initial]))
  }
  @Remote
  async setScope(request){
    if(this.ctx.invocation?.peer?.principal?.kind!=='owner')throw Error('仅客户端所有者可分配插件。')
    const {name,value,previous}=update.parse(request)
    const row=(await this.entries()).find(row=>row.name===name)
    if(!row || (row.spec.exclusive && value==='both'))throw Error('此插件不支持所选范围。')
    await this.ctx.configEditor.edit(row.entry,current=>{
      if((current.productScope ?? row.spec.initial)!==previous)throw Error('范围已改变，请重新选择。')
      return {...current,productScope:value}
    })
    return this.readScopes({})
  }
}
