import { Notice } from './notice.jsx'
import { useDeletePreview, DeletePreview } from './delete-preview.jsx'
import React, { useEffect, useRef, useState } from 'react'
import { Button, Input, IconSearchOutlineRegular, IconPlusOutlineRegular, IconChevronLeftOutlineRegular, IconTrashOutlineRegular, IconChecklistOutlineRegular, IconClockOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { Field } from './business-pages.jsx'

export const appleOperationMessage = operation => {
  if (operation?.state === 'sending') return '正在向 Apple 提交；请等待本机操作结果。'
  if (operation?.state === 'uncertain') return 'Apple 操作结果未知；内容和证据已保留，请核对，不要重复提交。'
  if (!['delete', 'truncate', 'exclude'].includes(operation?.action)) return '等待同步；现有内容已保留。'
  let document = operation?.document
  try { if (typeof document === 'string') document = JSON.parse(document) } catch { document = null }
  const intent = document?._confirmed_intent
  if (intent?.version === 1 && /^[a-f0-9]{64}$/.test(intent.plan_fingerprint ?? '')) return operation.deletion_phase === 'not_sent'
    ? '已确认，尚未发送；将按原范围继续，可取消此次操作。' : '已确认，等待同步；可取消此次操作或暂停同步。'
  return '等待本次确认；现有内容已保留，可取消此请求。'
}

const colors = [['蓝色','#597ac5'],['绿色','#48a28e'],['金色','#b58b54'],['紫色','#9b72b0'],['红色','#c45d67'],['灰色','#6b7280']]

export function Categories({ commitDelete, categories, mutate, busy, task = false, counts = {}, api, failure }) {
  const noun = task ? '清单' : '分类'
  const content = task ? '事务' : '日程'
  const [selection, select] = useState(null)
  const [drafts, setDrafts] = useState({})
  const [search, setSearch] = useState('')
  const [deleting, setDeleting] = useState(null)
  const deletePreview = useDeletePreview(api, task ? 'tasks' : 'calendar', 'category', deleting?.id)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [sources, setSources] = useState([])
  const [sourceError, setSourceError] = useState('')
  const [sourceLoading, setSourceLoading] = useState(true)
  const [retry, setRetry] = useState(0)
  const cancelDelete = useRef(null)
  const current = selection === 'new' ? null : categories.find(item => item.id === selection) ?? categories[0]
  const key = current?.id ?? 'new'
  const original = {name:current?.display_name ?? current?.name ?? '',color:current?.color ?? '#597ac5',source_id:''}
  const value = drafts[key] ?? original
  const pending = !!current?.apple_pending?.length
  const locked = pending || current?.apple?.can_rename === false
  const dirty = Object.keys(original).some(field => value[field] !== original[field])
  const errorText = failure?.includes('category name already exists') ? `已有同名${noun}，请使用其他名称。` : failure || error
  const update = patch => {setDrafts(previous => ({...previous,[key]:{...value,...patch}}));setNotice('');setError('')}
  const clearDraft = id => setDrafts(previous => {const next={...previous};delete next[id];return next})
  const open = id => {select(id);setDeleting(null);setError('');setNotice('')}
  useEffect(() => {if (deleting) cancelDelete.current?.focus()},[deleting])
  useEffect(() => {
    let disposed = false
    setSourceLoading(true);setSourceError('')
    if (!api) {setSourceLoading(false);return}
    api.business({domain:'apple',method:'status',arguments:{kind:task ? 'reminders' : 'calendar'}}).then(result => {
      if (!result.ok) throw new Error(result.error?.message || 'Apple 账户读取失败。')
      if (!disposed) setSources(result.value.sources ?? [])
    }).catch(error => {if (!disposed) setSourceError(error.message)}).finally(() => {if (!disposed) setSourceLoading(false)})
    return () => {disposed = true}
  },[api,task,retry])
  async function perform(method,args,message) {
    setError('');setNotice('')
    try {const result=await mutate(method,args);if (!result) throw new Error('操作未完成，现有内容已保留，请重试。');setNotice(message);return result}
    catch (error) {setError(error.message);return null}
  }
  const sourceName = item => item.apple?.source_name || sources.find(source => source.id === item.apple?.source_id)?.title || 'Alcor'
  const matches = categories.filter(item => `${item.display_name ?? item.name} ${item.apple?.source_name ?? ''}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
  const target = categories.find(item => item.id === deleting?.id) ?? deleting
  return <div className="alcor-category-manager">
    <aside className="alcor-category-browser">
      <Input icon={<IconSearchOutlineRegular size={16} />} aria-label={`搜索${noun}`} placeholder={`搜索${noun}`} value={search} onChange={event => setSearch(event.target.value)} />
      <div className="alcor-category-manager-toolbar"><span>{categories.length} 个{noun}</span><Button variant="ghost" size="sm" disabled={busy} icon={<IconPlusOutlineRegular size={15} />} onClick={() => open('new')}>新建{noun}</Button></div>
      <ul className="alcor-category-list" aria-label={`${noun}列表`}>{matches.map(item => <li key={item.id}><button type="button" aria-label={item.display_name ?? item.name} aria-pressed={current?.id === item.id && selection !== 'new'} disabled={busy} onClick={() => open(item.id)}><span className="alcor-category-swatch" style={{background:item.color}} /><span className="alcor-category-name"><strong>{item.display_name ?? item.name}</strong><small>{item.apple_pending?.length ? appleOperationMessage(item.apple_pending[0]).split('；')[0] : sourceName(item)}{drafts[item.id] && ' · 未保存'}</small></span><span className="alcor-category-count">{counts[item.id] ?? 0}</span></button></li>)}</ul>
      {!matches.length && <p className="alcor-category-empty">{categories.length ? `没有匹配的${noun}` : `还没有${noun}`}</p>}
    </aside>
    <div className="alcor-category-detail">
      {target ? <section className="alcor-category-delete"><Button variant="ghost" size="sm" disabled={busy} icon={<IconChevronLeftOutlineRegular size={15} />} onClick={() => setDeleting(null)}>返回{noun}详情</Button><span className="alcor-category-preview" style={{'--category-color':target.color}}><IconTrashOutlineRegular size={24} /></span><h3>删除“{target.display_name ?? target.name}”？</h3>{!deletePreview.plan?.importUnlink && <Notice tone="warning">将同时删除该{noun}中的全部{content}{task ? '及其子事务和提醒' : '（含重复系列）'}，无法撤销。</Notice>}<DeletePreview state={deletePreview} />{deletePreview.plan?.apple && <Notice className="alcor-category-callout"><p>Alcor 将请求 Apple 删除对应{task ? '清单' : '日历'}，并在本机操作完成后处理本地记录。结果未知时保留内容，不会重复提交。</p></Notice>}{(failure || error) && <Notice tone="error" role="alert" className="alcor-error">{errorText}</Notice>}<div className="alcor-category-form-actions"><Button ref={cancelDelete} variant="outline" disabled={busy} onClick={() => setDeleting(null)}>取消</Button><Button variant="primary" className="alcor-category-danger" disabled={busy || !deletePreview.plan || (target.apple?.can_delete === false && !deletePreview.plan?.importUnlink) || !!target.apple_pending?.length} onClick={async () => {if (await commitDelete(deletePreview.plan.ticket)) {clearDraft(target.id);setDeleting(null)}}}>继续确认</Button></div></section> : <form className="alcor-category-form" aria-label={`${current ? '编辑' : '新建'}${noun}`} onSubmit={async event => {event.preventDefault();if (locked || !value.name.trim()) return;const result=await perform(current ? 'update_category' : 'create_category',current ? {id:current.id,patch:{name:value.name.trim(),color:value.color}} : {name:value.name.trim(),color:value.color,...(value.source_id ? {source_id:value.source_id} : {})},`${noun}已保存`);if (result) {clearDraft(key);if (result.id) select(result.id)}}}>
        <header className="alcor-category-detail-heading"><span className="alcor-category-preview" style={{'--category-color':value.color}}><IconChecklistOutlineRegular size={24} /></span><div><h3>{current ? current.display_name ?? current.name : `新建${noun}`}</h3><span>{current ? `${counts[current.id] ?? 0} 项${content}` : noun}{current?.apple?.default && ' · Apple 默认'}{dirty && ' · 未保存'}</span></div></header>
        <fieldset className="alcor-category-fields" disabled={busy || locked}>
          <Field label={`${noun}名称`}><Input required maxLength={200} placeholder={`输入${noun}名称`} value={value.name} onChange={event => update({name:event.target.value})} /></Field>
          <div className="alcor-category-color-field"><span>{noun}颜色</span><div className="alcor-category-palette" role="group" aria-label={`${noun}配色`}>{colors.map(([name,color]) => <button key={color} type="button" aria-label={name} aria-pressed={value.color.toLowerCase() === color} style={{'--category-color':color}} onClick={() => update({color})}><span /></button>)}<label className="alcor-category-custom-color" title="自定义颜色"><input type="color" aria-label={`${noun}颜色`} value={value.color} onChange={event => update({color:event.target.value})} /><span>自定义</span></label></div></div>
          {!current && <Field label="Apple 账户"><select disabled={busy || sourceLoading || !sources.length} value={value.source_id} onChange={event => update({source_id:event.target.value})}><option value="">{sourceLoading ? '正在读取账户…' : 'Apple 默认账户'}</option>{sources.filter(item => item.can_create !== false).map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></Field>}
        </fieldset>
        {current ? <dl className="alcor-category-facts"><div><dt>来源</dt><dd>{sourceName(current)}</dd></div><div><dt>Apple 关联</dt><dd>{pending ? appleOperationMessage(current.apple_pending[0]).split('；')[0] : current.apple?.planned ? '待关联' : current.apple ? '已关联' : '尚未关联'}</dd></div><div><dt>{content}内容</dt><dd>{pending ? '待处理，暂不可更改' : current.apple?.can_modify === false ? '只读，在 Apple 中编辑' : '可添加和编辑'}</dd></div>{current.apple?.can_rename === false && <div><dt>名称与颜色</dt><dd>Apple 不允许修改</dd></div>}{current.apple?.can_delete === false && <div><dt>删除权限</dt><dd>Apple 不允许删除</dd></div>}</dl> : sourceError && <details className="alcor-category-source-error"><summary>Apple 账户暂不可用</summary><Notice tone="warning">{sourceError}</Notice><Button size="sm" variant="ghost" type="button" onClick={() => setRetry(value => value+1)}>重新读取账户</Button></details>}
        {pending && <Notice className="alcor-category-callout">{current.apple_pending.map(operation => <div key={operation.id}><p>{appleOperationMessage(operation)}</p>{operation.error && <p>{operation.error}</p>}{operation.state === 'pending' && <Button variant="outline" size="sm" disabled={busy} onClick={() => void perform('cancel_apple_operation',{operation_id:operation.id},'已取消待处理操作，内容已保留。')}>取消此请求</Button>}</div>)}</Notice>}
        {(failure || error) && <Notice tone="error" role="alert" className="alcor-error">{errorText}</Notice>}{notice && <Notice tone="success" role="status" className="alcor-category-saved">{notice}</Notice>}
        <div className="alcor-category-form-actions">{current && <Button variant="ghost" className="alcor-category-delete-button" aria-label={`删除${noun}：${current.display_name ?? current.name}`} disabled={busy || pending} icon={<IconTrashOutlineRegular size={15} />} onClick={() => {setError('');setNotice('');setDeleting(current)}}>删除{noun}</Button>}<span />{dirty && <Button variant="outline" disabled={busy} onClick={() => {clearDraft(key);setError('');setNotice('')}}>还原</Button>}<Button type="submit" variant="primary" disabled={busy || locked || !value.name.trim() || !!current && !dirty}>保存{noun}</Button></div>
      </form>}
    </div>
  </div>
}
