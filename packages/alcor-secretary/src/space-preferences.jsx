import React, { useState, useSyncExternalStore } from 'react'
import { IconFolderOpenRegular, SegmentedControl } from '@deepseek-ai/dsh-client-ui-primitives'
import { projectViews, qqRoute, spaceOf } from './spaces.js'
import { Notice } from './notice.jsx'
import { applicationEnvironment } from '../../alcor-plugin-info/src/assignments.js'

export function SpacePreferences({ navigation, allWorkspaces, allSessions }) {
  const { metadata, error: loadError } = useSyncExternalStore(navigation.state.subscribe, navigation.state.getSnapshot)
  const list = useSyncExternalStore(listener => allWorkspaces.subscribe(listener), () => allWorkspaces.getSnapshot())
  const visible=useSyncExternalStore(navigation.workspaces.subscribe,navigation.workspaces.getSnapshot).items
  const catalog = useSyncExternalStore(listener => allSessions.subscribe(listener), () => allSessions.getSnapshot())
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [filter,setFilter]=useState(''),[expanded,setExpanded]=useState(false)
  async function assign(request) {
    setBusy(true); setError('')
    try { await navigation.claim(request) } catch (reason) { setError(reason.message) } finally { setBusy(false) }
  }
  if (!metadata) return loadError && <Notice tone="error">{loadError}</Notice>
  const unknown = catalog.ids.filter(id => !qqRoute(id) && !catalog.byId[id]?.origin && spaceOf(id,catalog,metadata)==='unassigned')
  const unsorted=list.items.filter(item=>!Object.values(metadata.defaults).includes(item.workspaceId) && !item.sessionIds.some(qqRoute) &&
    !projectViews(metadata,item.workspaceId,item.title) && !item.sessionIds.some(id=>spaceOf(id,catalog,metadata)!=='unassigned'))
  const matches=visible.filter(item=>(item.title+' '+item.path).toLowerCase().includes(filter.trim().toLowerCase()))
  const shown=expanded || filter ? matches : matches.slice(0,6)
  const scopeOptions=[{value:'alcor',label:'Alcor'},{value:'dsh',label:'DSH'}].filter(item=>!applicationEnvironment() || item.value===applicationEnvironment())
  return <section className="alcor-space-settings"><h2>当前空间的项目</h2>{error && <Notice tone="error" role="alert">{error}</Notice>}
    {visible.length>6 && <input className="alcor-project-filter" type="search" aria-label="查找当前空间项目" placeholder="查找项目" value={filter} onChange={e=>setFilter(e.target.value)} />}
    {visible.length ? shown.map(item=><div className="alcor-space-row" key={item.workspaceId}><IconFolderOpenRegular size={20}/><span><strong>{item.title}</strong><small>{item.path}</small></span>
      {applicationEnvironment() || Object.keys(metadata.projectViews?.[item.workspaceId] ?? {}).length>1 ? <small>{item.sessionIds.length} 段对话</small> :
        <SegmentedControl id={'space-'+item.workspaceId} label={'归属 · '+item.title} disabled={busy} value={metadata.projects[item.workspaceId] ?? ''}
          options={scopeOptions} onChange={space=>assign({space,workspaceId:item.workspaceId})}/>}</div>) : <p className="alcor-muted">当前空间暂无项目。</p>}
    {visible.length>6 && !filter && <button className="alcor-show-projects" type="button" aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}>{expanded?'收起项目':'查看全部 '+visible.length+' 个项目'}</button>}
    {filter && !matches.length && <p className="alcor-muted">未找到匹配的项目。</p>}
    {unsorted.length>0 && <details><summary>待整理的项目 · {unsorted.length}</summary>{unsorted.map(item=><div className="alcor-space-row" key={item.workspaceId}><span>{item.title}</span>
      <SegmentedControl id={'space-'+item.workspaceId} label={item.title || '项目归属'} value="" disabled={busy}
        options={scopeOptions} onChange={space=>assign({space,workspaceId:item.workspaceId})}/></div>)}</details>}
    {!!unknown.length && <details><summary>待整理的旧对话 · {unknown.length}</summary>{unknown.slice(0,100).map(id => <div className="alcor-space-row" key={id}><span>{catalog.byId[id].displayTitle || '未命名对话'}</span>
      <SegmentedControl id={'session-space-' + id} label="对话归属" disabled={busy} value="" options={scopeOptions} onChange={space => assign({ space, sessionId:id })} /></div>)}</details>}
  </section>
}
