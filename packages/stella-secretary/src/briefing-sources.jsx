import React, { useEffect, useState } from 'react'
import { Button, Checkbox, IconRefreshOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { sourceKey, sourceName, briefingScope, mergeSources, selectCourseScope } from './briefing-source-selection.js'

const call = async (api, method, args = {}) => {
  const result = await api.business({ domain: 'briefing', method, arguments: args })
  if (!result.ok) throw new Error(result.error?.message || '读取来源失败，请重试。')
  return result.value
}

export function BriefingSources({ api, onSaved, openPlugins }) {
  const [catalog, setCatalog] = useState(null)
  const [saved, setSaved] = useState([])
  const [selected, setSelected] = useState([])
  const [scope, setScope] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  async function run(work) { setBusy(true); setError(''); setNotice(''); try { await work() } catch (error) { setError(error.message) } finally { setBusy(false) } }
  async function load() {
    const [value, sources] = await Promise.all([call(api, 'discover_sources'), call(api, 'list_sources')])
    setCatalog(value); setSaved(sources); setSelected(sources); setScope(null)
  }
  useEffect(() => { void run(load) }, [api])
  const rows = mergeSources([...saved, ...selected], catalog?.sources || [])
  const available = new Set((catalog?.sources || []).map(sourceKey))
  async function editScope(source) {
    await run(async () => { const options = await call(api, 'source_options', { source }); setScope({ key: sourceKey(source), items: options, all: !source.arguments?.course_ids?.length, ids: source.arguments?.course_ids || [] }) })
  }
  async function save() {
    const next = selected.map(source => scope?.key === sourceKey(source) ? selectCourseScope(source, scope.all, scope.ids) : source)
    const value = await call(api, 'configure_sources', { sources: next, expectedSources: saved })
    setSaved(value); setSelected(value); setScope(null); onSaved(value); setNotice('简报来源已保存。')
  }
  return <section className="stella-briefing-source-settings">
    <p className="stella-muted">所选内容将在生成简报时发送给模型。</p>
    <div className="stella-actions"><Button variant="outline" aria-label="刷新来源" title="刷新来源" disabled={busy} onClick={() => run(load)}><IconRefreshOutlineRegular size={16} /></Button><Button variant="ghost" onClick={() => openPlugins()}>管理 DSH 插件</Button></div>
    {!catalog && !error && <p role="status">正在发现来源…</p>}
    {catalog?.errors.map(message => <p role="status" key={message}>{message}</p>)}
    {catalog?.connections?.filter(connection => connection.status !== 'ready').map(connection => <div className="stella-briefing-source-option" data-source-connection={connection.package} key={connection.package}>
      <div className="stella-briefing-source-choice"><strong>{connection.label}</strong><span className="stella-muted">{({ unconfigured: '未配置账号', disconnected: '连接未就绪', unavailable: '暂无可用来源' })[connection.status]}</span></div>
      <Button variant="outline" size="sm" disabled={busy} onClick={() => openPlugins(connection.package)}>{connection.status === 'unconfigured' ? '设置账号' : '检查设置'}</Button>
    </div>)}
    {catalog && rows.map(source => {
      const key = sourceKey(source); const included = selected.some(item => sourceKey(item) === key)
      return <div className="stella-briefing-source-option" key={key}><div className="stella-briefing-source-choice"><Checkbox label={sourceName(source)} checked={included} disabled={busy || (!available.has(key) && !included)} onChange={enabled => { setSelected(current => enabled ? [...current, source] : current.filter(item => sourceKey(item) !== key)); if (!enabled && scope?.key === key) setScope(null) }} /><span className="stella-muted">{available.has(key) ? briefingScope(source) : '插件或账号不可用，可取消选择'}</span></div>
        {source.source === 'elearning' && <Button variant="ghost" size="sm" disabled={busy || !included || !available.has(key)} onClick={() => editScope(source)}>选择课程</Button>}
        {scope?.key === key && <fieldset className="stella-briefing-course-scope"><legend>课程范围</legend><Checkbox label="全部可访问课程" checked={scope.all} disabled={busy} onChange={all => setScope({ ...scope, all })} />{scope.items.map(course => <Checkbox key={course.id} label={course.name} checked={scope.all || scope.ids.includes(course.id)} disabled={busy || scope.all} onChange={checked => setScope({ ...scope, ids: checked ? [...scope.ids, course.id] : scope.ids.filter(id => id !== course.id) })} />)}{!scope.items.length && <p>当前没有可选课程。</p>}</fieldset>}
      </div>
    })}
    <p className="stella-muted">定时简报仅使用本机来源。</p>
    <Button variant="primary" disabled={busy || !catalog} onClick={() => run(save)}>保存来源</Button>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
  </section>
}
