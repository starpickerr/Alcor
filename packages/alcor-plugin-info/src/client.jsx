import React from 'react'
import {pluginScope} from './scope.js'

export const inject = ['layout', 'slots', 'remote', 'remote.pluginManager']

function ScopeDetail({subject}) {
  const {pkg} = subject
  const [scope, background] = pluginScope(pkg?.name ?? subject.id, {item:subject.kind==='item',version:pkg?.version})
  return <small className="alcor-plugin-facts">
    <strong>{subject.kind==='row' ? '所属插件适用' : '适用'} · {scope}</strong>
    <span>启用后切换模式 · {background}</span>
  </small>
}

const cards = '[data-plugin-package], [data-plugin-item], [data-market-entry], [data-market-detail]'
const labelClass = 'alcor-plugin-scope'
const style = `
 .alcor-plugin-scope,.alcor-plugin-facts { display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 10px;font-size:12px;line-height:18px;color:var(--dsw-alias-label-secondary); }
.alcor-plugin-scope { pointer-events:none; }
.alcor-plugin-facts { flex-basis:100%; }
.alcor-plugin-scope strong,.alcor-plugin-facts strong { font-weight:500;color:var(--dsw-alias-label-primary); }
`

// DSH exposes detail slots but no installed-card slot in the supported releases.
// Use its stable card identities and leave every native control and its events with DSH.
function labelCards(versions) {
  for (const card of document.querySelectorAll('[data-plugin-panel] :is(' + cards + ')')) {
    const {pluginPackage, pluginItem, marketEntry, marketDetail} = card.dataset
    const name = pluginPackage ?? pluginItem ?? marketEntry ?? marketDetail
    const [scope, background] = pluginScope(name, {item:pluginItem !== undefined, version:versions.get(name)})
    const title = marketDetail ? card.querySelector('h3') : card.querySelector('button[aria-describedby], button[aria-label]')
    const target = title?.parentElement?.parentElement
    if (!target) continue
    let label = target.querySelector(':scope > .' + labelClass)
    if (!label) {
      label = document.createElement('small')
      label.className = labelClass
      label.append(document.createElement('strong'), document.createElement('span'))
      target.append(label)
    }
    const behavior = (card.dataset.pluginStatus === 'running' || pluginItem ? '切换后 · ' : '启用后切换 · ') + background
    if (label.firstChild.textContent !== scope) label.firstChild.textContent = scope
    if (label.lastChild.textContent !== behavior) label.lastChild.textContent = behavior
  }
}

export function apply(ctx) {
  ctx.slots.inject('plugins.detail.badge', () => ctx.slots.register({
    name:'plugins.detail.badge', id:'alcor-plugin-scope', order:-20,
  }, ScopeDetail))
  ctx.effect(() => {
    const sheet=document.createElement('style')
    sheet.textContent=style
    document.head.append(sheet)
    let observer, generation=0, versions=new Map()
    const refresh=async () => {
      if (!observer) return
      const revision=++generation
      try {
        const result=await ctx.remote.pluginManager.listBundles()
        if (revision!==generation || !result.ok) return
        versions=new Map(result.value.map(bundle=>[bundle.name,bundle.version]))
        labelCards(versions)
      } catch { /* Keep unverified versions unlabeled until the next inventory update. */ }
    }
    const stop=()=>{
      ++generation
      observer?.disconnect()
      observer=undefined
      document.querySelectorAll('.'+labelClass).forEach(label=>label.remove())
    }
    const navigate=()=>{
      if(ctx.layout.panelInfo.getSnapshot().activePanelId!=='plugins') {
        if(observer) stop()
        return
      }
      if(observer) return
      observer=new MutationObserver(changes=>{
        if(changes.some(change=>change.type==='attributes' || [...change.addedNodes].some(node=>
          node.nodeType===1 && (node.matches(cards) || node.querySelector(cards))))) labelCards(versions)
      })
      observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['data-plugin-package','data-plugin-item','data-plugin-status','data-market-entry','data-market-detail']})
      labelCards(versions)
      void refresh()
    }
    const unsubscribe=ctx.layout.panelInfo.subscribe(navigate)
    const changed=ctx.remote.$on('plugin-manager/changed',refresh)
    const reset=ctx.on('connection/reset',refresh)
    navigate()
    return ()=>{unsubscribe();changed();reset();stop();sheet.remove()}
  })
}
