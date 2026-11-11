import {app,BaseWindow,WebContentsView,Menu,ipcMain,session,protocol,dialog,shell} from 'electron'
import {readFile} from 'node:fs/promises'
import {watchFile,unwatchFile} from 'node:fs'
import {writeFileAtomic} from '@deepseek-ai/dsh-atomic-write'
import {join,isAbsolute} from 'node:path'
import {fileURLToPath,pathToFileURL} from 'node:url'
import {prepareEnvironment,officialDesktop,installPlugin,cachePluginArchive,runPluginCli} from '../../scripts/product/environments.mjs'
import {prepareProductHome} from '../../scripts/product/home.mjs'
import {nativeHost} from './host.mjs'
import {authenticate,forward} from './transport.mjs'
import {extensionLifecycle,extensionSelected} from './extension-lifecycle.mjs'

const directory=fileURLToPath(new URL('.',import.meta.url))
const config=JSON.parse(await readFile(join(directory,'config.json'),'utf8'))
const requiredPaths=['officialApp','environmentRoot','clientData']
if(!config.managedWorkbench)requiredPaths.push('workbenchDsh','workbenchHome','workbenchWorkspace','workbenchBrowser')
for(const key of requiredPaths)if(typeof config[key]!=='string' || !isAbsolute(config[key]))throw Error('客户端路径配置无效。')
app.setName('Alcor')
await prepareProductHome(config.clientData,fileURLToPath(new URL('../../',import.meta.url)),'client')
app.setPath('userData',config.clientData)
protocol.registerSchemesAsPrivileged([{scheme:'dsh-app',privileges:{standard:true,secure:true,supportFetchAPI:true,corsEnabled:true,stream:true,codeCache:true}}])
const singleton=app.requestSingleInstanceLock()
if(!singleton){app.quit()}else{
 void app.whenReady().then(async()=>{
 app.setAccessibilitySupportEnabled(true)
 const desktop=await officialDesktop(config.officialApp)
 const paths={alcor:await prepareEnvironment(config.environmentRoot,'alcor'),dsh:config.managedWorkbench ? await prepareEnvironment(config.environmentRoot,'dsh') : {base:config.workbenchDsh,kind:'dsh',root:config.environmentRoot,
   dsh:config.workbenchDsh,home:config.workbenchHome,profile:join(config.workbenchDsh,'profiles/desktop'),workspace:config.workbenchWorkspace,userData:config.workbenchBrowser,packages:join(config.clientData,'packages')}}
 const dshManifest=JSON.parse(await readFile(join(paths.dsh.profile,'package.json'),'utf8'))
 if(dshManifest.dependencies?.['@alcor/secretary'] || dshManifest.dependencies?.['@alcor/launcher'])throw Error('请先完成工作台插件归属更新；未启动共用的业务环境。')
 for(const kind of ['dsh','alcor']){
   const manifest=JSON.parse(await readFile(join(paths[kind].profile,'package.json'),'utf8'))
   if(manifest.dependencies?.['@alcor/plugin-info'])await runPluginCli(paths[kind],desktop,['plugin','--profile','desktop','remove','@alcor/plugin-info'])
 }
 const installed=join(config.clientData,'alcor-extension-installed.json')
 try{if(JSON.parse(await readFile(installed,'utf8')).version!==1)throw Error('Alcor 安装记录无效，原数据已保留。')}catch(error){
   if(error.code!=='ENOENT')throw error
   if(!dshManifest.dependencies?.['@alcor/extension'])await installPlugin(paths.dsh,desktop,await cachePluginArchive(paths.dsh,join(directory,'alcor-extension.tgz')))
   await writeFileAtomic(installed,JSON.stringify({version:1})+'\n',{mode:0o600})
 }
 const saved=join(config.clientData,'alcor-shell-state.json')
 let mode='dsh',restoreAlcor=true
 try{const value=JSON.parse(await readFile(saved,'utf8'));restoreAlcor=value.mode==='alcor'}catch(error){if(error.code!=='ENOENT')throw Error('上次的客户端状态无法读取，原文件已保留。')}
 const states={alcor:{phase:'disabled',generation:0},dsh:{phase:'loading',generation:0}}
 let window,shellView,quitting=false,quittingWork
 const phaseOf=kind=>kind==='alcor'?({starting:'loading',stopping:'stopping',ready:'ready',disabled:'disabled',failed:'failed'}[lifecycle.snapshot().phase]):states[kind].phase
 const snapshot=()=>({mode,extension:lifecycle.snapshot(),phases:Object.fromEntries(Object.keys(states).map(key=>[key,phaseOf(key)]))})
 const layout=()=>{
   if(!window || window.isDestroyed())return
   const [width,height]=window.getContentSize()
   for(const [key,value] of Object.entries(states))if(value.view && !value.view.webContents.isDestroyed()){
     // Keep native window ownership while switching; detached views can stop painting.
     if(!value.attached){window.contentView.addChildView(value.view);value.attached=true}
     value.view.setBounds({x:0,y:44,width,height:Math.max(0,height-44)})
     const active=key===mode && phaseOf(key)==='ready'
     value.view.setVisible(active)
   }
   shellView.setBounds({x:0,y:0,width,height:phaseOf(mode)==='ready'?44:height})
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
     if(next==='alcor' && !lifecycle.snapshot().desired)throw Error('请先在 DSH 插件页启用 Alcor。')
     await writeFileAtomic(saved,JSON.stringify({mode:next})+'\n',{mode:0o600})
     mode=next;layout();states[mode].view?.webContents.focus();return snapshot()
   })
   switching=changed.catch(()=>{});return changed
 }
 const stopView=kind=>{
   const state=states[kind]
   return state.stopping ??= (async()=>{
   state.generation++;state.phase='stopping'
   state.view?.setVisible(false)
   layout()
   const browser=session.fromPartition('persist:'+kind)
   if(await browser.protocol.isProtocolHandled('dsh-app'))await browser.protocol.unhandle('dsh-app')
   // Keep the view alive until the Host and its child processes have stopped.
   await state.host?.stop()
   if(state.view){
     if(state.attached){window.contentView.removeChildView(state.view);state.attached=false}
     const contents=state.view.webContents
     if(!contents.isDestroyed()){
       let timer,closed
       try{await new Promise((done,fail)=>{closed=done;timer=setTimeout(()=>fail(Error('Alcor 界面尚未关闭，请重试。')),10000);contents.once('destroyed',closed);contents.close({waitForBeforeUnload:false})})}
       finally{clearTimeout(timer);contents.off('destroyed',closed)}
     }
     state.view=undefined
   }
   state.host=undefined;state.cookie=undefined;state.ready=undefined;state.phase=kind==='alcor'?'disabled':'failed';layout()
   })().finally(()=>{state.stopping=undefined})
 }
 const lifecycle=extensionLifecycle({
   start:async()=>{
     await stopView('alcor')
     const manifest=JSON.parse(await readFile(join(paths.alcor.profile,'package.json'),'utf8'))
     if(!manifest.dependencies?.['@alcor/secretary'])await installPlugin(paths.alcor,desktop,await cachePluginArchive(paths.alcor,join(directory,'alcor-secretary.tgz')))
     if(quitting || !lifecycle.snapshot().desired)throw Error('Alcor 启动已取消。')
     await start('alcor')
   },
   stop:()=>stopView('alcor'),
   selectWorkbench:async()=>{restoreAlcor=false;await switchMode('dsh')},
   changed:value=>{if(value.phase==='failed')states.alcor.phase='failed';layout()},
 })
 let profileRevision=0
 const updateExtension=async revision=>{
   if(quitting)return
   let manifest
   try{manifest=JSON.parse(await readFile(join(paths.dsh.profile,'package.json'),'utf8'));extensionSelected(manifest)}
   catch(error){if(revision===profileRevision)await lifecycle.setEnabled(false);throw error}
   if(revision!==profileRevision || quitting)return
   const state=await lifecycle.setEnabled(extensionSelected(manifest))
   if(revision!==profileRevision || quitting)return
   if(state.enabled && restoreAlcor){restoreAlcor=false;await switchMode('alcor')}
 }
 let reportedFailure=false
 const syncExtension=()=>{const revision=++profileRevision;void updateExtension(revision).then(()=>{if(revision===profileRevision)reportedFailure=false},()=>{
   if(!quitting && revision===profileRevision && !reportedFailure){reportedFailure=true;dialog.showErrorBox('Alcor 启停未完成','请检查插件状态并重试；已有数据已保留。')}
 })}
 const start=async kind=>{
   const state=states[kind],generation=++state.generation;state.phase='loading';layout()
   const current=()=>{if(state.generation!==generation || quitting)throw Error('运行环境已停止。')}
   state.host=nativeHost(paths[kind],desktop,{onExit:()=>{if(!quitting){state.phase='failed';if(kind==='alcor')lifecycle.invalidate();layout();if(kind==='dsh')void lifecycle.setEnabled(false).catch(()=>{})}}})
   try{
     const ready=await state.host.start();current()
     const cookie=await authenticate(ready.url);current()
     state.ready=ready;state.cookie=cookie
     const browser=session.fromPartition('persist:'+kind)
     if(await browser.protocol.isProtocolHandled('dsh-app'))await browser.protocol.unhandle('dsh-app')
     browser.protocol.handle('dsh-app',request=>{
       const url=new URL(request.url)
       if(url.hostname!=='app' || state.generation!==generation)return new Response(null,{status:404})
       if(url.pathname==='/' || url.pathname==='/index.html')return (async()=>{
         const response=await forward(request,ready.url,cookie)
         const html=(await response.text()).replace('<head>',`<head><script>globalThis.__DSH_TRANSPORT__=${JSON.stringify({ownsHost:true,streamBaseUrl:new URL(ready.url).origin})}</script>`)
         return new Response(html,{status:response.status,headers:response.headers})
       })()
       return forward(request,ready.url,cookie)
     })
     state.view=new WebContentsView({webPreferences:{session:browser,preload:join(directory,'content-preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true,backgroundThrottling:false}})
     browser.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false))
     browser.webRequest.onBeforeSendHeaders({urls:['ws://127.0.0.1/*','wss://127.0.0.1/*']},(details,callback)=>{
       const target=new URL(ready.url),requested=new URL(details.url)
       if(state.generation!==generation || details.webContentsId!==state.view?.webContents.id || requested.host!==target.host || requested.protocol!=='ws:'){callback({cancel:true});return}
       const headers=Object.fromEntries(Object.entries(details.requestHeaders).map(([name,value])=>[name.toLowerCase(),value]))
       if(headers.origin!=='dsh-app://app'){callback({cancel:true});return}
       callback({requestHeaders:{...headers,origin:target.origin,cookie,'sec-fetch-site':'same-origin'}})
     })
     state.view.webContents.setWindowOpenHandler(({url})=>{if(['http:','https:'].includes(new URL(url).protocol))void shell.openExternal(url);return {action:'deny'}})
     state.view.webContents.on('will-navigate',(event,url)=>{if(!url.startsWith('dsh-app://app/')){event.preventDefault();if(['http:','https:'].includes(new URL(url).protocol))void shell.openExternal(url)}})
     state.view.webContents.on('render-process-gone',()=>{if(state.generation===generation && !quitting){state.phase='failed';if(kind==='alcor')lifecycle.invalidate();layout()}})
     layout()
     await state.view.webContents.loadURL('dsh-app://app/')
     current()
     state.phase='ready';layout()
     if(kind==='dsh')syncExtension()
   }catch(error){if(state.generation===generation){await stopView(kind);state.phase='failed';layout()}throw error}
 }
 ipcMain.handle('alcor:state',event=>{owner(event);return snapshot()})
 ipcMain.handle('alcor:switch',(event,next)=>{owner(event);return switchMode(next)})
 ipcMain.handle('alcor:menu',event=>{owner(event);return new Promise(resolve=>{
   const menu=Menu.buildFromTemplate([{label:lifecycle.snapshot().desired?'Alcor':'Alcor（已关闭）',enabled:lifecycle.snapshot().desired,type:'radio',checked:mode==='alcor',click:()=>{void switchMode('alcor').catch(()=>dialog.showErrorBox('切换失败','当前界面已保留，请重试。'))}},{label:'DeepSeek Harness',type:'radio',checked:mode==='dsh',click:()=>{void switchMode('dsh').catch(()=>dialog.showErrorBox('切换失败','当前界面已保留，请重试。'))}}])
   menu.popup({window,x:88,y:40,callback:()=>resolve(snapshot())})
 })})
 ipcMain.handle('alcor:retry',async event=>{owner(event);if(phaseOf(mode)!=='failed')return snapshot();if(mode==='alcor')void lifecycle.setEnabled(true).catch(()=>{});else{await stopView('dsh');void start('dsh').catch(()=>{})}return snapshot()})
 ipcMain.on('alcor:boot-failed',event=>{const state=content(event);state.phase='failed';layout()})
 ipcMain.handle('alcor:directory',async event=>{content(event);const value=await dialog.showOpenDialog(window,{properties:['openDirectory','createDirectory']});return value.canceled?null:value.filePaths[0]??null})
 ipcMain.handle('alcor:environment',event=>{const state=content(event);return state===states.alcor?'alcor':'dsh'})
 const quit=()=>quittingWork ??= (async()=>{
   const inspections=await Promise.all(Object.values(states).map(value=>value.host?.inspect()))
   if(inspections.some(value=>value?.activeTasks)){
     const response=await dialog.showMessageBox(window,{type:'question',message:'退出 Alcor？',detail:'仍有任务在运行，退出后会中断。',buttons:['取消','退出'],defaultId:0,cancelId:0})
     if(response.response!==1){quittingWork=undefined;return}
   }
   quitting=true;unwatchFile(join(paths.dsh.profile,'package.json'))
   try{await Promise.all([lifecycle.close(),stopView('dsh')]);shellView.webContents.close({waitForBeforeUnload:false});app.quit()}
   catch{quitting=false;quittingWork=undefined;states.alcor.phase='failed';states.dsh.phase='failed';layout();dialog.showErrorBox('退出尚未完成','仍有后台进程未能停止，请重试退出；已有数据已保留。')}
 })()
 app.on('before-quit',event=>{if(!quitting){event.preventDefault();void quit()}})
 for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>{quitting=true;unwatchFile(join(paths.dsh.profile,'package.json'));void Promise.all([lifecycle.close(),stopView('dsh')]).then(()=>app.exit(0),()=>app.exit(1))})
 app.on('second-instance',()=>focus());app.on('activate',()=>{if(window)focus()})
 const menu=Menu.buildFromTemplate([{label:'Alcor',submenu:[{role:'about'},{type:'separator'},{role:'hide'},{role:'hideOthers'},{role:'unhide'},{type:'separator'},{label:'退出 Alcor',accelerator:'CommandOrControl+Q',click:()=>void quit()}]},{role:'editMenu'},{role:'windowMenu'}]);Menu.setApplicationMenu(menu)
 window=new BaseWindow({title:'Alcor',width:1280,height:900,minWidth:640,minHeight:540,...(process.platform==='darwin'?{titleBarStyle:'hiddenInset',trafficLightPosition:{x:12,y:14}}:{autoHideMenuBar:true}),backgroundColor:'#f7f7f8'})
 // Hidden native views must still receive mode updates and finish their pending work.
 shellView=new WebContentsView({webPreferences:{preload:join(directory,'preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true,backgroundThrottling:false}})
 window.contentView.addChildView(shellView)
 const [width,height]=window.getContentSize();shellView.setBounds({x:0,y:0,width,height})
 window.on('close',event=>{if(!quitting){event.preventDefault();if(process.platform==='win32')void quit();else window.hide()}})
 window.on('resize',layout)
 await shellView.webContents.loadFile(join(directory,'index.html'));window.show()
 // Native selection is authoritative even when the Host cannot hot-reload a bundle.
 watchFile(join(paths.dsh.profile,'package.json'),{interval:250,persistent:false},syncExtension)
 void start('dsh').catch(()=>{})
 }).catch(async()=>{await dialog.showMessageBox({type:'error',message:'Alcor 启动失败',detail:'原数据已保留，请检查安装与运行环境。'});app.exit(1)})
}
