const {contextBridge,ipcRenderer,webUtils}=require('electron')
if(process.isMainFrame && location.protocol==='dsh-app:' && location.hostname==='app'){
  contextBridge.exposeInMainWorld('__DSH_DIRECTORY_PICKER__',{pick:()=>ipcRenderer.invoke('alcor:directory')})
  contextBridge.exposeInMainWorld('__DSH_HOST_PATHS__',{pathFor:file=>webUtils.getPathForFile(file)})
}
