import {permits,selectedScope} from '../../alcor-plugin-info/src/assignments.js'
import { briefingSourceGroup, briefingGroupEnabled } from './source-groups.js'
import { randomUUID } from 'node:crypto'
import { z } from 'zod'

const pluginPrefix = /^([A-Za-z0-9_-]{1,64})_briefing_sources$/
const resourceId = z.string().regex(/^[A-Za-z0-9._:-]{1,80}$/)
const pluginSources = z.array(z.object({ id: resourceId, account_id: z.string().min(1).max(200), label: z.string().min(1).max(200) })).max(200)
const pluginPage = z.object({
  source_id: resourceId, account_id: z.string().min(1).max(200),
  items: z.array(z.object({ source_id: z.string().min(1).max(500), thread_id:z.string().optional(), published_at:z.iso.datetime({offset:true}).optional(), source_updated_at:z.iso.datetime({offset:true}).optional(), title: z.string(), occurred_at: z.iso.datetime({ offset: true }), snippet: z.string().optional(), sender: z.string().optional(), url: z.string().optional(), payload: z.record(z.string(), z.json()).optional() })).max(100),
  next_offset: z.number().int().nonnegative().nullable(), truncated:z.boolean().optional(), errors: z.array(z.string()).default([]),
})

function allowedSource(ctx,tool) {
  const name=(tool?.startsWith('mcp__email__') || tool?.startsWith('mcp__mail__'))?'@alcor/mail':tool?.startsWith('mcp__elearning__')?'@alcor/elearning':null
  return !name || permits(selectedScope(ctx,name),'alcor')
}

async function execute(ctx, name, args, signal) {
  if(!allowedSource(ctx,name))throw Error('来源已从 Alcor 移出。')
  const batch = await ctx.tools.execute({ signal, calls: [{ type: 'tool-call', id: randomUUID(), name, arguments: JSON.stringify(args) }] })
  if(!allowedSource(ctx,name))throw Error('来源分配已变化。')
  const result = batch.results[0]?.result
  if (!result || result.isError) throw new Error(result?.error?.message || '来源插件读取失败，请检查连接后重试。')
  return result.value?.structuredContent?.value ?? result.value
}

export function localBriefingSources() {
  return [{ source: 'calendar_agenda', accountId: 'local', label: 'Alcor 日程' }, { source: 'alcor_tasks', accountId: 'local', label: 'Alcor 事务' }, { source: 'alcor_bills', accountId: 'local', label: 'Alcor 账单' }]
}

export async function discoverBriefingSources(ctx) {
  const sources = localBriefingSources()
  const errors = []
  const names = new Set(ctx.tools.definitions().map(tool => tool.name).filter(name=>allowedSource(ctx,name)))
  try {
    const school = permits(selectedScope(ctx,'@alcor/elearning'),'alcor') && await ctx.get('alcorElearning')?.briefingSource()
    if (school && names.has(school.tool)) sources.push(school)
  } catch { errors.push('学校来源暂时不可用，请检查连接。') }
  for (const name of names) {
    const native = pluginPrefix.exec(name)
    if (native && names.has(native[1] + '_briefing_fetch')) {
      try {
        const value = await execute(ctx, name, {}, AbortSignal.timeout(15000))
        const rows = pluginSources.parse(value?.sources)
        for (const row of rows) sources.push({ source: `plugin:${native[1]}:${row.id}`, accountId: row.account_id, label: row.label, tool: native[1] + '_briefing_fetch', method: 'briefing_fetch', arguments: { source_id: row.id } })
      } catch { errors.push(`${native[1]} 的简报来源目录不可用，请检查插件。`) }
      continue
    }
    if (!/^mcp__[A-Za-z0-9_-]+__list_sources$/.test(name)) continue
    const fetch = name.replace(/list_sources$/, 'fetch_messages')
    if (!names.has(fetch)) continue
    try {
      const rows = await execute(ctx, name, {}, AbortSignal.timeout(15000))
      if (!Array.isArray(rows)) throw new Error('来源目录不可用')
      for (const row of rows) if (row.source === 'email' && typeof row.account_id === 'string' && typeof row.mailbox_id === 'string') {
        sources.push({ source: 'email', accountId: row.account_id, label: String(row.name || '邮箱'), tool: fetch, method: 'fetch_messages', arguments: { account_id: row.mailbox_id } })
      }
    } catch { errors.push('邮件来源目录暂时不可用，请检查连接后刷新。') }
  }
  const connections = []
  for (const [service, type, name, label] of [
    ['alcorElearning', 'elearning', '@alcor/elearning', 'eLearning'],
    ['alcorMailConnection', 'email', '@alcor/mail', '邮件'],
  ]) {
    if(!permits(selectedScope(ctx,name),'alcor'))continue
    const connection = ctx.get(service)
    if (!connection) continue
    let status = 'unavailable'
    try {
      const state = await connection.state()
      status = !state.configured ? 'unconfigured' : !state.connected ? 'disconnected' : type === 'email' && state.accounts?.length && !state.accounts.some(account => account.enabled) ? 'disabled' : sources.some(source => source.source === type) ? 'ready' : 'unavailable'
    } catch { /* Keep installed connections visible even when their status cannot be read. */ }
    const oldName = name.replace('@alcor/', '@stella/')
    const started = ctx.get('profileContext')?.startedBundles || []
    connections.push({ package: started.includes(oldName) && !started.includes(name) ? oldName : name, label, status })
  }
  return { sources: [...new Map(sources.map(source => [source.source + ':' + source.accountId, source])).values()], connections, errors }
}

