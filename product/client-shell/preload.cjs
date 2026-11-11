const {contextBridge,ipcRenderer}=require('electron')
contextBridge.exposeInMainWorld('alcorShell',{
  state:()=>ipcRenderer.invoke('alcor:state'),
  menu:()=>ipcRenderer.invoke('alcor:menu'),
  switchMode:mode=>ipcRenderer.invoke('alcor:switch',mode),
  retry:()=>ipcRenderer.invoke('alcor:retry'),
  subscribe:listener=>{const receive=(_,state)=>listener(state);ipcRenderer.on('alcor:state',receive);return ()=>ipcRenderer.removeListener('alcor:state',receive)},
})
