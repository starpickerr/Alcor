import {spawn} from 'node:child_process'
import {join} from 'node:path'
import {withFileLock} from '@deepseek-ai/dsh-atomic-write'
import {environmentFor,desktopIsRunning} from '../../scripts/product/environments.mjs'
import {ownedChildren,stopChildren} from './child-processes.mjs'

export function hostUrl(value) {
  const url=new URL(value)
  if(url.protocol!=='http:' || url.hostname!=='127.0.0.1' || !url.port || url.username || url.password || url.pathname!=='/' || !url.searchParams.get('token'))throw Error('DSH Host 返回了无效的本地连接。')
  return url.href
}

export function nativeHost(paths,desktop,{onExit=()=>{}}={}) {
  let child,ready,stopping=false,finished,sequence=0,stopWork,children
  const requests=new Map()
  const start=()=>ready ??=new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>{reject(Error('DSH Host 启动超时，请重试。'));void stop()},60000)
    finished=withFileLock(join(paths.base,'desktop-instance'),async()=>{
      if(await desktopIsRunning(paths,desktop))throw Error('请先退出此环境的旧客户端窗口。')
      if(stopping)throw Error('客户端正在退出。')
      const resources=desktop.resources,runtime=join(resources,'app.asar/dsh')
      child=spawn(desktop.binary,['--expose-internals',join(runtime,'node_modules/@deepseek-ai/dsh-desktop-host/lib/index.js'),runtime,paths.profile,
        join(resources,'runtime/primary-runtime'),join(resources,'runtime/primary-runtime/dependencies/pnpm/bin/pnpm.mjs'),join(resources,'runtime/bin')],
        {windowsHide:true,cwd:paths.workspace,env:{...environmentFor(paths),ELECTRON_RUN_AS_NODE:'1',DSH_DESKTOP_NODE_EXECUTABLE:desktop.binary,ALCOR_CLIENT_SHELL:'1'},stdio:['ignore','ignore','pipe','ipc']})
      // Host diagnostics can contain user data; only a generic error crosses into the shell.
      child.stderr.resume()
      await new Promise((done,fail)=>{
        child.once('error',error=>{clearTimeout(timeout);fail(error)})
        child.once('exit',(code)=>{clearTimeout(timeout);reject(Error(stopping?'运行环境已停止。':'DSH Host 已退出，请重试。'));if(!stopping)onExit(code);done()})
        child.on('message',message=>{
          if(!message || typeof message!=='object')return
          if(message.type==='ready'){
            try{clearTimeout(timeout);resolve({url:hostUrl(message.url),pid:child.pid})}catch(error){reject(error);child.kill('SIGTERM')}
          }else if(message.type==='fatal'){reject(Error('DSH Host 启动失败；原数据已保留。'));child.kill('SIGTERM')}
          else if(message.type==='quit-inspection'){
            const request=requests.get(message.requestId)
            if(request){requests.delete(message.requestId);request({activeTasks:message.activeTasks!==false,scheduledTasks:message.scheduledTasks!==false})}
          }
        })
      })
    },{waitMs:0}).catch(error=>{clearTimeout(timeout);reject(error);throw error})
    // Keep failure handled after startup; the owning client displays per-environment failure.
    finished.catch(()=>{})
  })
  const inspect=async()=>{
    if(!child?.connected)return {activeTasks:true,scheduledTasks:true}
    const requestId=++sequence
    return new Promise(resolve=>{
      const timeout=setTimeout(()=>{requests.delete(requestId);resolve({activeTasks:true,scheduledTasks:true})},2500)
      requests.set(requestId,value=>{clearTimeout(timeout);resolve(value)})
      child.send({type:'quit-inspection',requestId},error=>{if(error){clearTimeout(timeout);requests.delete(requestId);resolve({activeTasks:true,scheduledTasks:true})}})
    })
  }
  const stop=()=>stopWork ??= (async()=>{
    stopping=true
    if(!child){await finished?.catch(()=>{});return}
    children ??= child.pid && child.exitCode===null && child.signalCode===null?await ownedChildren(child.pid):[]
    const done=new Promise(resolve=>{if(!child.pid || child.exitCode!==null || child.signalCode!==null)resolve();else child.once('exit',resolve)})
    if(child.connected)child.send({type:'shutdown'},()=>{})
    else child.kill('SIGTERM')
    const timeout=setTimeout(()=>child.kill('SIGTERM'),12000)
    const last=setTimeout(()=>child.kill('SIGKILL'),17000)
    await done;clearTimeout(timeout);clearTimeout(last)
    for(const resolve of requests.values())resolve({activeTasks:false,scheduledTasks:false})
    requests.clear();await finished?.catch(()=>{})
    await stopChildren(children)
  })().catch(error=>{stopWork=undefined;throw error})
  return {start,inspect,stop}
}
