const modeButton=document.getElementById('mode')
function render(state){
  const name=state.mode==='alcor'?'Alcor':'DeepSeek Harness',phase=state.phases[state.mode]
  document.getElementById('mode-name').textContent=name
  document.getElementById('logo').src=state.mode==='alcor'?'bear.svg':'fish.svg'
  modeButton.setAttribute('aria-label','切换使用模式，当前 '+name)
  document.getElementById('status').hidden=phase==='ready'
  document.getElementById('message').textContent=phase==='failed'?'暂时无法打开 '+name+'，原数据已保留。':(phase==='stopping'?'正在关闭 ':'正在打开 ')+name+'…'
  document.getElementById('retry').hidden=phase!=='failed'
}
modeButton.onclick=async()=>{modeButton.setAttribute('aria-expanded','true');try{render(await window.alcorShell.menu())}finally{modeButton.setAttribute('aria-expanded','false')}}
document.getElementById('retry').onclick=async()=>render(await window.alcorShell.retry())
window.alcorShell.subscribe(render);window.alcorShell.state().then(render)
