function pluginLabel(name,role,market=false) {
  if(name==='@alcor/extension')return 'Alcor 环境'
  return (market?'安装到 ':'')+(role==='alcor'?'Alcor':'DSH')+' 环境'
}

function installPluginBadges(document,role) {
  const selector='[data-plugin-panel] :is([data-plugin-package],[data-plugin-item],[data-market-entry],[data-market-detail])'
  const update=()=>{
    for(const card of document.querySelectorAll(selector)){
      const data=card.dataset,name=data.pluginPackage??data.pluginItem??data.marketEntry??data.marketDetail
      const target=(data.marketDetail?card.querySelector('h3'):card.querySelector('button[aria-describedby],button[aria-label]'))?.parentElement?.parentElement
      if(!target)continue
      let badge=target.querySelector(':scope > .alcor-client-plugin-scope')
      if(!badge){badge=document.createElement('div');badge.className='alcor-client-plugin-scope';badge.style.cssText='font-size:12px;color:var(--dsw-alias-label-secondary);margin-top:4px';target.append(badge)}
      const text=pluginLabel(name,role,data.marketEntry!==undefined||data.marketDetail!==undefined)
      if(badge.textContent!==text)badge.textContent=text
    }
  }
  const observer=new MutationObserver(update)
  observer.observe(document.documentElement,{childList:true,subtree:true})
  update()
  return ()=>observer.disconnect()
}
module.exports={pluginLabel,installPluginBadges}
