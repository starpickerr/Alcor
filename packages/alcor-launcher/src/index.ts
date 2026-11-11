import {homedir} from 'node:os'
import {join} from 'node:path'
import {fileURLToPath} from 'node:url'
import {Service} from '@deepseek-ai/cordis'
import {Remote,TypertRemoteService} from '@deepseek-ai/dsh-typert-protocol'
import {prepareEnvironment,officialDesktop,launchEnvironment,desktopApplication} from '../../../scripts/product/environments.mjs'
import {remote} from './remote.js'
import {publishApplication} from '../../alcor-plugin-info/src/assignments.js'

export default class AlcorLauncher extends TypertRemoteService {
  static inject=['typert','profileContext']
  private opening?:Promise<{opened:boolean}>
  constructor(ctx){super(ctx,'alcorLauncher',{namespace:'alcorLauncher'})}
  async [Service.init](){
    publishApplication(this.ctx)
    this.ctx.typert.register({package:'@alcor/launcher',face:'host',schemas:[],invocations:remote.descriptors,model:{services:[],events:[],objects:[]}})
  }
  @Remote
  async open(){
    if(this.ctx.invocation?.peer?.principal?.kind!=='owner')throw Error('仅客户端所有者可打开本地运行环境。')
    if(!this.opening)this.opening=this.openDesktop().finally(()=>{this.opening=undefined})
    return this.opening
  }
  private async openDesktop(){
    const paths=await prepareEnvironment(process.env.ALCOR_ENVIRONMENTS_ROOT ?? join(homedir(),'.local/share/Alcor/environments'),'alcor')
    const desktop=await officialDesktop(desktopApplication(this.ctx.profileContext.installAnchor))
    const archive=fileURLToPath(new URL('../runtime/alcor-secretary.tgz',import.meta.url))
    return new Promise<{opened:boolean}>((resolve,reject)=>{
      void launchEnvironment(paths,desktop,{archive,detached:true,onStarted:()=>resolve({opened:true})})
        .catch(error=>{this.ctx.logger.warn('独立 Alcor 桌面退出：'+error.message);reject(error)})
    })
  }
}
