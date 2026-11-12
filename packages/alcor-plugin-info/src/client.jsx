import React, {useLayoutEffect, useRef, useSyncExternalStore} from 'react'
import {remote} from './remote.js'
import {assignments,applicationEnvironment,modeNames} from './assignments.js'
import {pluginScope} from './scope.js'

export const inject = ['layout', 'slots', 'remote', 'remote.pluginManager']

const modes = {both:'共用', alcor:'Alcor', dsh:'DSH工作台'}
const fixedNames = {'两边共用':modes.both, 'DSH 工作台':modes.dsh}
const cards = '[data-plugin-package], [data-plugin-item], [data-market-entry], [data-market-detail]'
const labelClass = 'alcor-plugin-scope'

function renderScope(label, name, scope, background, selected, editable, store) {
  const application=applicationEnvironment()
  if(application){scope=modeNames[application]+' 环境';background='在当前独立环境中运行';selected=undefined;editable=false}
  label.title = background
  if (!selected || !editable) {
    const text = modes[selected] ?? fixedNames[scope] ?? scope
    if (label.textContent !== text || !label.querySelector('.alcor-scope-fixed')) {
      const badge = document.createElement('span')
      badge.className = 'alcor-scope-fixed'
      badge.textContent = text
      label.replaceChildren(badge)
    }
    return
  }
  let group = label.querySelector('[role="group"]')
  if (group?.dataset.package !== name) {
    group = document.createElement('div')
    group.setAttribute('role', 'group')
    group.setAttribute('aria-label', assignments[name]?.exclusive ? 'QQ 接管方' : name + ' 使用范围')
    group.className = 'alcor-scope-options'
    group.dataset.package = name
    for (const [value, title] of Object.entries(modes)) {
      if (value === 'both' && assignments[name]?.exclusive) continue
      const button = document.createElement('button')
      button.type = 'button'
      button.textContent = title
      button.dataset.value = value
      button.onclick = async event => {
        event.stopPropagation()
        if (group.dataset.saved === value || group.getAttribute('aria-busy') === 'true') return
        group.setAttribute('aria-busy', 'true')
        label.querySelector('[role="alert"]')?.remove()
        try {
          await store.set(name, value, group.dataset.saved)
        } catch (error) {
          const alert = document.createElement('span')
          alert.setAttribute('role', 'alert')
          alert.textContent = error.message
          label.append(alert)
        } finally {
          group.removeAttribute('aria-busy')
        }
      }
      group.append(button)
    }
    label.replaceChildren(group)
  }
  group.dataset.saved = selected
  for (const button of group.children) {
    button.setAttribute('aria-pressed', String(button.dataset.value === selected))
  }
}

function ScopeDetail({subject, store}) {
  const scopes = useSyncExternalStore(store.subscribe, store.getSnapshot)
  const container = useRef(null)
  const {pkg} = subject
  const name = pkg?.name ?? subject.id
  useLayoutEffect(() => {
    const [scope, background] = pluginScope(name, {item:subject.kind === 'item', version:pkg?.version})
    renderScope(container.current, name, scope, background, scopes[name], subject.kind === 'bundle', store)
  }, [name, pkg?.version, scopes, store, subject.kind])
  return <div className="alcor-plugin-facts" ref={container}/>
}

