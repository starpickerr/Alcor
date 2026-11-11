const {contextBridge,ipcRenderer,webUtils}=require('electron')
const {installPluginBadges}=require('./plugin-badges.cjs')
if(process.isMainFrame && location.protocol==='dsh-app:' && location.hostname==='app'){
  contextBridge.exposeInMainWorld('__DSH_DIRECTORY_PICKER__',{pick:()=>ipcRenderer.invoke('alcor:directory')})
  contextBridge.exposeInMainWorld('__DSH_HOST_PATHS__',{pathFor:file=>webUtils.getPathForFile(file)})
  window.addEventListener('DOMContentLoaded',()=>{
    ipcRenderer.invoke('alcor:environment').then(role=>installPluginBadges(document,role))
  },{once:true})
}
