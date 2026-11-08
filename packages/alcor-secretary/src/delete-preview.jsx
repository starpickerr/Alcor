import React, { useEffect, useState } from 'react'
import { Notice } from './notice.jsx'

export function useDeletePreview(api, domain, kind, id, scope, occurrenceOn, excludeOccurrence) {
  const [state, setState] = useState({})
  const key = JSON.stringify([domain, kind, id, scope, occurrenceOn, excludeOccurrence])
  useEffect(() => {
    let active = true, ticket
    const cancel = () => { if (ticket && typeof api?.cancelPageDelete === 'function') void api.cancelPageDelete({ ticket }).catch(() => {}) }
    setState({})
    if (!id) return () => { active = false }
    if (!api || typeof api.preparePageDelete !== 'function') { setState({ key: key, error: '无法预览删除范围。' }); return () => { active = false } }
    api.preparePageDelete({ domain, kind, id, ...(scope ? { scope } : {}), ...(occurrenceOn ? { occurrenceOn } : {}), ...(excludeOccurrence ? { excludeOccurrence: true } : {}) }).then(result => {
      if (!result.ok) throw new Error(result.error?.message || '无法预览删除范围。')
      ticket = result.value.ticket
      if (active) setState({ key: key, plan: result.value })
      else cancel()
    }).catch(error => { if (active) setState({ key: key, error: error.message }) })
    return () => { active = false; cancel() }
  }, [api, domain, kind, id, scope, occurrenceOn, excludeOccurrence, key])
  return id && state.key === key ? state : {}
}

export function DeletePreview({ state }) {
  if (state.error) return <Notice tone="error" role="alert">{state.error}</Notice>
  if (!state.plan) return <Notice role="status">正在确认删除范围…</Notice>
  return <>{!state.plan.apple && !state.plan.importUnlink && <Notice>仅删除 Alcor 本地内容，Apple 不变。</Notice>}{state.plan.importUnlink && <Notice tone="warning">停止导入并删除 Alcor 本地分类，Apple 不变。来源 {state.plan.importUnlink.source_id}；分类 {state.plan.importUnlink.apple_name}；容器 {state.plan.importUnlink.apple_id}。以后可重新选择此来源恢复导入。</Notice>}{state.plan.occurrence && <Notice tone="warning">起始日期 {state.plan.occurrence.occurrence_on}；范围 {state.plan.occurrence.scope}。{state.plan.occurrence.scope === 'future' ? '影响该系列从所选日期起及之后的日程。' : '仅跳过所选实例。'}</Notice>}{state.plan.apple && <Notice tone="warning">将请求 Apple 删除，Apple 确认后才处理本地记录；结果未知时保留内容，不会重复提交。Apple 来源 {state.plan.apple.source_id}；容器 {state.plan.apple.group_id}；系列 {state.plan.apple.series_apple_id}；实例 {state.plan.apple.instance_apple_id}；范围 {state.plan.apple.scope}{state.plan.apple.occurrence_on ? `；起始日期 ${state.plan.apple.occurrence_on}` : ''}。{state.plan.apple.scope === 'future' ? '影响此系列从所选实例起及之后的事件，可能涉及已修改例外；脱离系列的例外依 EventKit 处理。' : ''}{state.plan.apple.scope === 'category' ? '将删除容器及其内容，可能包含无法穷举的重复事件或例外；EventKit 无法保证跨系统操作原子完成。' : ''}</Notice>}<p>本次将删除以下 {state.plan.targets.length} 项{state.plan.importUnlink ? '本地内容' : ''}：</p><ul>{state.plan.targets.map((item, index) => <li key={`${item.id}-${index}`}>{item.category ? '分类：' : ''}{item.title}（ID {item.id}）{item.recurring ? '，重复系列' : ''}{item.apple ? `，Apple 来源 ${item.apple.source_id ?? ''}，容器 ${item.apple.group_id ?? ''}，对象 ${item.apple.apple_id ?? ''}` : ''}</li>)}</ul><Notice>继续后将在系统窗口核对并确认。</Notice></>
}
