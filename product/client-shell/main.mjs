import {app,BaseWindow,WebContentsView,Menu,ipcMain,session,protocol,dialog,shell} from 'electron'
import {readFile} from 'node:fs/promises'
import {writeFileAtomic} from '@deepseek-ai/dsh-atomic-write'
import {join,isAbsolute} from 'node:path'
import {fileURLToPath,pathToFileURL} from 'node:url'
import {prepareEnvironment,officialDesktop,installPlugin} from '../../scripts/product/environments.mjs'
import {prepareProductHome} from '../../scripts/product/home.mjs'
import {nativeHost} from './host.mjs'
import {authenticate,forward} from './transport.mjs'

const directory=fileURLToPath(new URL('.',import.meta.url))
const config=JSON.parse(await readFile(join(directory,'config.json'),'utf8'))
for(const key of ['officialApp','environmentRoot','workbenchDsh','workbenchHome','workbenchWorkspace','workbenchBrowser','clientData'])if(typeof config[key]!=='string' || !isAbsolute(config[key]))throw Error('客户端路径配置无效。')
app.setName('Alcor')
await prepareProductHome(config.clientData,fileURLToPath(new URL('../../',import.meta.url)),'client')
app.setPath('userData',config.clientData)
protocol.registerSchemesAsPrivileged([{scheme:'dsh-app',privileges:{standard:true,secure:true,supportFetchAPI:true,corsEnabled:true,stream:true,codeCache:true}}])
const singleton=app.requestSingleInstanceLock()
if(!singleton){app.quit()}else{
 void app.whenReady().then(async()=>{
 app.setAccessibilitySupportEnabled(true)
 const desktop=await officialDesktop(config.officialApp)
 const paths={alcor:await prepareEnvironment(config.environmentRoot,'alcor'),dsh:{base:config.workbenchDsh,kind:'dsh',root:config.environmentRoot,
   dsh:config.workbenchDsh,home:config.workbenchHome,profile:join(config.workbenchDsh,'profiles/desktop'),workspace:config.workbenchWorkspace,userData:config.workbenchBrowser}}
 const dshManifest=JSON.parse(await readFile(join(paths.dsh.profile,'package.json'),'utf8'))
 if(dshManifest.dependencies?.['@alcor/secretary'] || dshManifest.dependencies?.['@alcor/launcher'])throw Error('请先完成工作台插件归属更新；未启动共用的业务环境。')
 const alcorManifest=JSON.parse(await readFile(join(paths.alcor.profile,'package.json'),'utf8'))
 if(!alcorManifest.dependencies?.['@alcor/secretary'])await installPlugin(paths.alcor,desktop,join(directory,'alcor-secretary.tgz'))
 const saved=join(config.clientData,'alcor-shell-state.json')
 let mode='alcor'
 try{const value=JSON.parse(await readFile(saved,'utf8'));if(['alcor','dsh'].includes(value.mode))mode=value.mode}catch(error){if(error.code!=='ENOENT')throw Error('上次的客户端状态无法读取，原文件已保留。')}
 const states={alcor:{phase:'loading'},dsh:{phase:'loading'}}
 let window,shellView,quitting=false,quittingWork
 const snapshot=()=>({mode,phases:Object.fromEntries(Object.entries(states).map(([key,value])=>[key,value.phase]))})
 const layout=()=>{
   if(!window || window.isDestroyed())return
   const [width,height]=window.getContentSize()
   for(const [key,value] of Object.entries(states))if(value.view){
     value.view.setBounds({x:0,y:44,width,height:Math.max(0,height-44)})
     const active=key===mode && value.phase==='ready'
     if(active && !value.attached){window.contentView.addChildView(value.view);value.attached=true}
     if(!active && value.attached){window.contentView.removeChildView(value.view);value.attached=false}
   }
   shellView.setBounds({x:0,y:0,width,height:states[mode].phase==='ready'?44:height})
   shellView.webContents.send('alcor:state',snapshot())
 }
 const focus=()=>{if(window.isMinimized())window.restore();window.show();window.focus()}
 const owner=event=>{
   if(event.sender!==shellView.webContents || event.senderFrame!==shellView.webContents.mainFrame || event.senderFrame.url!==pathToFileURL(join(directory,'index.html')).href)throw Error('拒绝非客户端界面的操作。')
 }
 const content=event=>{
   const state=Object.values(states).find(value=>value.view?.webContents===event.sender)
   if(!state || event.senderFrame!==event.sender.mainFrame || !event.senderFrame.url.startsWith('dsh-app://app/'))throw Error('拒绝其他运行环境的操作。')
   return state
 }
 let switching=Promise.resolve()
 const switchMode=next=>{
   if(!['alcor','dsh'].includes(next))throw Error('未知的使用模式。')
   const changed=switching.then(async()=>{
     await writeFileAtomic(saved,JSON.stringify({mode:next})+'\n',{mode:0o600})
     mode=next;layout();states[mode].view?.webContents.focus();return snapshot()
   })
   switching=changed.catch(()=>{});return changed
 }
 const start=async kind=>{
   const state=states[kind];state.phase='loading';layout()
   state.host=nativeHost(paths[kind],desktop,{onExit:()=>{if(!quitting){state.phase='failed';layout()}}})
   try{
     state.ready=await state.host.start();state.cookie=await authenticate(state.ready.url)
     const browser=session.fromPartition('persist:'+kind)
     if(await browser.protocol.isProtocolHandled('dsh-app'))await browser.protocol.unhandle('dsh-app')
     browser.protocol.handle('dsh-app',request=>{
       const url=new URL(request.url)
       if(url.hostname!=='app')return new Response(null,{status:404})
       if(url.pathname==='/' || url.pathname==='/index.html')return (async()=>{
         const response=await forward(request,state.ready.url,state.cookie)
         const html=(await response.text()).replace('<head>',`<head><script>globalThis.__DSH_TRANSPORT__=${JSON.stringify({ownsHost:true,streamBaseUrl:new URL(state.ready.url).origin})}</script>`)
         return new Response(html,{status:response.status,headers:response.headers})
       })()
       return forward(request,state.ready.url,state.cookie)
     })
     state.view=new WebContentsView({webPreferences:{session:browser,preload:join(directory,'content-preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true,backgroundThrottling:false}})
     browser.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false))
     browser.webRequest.onBeforeSendHeaders({urls:['ws://127.0.0.1/*','wss://127.0.0.1/*']},(details,callback)=>{
       const target=new URL(state.ready.url),requested=new URL(details.url)
       if(details.webContentsId!==state.view.webContents.id || requested.host!==target.host || requested.protocol!=='ws:'){callback({});return}
       const headers=Object.fromEntries(Object.entries(details.requestHeaders).map(([name,value])=>[name.toLowerCase(),value]))
       if(headers.origin!=='dsh-app://app'){callback({cancel:true});return}
       callback({requestHeaders:{...headers,origin:target.origin,cookie:state.cookie,'sec-fetch-site':'same-origin'}})
     })
     state.view.webContents.setWindowOpenHandler(({url})=>{if(['http:','https:'].includes(new URL(url).protocol))void shell.openExternal(url);return {action:'deny'}})
     state.view.webContents.on('will-navigate',(event,url)=>{if(!url.startsWith('dsh-app://app/')){event.preventDefault();if(['http:','https:'].includes(new URL(url).protocol))void shell.openExternal(url)}})
     state.view.webContents.on('render-process-gone',()=>{state.phase='failed';layout()})
     layout()
     await state.view.webContents.loadURL('dsh-app://app/')
     state.phase='ready';layout()
   }catch{state.phase='failed';layout()}
 }
 ipcMain.handle('alcor:state',event=>{owner(event);return snapshot()})
 ipcMain.handle('alcor:switch',(event,next)=>{owner(event);return switchMode(next)})
 ipcMain.handle('alcor:menu',event=>{owner(event);return new Promise(resolve=>{
   const menu=Menu.buildFromTemplate([{label:'Alcor',type:'radio',checked:mode==='alcor',click:()=>{void switchMode('alcor').catch(()=>dialog.showErrorBox('切换失败','当前界面已保留，请重试。'))}},{label:'DeepSeek Harness',type:'radio',checked:mode==='dsh',click:()=>{void switchMode('dsh').catch(()=>dialog.showErrorBox('切换失败','当前界面已保留，请重试。'))}}])
   menu.popup({window,x:88,y:40,callback:()=>resolve(snapshot())})
 })})
 ipcMain.handle('alcor:retry',async event=>{owner(event);const state=states[mode];if(state.phase!=='failed')return snapshot();await state.host?.stop();if(state.view){if(state.attached)window.contentView.removeChildView(state.view);state.attached=false;state.view.webContents.close({waitForBeforeUnload:false});state.view=undefined}void start(mode);return snapshot()})
 ipcMain.on('alcor:boot-failed',event=>{const state=content(event);state.phase='failed';layout()})
 ipcMain.handle('alcor:directory',async event=>{content(event);const value=await dialog.showOpenDialog(window,{properties:['openDirectory','createDirectory']});return value.canceled?null:value.filePaths[0]??null})
 const quit=()=>quittingWork ??= (async()=>{
   const inspections=await Promise.all(Object.values(states).map(value=>value.host?.inspect()))
   if(inspections.some(value=>value?.activeTasks)){
     const response=await dialog.showMessageBox(window,{type:'question',message:'退出 Alcor？',detail:'仍有任务在运行，退出后会中断。',buttons:['取消','退出'],defaultId:0,cancelId:0})
     if(response.response!==1){quittingWork=undefined;return}
   }
   quitting=true;await Promise.all(Object.values(states).map(value=>value.host?.stop()));for(const state of Object.values(states))state.view?.webContents.close({waitForBeforeUnload:false});shellView.webContents.close({waitForBeforeUnload:false});app.quit()
 })()
 app.on('before-quit',event=>{if(!quitting){event.preventDefault();void quit()}})
 for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>{quitting=true;void Promise.all(Object.values(states).map(value=>value.host?.stop())).finally(()=>app.exit(0))})
 app.on('second-instance',()=>focus());app.on('activate',()=>{if(window)focus()})
 const menu=Menu.buildFromTemplate([{label:'Alcor',submenu:[{role:'about'},{type:'separator'},{role:'hide'},{role:'hideOthers'},{role:'unhide'},{type:'separator'},{label:'退出 Alcor',accelerator:'CommandOrControl+Q',click:()=>void quit()}]},{role:'editMenu'},{role:'windowMenu'}]);Menu.setApplicationMenu(menu)
 window=new BaseWindow({title:'Alcor',width:1280,height:900,minWidth:640,minHeight:540,titleBarStyle:'hiddenInset',trafficLightPosition:{x:12,y:14},backgroundColor:'#f7f7f8'})
 // Hidden native views must still receive mode updates and finish their pending work.
 shellView=new WebContentsView({webPreferences:{preload:join(directory,'preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true,backgroundThrottling:false}})
 window.contentView.addChildView(shellView)
 const [width,height]=window.getContentSize();shellView.setBounds({x:0,y:0,width,height})
 window.on('close',event=>{if(!quitting){event.preventDefault();window.hide()}})
 window.on('resize',layout)
 await shellView.webContents.loadFile(join(directory,'index.html'));window.show()
 void Promise.all(['alcor','dsh'].map(start))
 }).catch(async()=>{await dialog.showMessageBox({type:'error',message:'Alcor 启动失败',detail:'原数据已保留，请检查安装与运行环境。'});app.exit(1)})
}
