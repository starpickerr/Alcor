import { Notice } from './notice.jsx'
import { BriefingToggle } from './briefing-toggle.jsx'
import { briefingSourceGroup, briefingGroupEnabled } from '../../alcor-organizer/src/source-groups.js'
import React, { useEffect, useState, useSyncExternalStore } from 'react'
import { Button, Checkbox, IconRefreshOutlineRegular, IconChevronRightOutlineRegular, IconChevronLeftOutlineRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { sourceKey, sourceName, briefingScope, mergeSources, selectCourseScope, sourceOwner, briefingSourceCards, canonicalPackage } from './briefing-source-selection.js'

const call = async (api, method, args = {}) => {
  const result = await api.business({ domain: 'briefing', method, arguments: args })
  if (!result.ok) throw new Error(result.error?.message || '读取来源失败，请重试。')
  return result.value
}

export function BriefingSourceControls({ api, owner, disabled = false }) {
  const [catalog, setCatalog] = useState(null)
  const [saved, setSaved] = useState([])
  const [selected, setSelected] = useState([])
  const [scope, setScope] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  async function run(work) { setBusy(true); setError(''); setNotice(''); try { await work() } catch (error) { setError(error.message) } finally { setBusy(false) } }
  async function load() {
    const [value, sources] = await Promise.all([call(api, 'discover_sources'), call(api, 'source_preferences')])
    setCatalog(value); setSaved(sources); setSelected(sources); setScope(null)
  }
  useEffect(() => { void run(load) }, [api])
  const rows = mergeSources([...saved, ...selected], catalog?.sources || []).filter(source => sourceOwner(source) === canonicalPackage(owner))
  const available = new Set((catalog?.sources || []).map(sourceKey))
  async function editScope(source) {
    await run(async () => { const options = await call(api, 'source_options', { source }); setScope({ key: sourceKey(source), items: options, all: !source.arguments?.course_ids?.length, ids: source.arguments?.course_ids || [] }) })
  }
  async function save() {
    const scoped = rows.find(source => scope?.key === sourceKey(source))
    const base = scoped && !selected.some(source => sourceKey(source) === sourceKey(scoped)) ? [...selected, scoped] : selected
    const next = base.map(source => scope?.key === sourceKey(source) ? selectCourseScope(source, scope.all, scope.ids) : source)
    const value = await call(api, 'configure_sources', { sources: next, expectedSources: saved })
    setSaved(value); setSelected(value); setScope(null); setNotice(canonicalPackage(owner) === '@alcor/elearning' ? '课程范围已保存。' : '简报来源已保存。')
  }
  return <section className="alcor-briefing-source-settings" data-briefing-source-controls={owner}><fieldset disabled={disabled} className="alcor-source-control-fields">
    <Notice>所选内容将在生成简报时发送给模型。</Notice>
    <div className="alcor-actions"><Button variant="outline" aria-label="刷新来源" title="刷新来源" disabled={busy} onClick={() => run(load)}><IconRefreshOutlineRegular size={16} /></Button></div>
    {!catalog && !error && <Notice role="status">正在发现来源…</Notice>}
    {catalog?.errors.map(message => <Notice role="status" key={message}>{message}</Notice>)}
    {catalog && rows.map(source => {
      const key = sourceKey(source); const included = source.source === 'elearning' || selected.some(item => sourceKey(item) === key)
      return <div className="alcor-briefing-source-option" key={key}><div className="alcor-briefing-source-choice">{source.source === 'elearning' ? <strong>{sourceName(source)}</strong> : <Checkbox label={sourceName(source)} checked={included} disabled={busy || (!available.has(key) && !included)} onChange={enabled => { setSelected(current => enabled ? [...current, source] : current.filter(item => sourceKey(item) !== key)); if (!enabled && scope?.key === key) setScope(null) }} />}{available.has(key) ? <span className="alcor-muted">{briefingScope(source)}</span> : <Notice tone="warning">插件或账号不可用，可取消选择</Notice>}</div>
        {source.source === 'elearning' && <Button variant="ghost" size="sm" disabled={busy || !included || !available.has(key)} onClick={() => editScope(source)}>选择课程</Button>}
        {scope?.key === key && <fieldset className="alcor-briefing-course-scope"><legend>课程范围</legend><Checkbox label="全部可访问课程" checked={scope.all} disabled={busy} onChange={all => setScope({ ...scope, all })} />{scope.items.map(course => <Checkbox key={course.id} label={course.name} checked={scope.all || scope.ids.includes(course.id)} disabled={busy || scope.all} onChange={checked => setScope({ ...scope, ids: checked ? [...scope.ids, course.id] : scope.ids.filter(id => id !== course.id) })} />)}{!scope.items.length && <Notice>当前没有可选课程。</Notice>}</fieldset>}
      </div>
    })}
    {canonicalPackage(owner) === '@alcor/secretary' && <Notice>定时简报仅使用所选本机来源。</Notice>}
    <Button variant="primary" disabled={busy || !catalog} onClick={() => run(save)}>{canonicalPackage(owner) === '@alcor/elearning' ? '保存范围' : '保存来源'}</Button>
    {error && <Notice tone="error" role="alert">{error}</Notice>}{notice && <Notice tone="success" role="status">{notice}</Notice>}
  </fieldset></section>
}


export function PluginBriefingSources({ api, subject }) {
  if (subject.kind !== 'bundle' || canonicalPackage(subject.pkg.name) !== '@alcor/elearning') return null
  return <section className="alcor-plugin-briefing" data-plugin-briefing={subject.pkg.name}><h2>{canonicalPackage(subject.pkg.name) === '@alcor/elearning' ? '课程范围' : '简报来源'}</h2><BriefingSourceControls api={api} owner={subject.pkg.name} disabled={!subject.pkg.enabled} /></section>
}

export function BriefingPluginReturn({ subject, navigation, onReturn }) {
  const visit = useSyncExternalStore(navigation.subscribe, navigation.getSnapshot)
  if (!visit || visit.returning || !visit.view?.settings || !subject.pkg
    || visit.packageName && canonicalPackage(visit.packageName) !== canonicalPackage(subject.pkg.name)) return null
  return <Button variant="outline" size="sm" onClick={onReturn}><IconChevronLeftOutlineRegular size={16} />返回简报设置</Button>
}

export function BriefingSources({ api, openPlugins }) {
  const [value, setValue] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function load() {
    setBusy(true); setError('')
    try {
      const [catalog, saved, retained, groups] = await Promise.all([call(api, 'discover_sources'), call(api, 'list_sources'), call(api, 'source_preferences'), call(api, 'source_groups')])
      setValue({ catalog, saved, retained, groups })
    } catch (error) { setError(error.message) } finally { setBusy(false) }
  }
  async function toggle(card, enabled) {
    setBusy(true); setError('')
    try { await call(api, 'configure_source_groups', { groups: { ...value.groups, [card.key]: enabled }, expectedGroups: value.groups }); await load() }
    catch (error) { setError(error.message); setBusy(false) }
  }
  useEffect(() => { void load() }, [api])
  return <div className="alcor-source-overview">
    <div className="alcor-source-overview-toolbar"><Button variant="ghost" aria-label="刷新来源" title="刷新来源" disabled={busy} onClick={load}><IconRefreshOutlineRegular size={16} /></Button><Button variant="ghost" size="sm" onClick={() => openPlugins()}>管理插件</Button></div>
    {!value && busy && <Notice role="status">正在读取来源…</Notice>}
    {value && <div className="alcor-source-cards">{briefingSourceCards(value.saved, value.catalog, value.retained).map(card => {
      const state = ['unconfigured', 'disconnected', 'unavailable'].includes(card.connection) ? 'unavailable' : card.unavailable ? 'unavailable' : card.selected ? 'selected' : 'idle'
      const label = card.connection === 'disabled' ? '已停用' : card.connection === 'unconfigured' ? '待配置' : state === 'unavailable' ? '需检查' : card.selected ? '已开启' : '未开启'
      const checked = briefingGroupEnabled(card.key, value.retained, value.groups)
      const activeCount = value.catalog.sources.filter(source => briefingSourceGroup(source) === card.key).length
      const description = card.key === 'email' ? `${activeCount} 个邮箱已开启` : card.key === 'elearning' ? '课程、作业、公告与文件' : card.key === 'calendar_agenda' ? '未来 24 小时日程' : card.key === 'alcor_tasks' ? '未归档事务' : card.key === 'alcor_bills' ? '未归档账单' : `${card.count} 个来源`
      return <div className="alcor-source-card" key={card.key} data-source-card={card.key}><button type="button" className="alcor-source-card-open" aria-label={`管理来源：${card.title}`} onClick={() => openPlugins(card.package)}><div><strong>{card.title}</strong></div><p>{description}</p>{state === 'unavailable' && <small>{label}</small>}<IconChevronRightOutlineRegular size={16} /></button><BriefingToggle label={`启用来源：${card.title}`} checked={checked} disabled={busy} onChange={enabled => toggle(card, enabled)} /></div>
    })}</div>}
    {error && <Notice tone="error" role="alert">{error}</Notice>}
  </div>
}