const style = `
.alcor-plugin-scope,.alcor-plugin-facts { display:flex;flex-wrap:wrap;align-items:center;gap:6px;font-size:12px;color:var(--dsw-alias-label-secondary); }
.alcor-plugin-facts { flex-basis:100%; }
.alcor-scope-options { position:relative;z-index:1;display:inline-flex;max-width:100%;padding:2px;gap:2px;border:1px solid var(--dsw-alias-border-l3);border-radius:9px;background:var(--dsw-alias-bg-l2); }
.alcor-scope-options button { appearance:none;border:0;border-radius:6px;background:transparent;color:var(--dsw-alias-label-secondary);font:inherit;font-weight:500;line-height:20px;padding:2px 9px;white-space:nowrap;cursor:pointer; }
.alcor-scope-options button:hover { background:var(--dsw-alias-bg-l1);color:var(--dsw-alias-label-primary); }
.alcor-scope-options button[aria-pressed=true] { background:var(--dsw-alias-label-primary);color:var(--dsw-alias-label-primary-inverted); }
.alcor-scope-options button:focus-visible { outline:2px solid var(--dsw-alias-label-primary);outline-offset:2px; }
.alcor-scope-options[aria-busy=true] button { cursor:progress; }
.alcor-scope-fixed { color:var(--dsw-alias-label-secondary);font-weight:500; }
`

// The native desktop exposes a slot for detail pages and stable DOM identities for cards.
function labelCards(versions, store) {
  const scopes = store.getSnapshot()
  for (const card of document.querySelectorAll('[data-plugin-panel] :is(' + cards + ')')) {
    const {pluginPackage, pluginItem, marketEntry, marketDetail} = card.dataset
    const name = pluginPackage ?? pluginItem ?? marketEntry ?? marketDetail
    const [scope, background] = pluginScope(name, {item:pluginItem !== undefined, version:versions.get(name)})
    const title = marketDetail ? card.querySelector('h3') : card.querySelector('button[aria-describedby], button[aria-label]')
    const target = title?.parentElement?.parentElement
    if (!target) continue
    let label = target.querySelector(':scope > .' + labelClass)
    if (!label) {
      label = document.createElement('div')
      label.className = labelClass
      target.append(label)
    }
    renderScope(label, name, scope, background, scopes[name], !!pluginPackage, store)
  }
}

export async function apply(ctx) {
  await ctx.remote.$mount(remote)
  ctx.inject(['remote.alcorPluginModes'],inner=>install(inner))
}
function install(ctx){
  let scopes={}, readGeneration=0
  const listeners=new Set()
  const store={
    subscribe(fn){listeners.add(fn);return ()=>listeners.delete(fn)},
    getSnapshot:()=>scopes,
    async set(name,value,previous){
      const result=await ctx.remote.alcorPluginModes.setScope({name,value,previous})
      await read()
      if(!result.ok)throw Error(result.error.message)
      globalThis.dispatchEvent(new Event('alcor-channel-catalog-changed'))
    },
  }
  async function read(){
    const stamp=++readGeneration
    const r=await ctx.remote.alcorPluginModes.readScopes({}).catch(()=>undefined)
    if(stamp!==readGeneration)return
    if(!r?.ok)return
    scopes=r.value
    for(const fn of listeners)fn()
  }

  ctx.slots.inject('plugins.detail.badge', () => ctx.slots.register({
    name:'plugins.detail.badge', id:'alcor-plugin-scope', order:-20, inject:()=>({store}),
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
        await read()
        const result=await ctx.remote.pluginManager.listBundles()
        if (revision!==generation || !result.ok) return
        versions=new Map(result.value.map(bundle=>[bundle.name,bundle.version]))
        labelCards(versions,store)
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
          node.nodeType===1 && (node.matches(cards) || node.querySelector(cards))))) labelCards(versions,store)
      })
      observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['data-plugin-package','data-plugin-item','data-plugin-status','data-market-entry','data-market-detail']})
      labelCards(versions,store)
      void refresh()
    }
    const unsubscribe=ctx.layout.panelInfo.subscribe(navigate)
    const onScopes=store.subscribe(()=>{if(observer)labelCards(versions,store)})
    const settings=ctx.remote.$on('settings/document-updated',()=>{if(observer)void read()})
    const changed=ctx.remote.$on('plugin-manager/changed',refresh)
    const reset=ctx.on('connection/reset',refresh)
    navigate()
    return ()=>{unsubscribe();onScopes();settings();changed();reset();stop();sheet.remove()}
  })
}