export async function briefingSourceOptions(ctx, source) {
  if (source.source !== 'elearning') throw new Error('此来源的范围由连接插件管理。')
  if(!permits(selectedScope(ctx,'@alcor/elearning'),'alcor'))throw Error('学校来源没有分配给 Alcor。')
  const current = await ctx.get('alcorElearning')?.briefingSource()
  if (!current || current.accountId !== source.accountId || current.tool !== source.tool) throw new Error('来源账号已变化，请刷新简报来源。')
  return z.array(z.object({ id: z.string(), name: z.string() })).parse(await execute(ctx, 'mcp__elearning__list_courses', {}, AbortSignal.timeout(185000)))
}

export async function readBriefingSourcePage(ctx, config, { since, until, offset, cursors, signal }) {
  const owner={email:'@alcor/mail',elearning:'@alcor/elearning'}[config.source]
  if(owner && !permits(selectedScope(ctx,owner),'alcor'))throw Error('来源没有分配给 Alcor。')
  if (config.method === 'briefing_fetch') {
    const prefix = config.tool.slice(0, -'_briefing_fetch'.length)
    if (!/^[A-Za-z0-9_-]{1,64}_briefing_fetch$/.test(config.tool) || config.source !== `plugin:${prefix}:${config.arguments.source_id}`) throw new Error('插件来源标识无效，请重新选择。')
    const value = pluginPage.parse(await execute(ctx, config.tool, { source_id: config.arguments.source_id, account_id: config.accountId, since, until, offset, limit: 100 }, signal))
    if (value.account_id !== config.accountId || value.source_id !== config.arguments.source_id) throw new Error('插件返回了不同账号或范围的数据，已拒绝使用。')
    return { ...value, source: config.source }
  }
  const schema = config.method === 'fetch' && config.source === 'elearning' ? ctx.tools.schemas?.().find(row => row.name === config.tool) : undefined
  if (schema && !schema.parameters.properties?.cursors) throw new Error('请更新 eLearning 插件后生成简报，当前版本不支持增量读取。')
  const args = { ...config.arguments, since, until, ...(config.method === 'fetch' ? { offset, limit: 50, cursors } : { cursors }) }
  const value = await execute(ctx, config.tool, args, signal)
  if (!value || !Array.isArray(value.items ?? value.source_items)) throw new Error('来源返回格式无效。')
  if ((value.source && value.source !== config.source) || (value.account_id && value.account_id !== config.accountId)) throw new Error('来源账号与简报配置不一致，请重新选择账号。')
  if ((value.items ?? value.source_items).some(item => (item.source && item.source !== config.source) || (item.account_id && item.account_id !== config.accountId))) throw new Error('来源账号已变化，请重新选择简报来源。')
  return value
}

// A briefing source needs both plugin/account availability and its independent source switch.
export function effectiveBriefingSources(preferences, available, groups = {}) {
  const key = source => JSON.stringify([source.source, source.accountId, source.tool])
  return available.filter(source => {
    const group = briefingSourceGroup(source)
    if (!briefingGroupEnabled(group, preferences, groups)) return false
    const scoped = preferences.filter(item => briefingSourceGroup(item) === group)
    return !group.startsWith('plugin:') || !scoped.length || scoped.some(item => key(item) === key(source))
  }).map(source => {
    const saved = preferences.find(item => key(item) === key(source))
    return source.source !== 'email' && saved ? { ...source, ...(saved.arguments ? { arguments: saved.arguments } : {}) } : source
  })
}
