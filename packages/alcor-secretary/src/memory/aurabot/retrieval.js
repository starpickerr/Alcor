export const validFromOf = (m) => m.validFrom ?? saidTime(m)
export const validUntilOf = (m) => m.validUntil ?? m.invalidAt
export const isCurrent = (m, now = Date.now()) => !m.invalidAt && (!validUntilOf(m) || validUntilOf(m) > now) && (!validFromOf(m) || validFromOf(m) <= now)

export function parseValidityDate(value, reference = Date.now()) {
  if (typeof value === 'number') return Number.isFinite(value) && value > 0 ? value : undefined
  const text = String(value || '').trim()
  const ref = new Date(reference)
  const relative = text.match(/^(去年|今年|上个月|本月|这个月)(?:(\d{1,2})月)?(?:(\d{1,2})日?)?$/)
  if (relative) {
    const [, word, month, day] = relative
    if (/月/.test(word) && month) return undefined
    const year = ref.getFullYear() - (word === '去年' ? 1 : 0)
    const mon = month ? Number(month) - 1 : /月/.test(word) ? ref.getMonth() - (word === '上个月' ? 1 : 0) : 0
    const date = new Date(year, mon, Number(day) || 1)
    if ((month && (Number(month) < 1 || Number(month) > 12)) || (day && date.getDate() !== Number(day))) return undefined
    return date.getTime()
  }
  const m = text.replace(/年|月/g, '-').replace(/日$/, '').replace(/-$/, '').match(/^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?$/)
  if (!m) return undefined
  const year = Number(m[1]), month = Number(m[2] || 1), day = Number(m[3] || 1)
  const date = new Date(year, month - 1, day)
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day ? date.getTime() : undefined
}

/** 时间范围沿用 whenText 的今年/去年判断，给召回和面板同一个显示结果。 */
export function pastWindow(query, now = Date.now(), triggers = []) {
  const text = String(query || '')
  const hit = text.match(/(?:\d{4}年|去年|前年|今年)(?:\d{1,2}月)?|上个月|上月|昨天|前天/)
  if (hit) {
    const word = hit[0]
    const n = new Date(now)
    let start, end
    if (/昨天|前天/.test(word)) {
      start = new Date(n.getFullYear(), n.getMonth(), n.getDate() - (word === '昨天' ? 1 : 2)).getTime()
      end = start + 86400_000
    } else {
      start = word === '前年' ? new Date(n.getFullYear() - 2, 0, 1).getTime() : parseValidityDate(word === '上月' ? '上个月' : word, now)
      if (!start) return null
      const d = new Date(start)
      end = /月/.test(word) ? new Date(d.getFullYear(), d.getMonth() + 1, 1).getTime() : new Date(d.getFullYear() + 1, 0, 1).getTime()
    }
    return { start, end: Math.min(end, now + 1) }
  }
  return [...triggers.filter((word) => /上次|上回|之前|以前|那天|那次|那时候|过去|曾经/.test(word)), '以前', '那时候', '过去', '之前'].some((word) => word && text.includes(word)) ? { start: -Infinity, end: now + 1 } : null
}

const STOP = new Set('的了是我你他她它们在有和就不也都很吗吧呢啊呀哦嗯这那个一上下来去说要会能着过'.split(''))
const NOISE = new Set('哈呵嘿嘻嗯哦啊呀吗呢吧'.split(''))

/** 切词：英文/数字整词；中文连续段拆成二元字组（单字段保留单字，去掉停用字）。 */
export function tokenize(text) {
  const out = []
  const s = String(text || '').toLowerCase()
  for (const m of s.matchAll(/[a-z0-9_]{2,}|[一-鿿]+/g)) {
    const w = m[0]
    if (/^[a-z0-9_]+$/.test(w)) { out.push(w); if (w.includes('_')) out.push(...w.split('_').filter(Boolean)); continue }
    if (w.length === 1) { if (!STOP.has(w)) out.push(w); if (w.includes('_')) out.push(...w.split('_').filter(Boolean)); continue }
    for (let i = 0; i < w.length - 1; i++) {
      const pair = w.slice(i, i + 2)
      if (!(NOISE.has(pair[0]) && NOISE.has(pair[1]))) out.push(pair)
    }
  }
  return out
}

/** 建索引：返回 { docs, df, avgLen }。 */
export function buildIndex(items) {
  const df = new Map()
  const docs = items.map((it) => {
    const toks = tokenize(`${it.text} ${it.kind === 'seen' ? `${it.meta?.title || ''} ${it.meta?.author || ''}` : ''} ${(it.tags || []).join(' ')} ${it.aboutName || ''}`)
    const tf = new Map()
    for (const t of toks) tf.set(t, (tf.get(t) || 0) + 1)
    for (const t of tf.keys()) df.set(t, (df.get(t) || 0) + 1)
    return { it, tf, len: toks.length || 1 }
  })
  const avgLen = docs.reduce((s, d) => s + d.len, 0) / (docs.length || 1)
  return { docs, df, avgLen, N: docs.length }
}

/**
 * 检索。
 * @param idx buildIndex() 的结果
 * @param {string} query
 * @param {{about?:string|string[], now?:number, limit?:number, minScore?:number, includeInvalid?:boolean, period?:{start:number,end:number}|null, related?:boolean, vectorHits?:{id:string, similarity:number}[], ownerScope?:boolean, scopeChatId?:string}} [o]
 *   about：关于这些人（QQ 号）的记忆加分；群里可以传这一段里所有说话的人
 *   ownerScope：true/不传 = 主人视角，可以看到所有来源（面板、内部整理、主人私聊）；
 *     false = 社交视角（群聊 / 非主人私聊），普通记忆只保留 chatId === scopeChatId 的条目。
 *   scopeChatId：社交视角下的当前聊天 id（private:QQ / group:群号）。
 */
export function search(idx, query, o = {}) {
  const q = [...new Set(tokenize(query))]
  const now = o.now ?? Date.now()
  const aboutSet = new Set((Array.isArray(o.about) ? o.about : o.about ? [o.about] : []).map(String))
  const excludeSet = new Set((Array.isArray(o.excludeAbout) ? o.excludeAbout : o.excludeAbout ? [o.excludeAbout] : []).map(String))
  const onlyAbout = o.onlyAbout
    ? new Set((Array.isArray(o.onlyAbout) ? o.onlyAbout : [o.onlyAbout]).map(String))
    : null
  const vectors = new Map()
  if (Array.isArray(o.vectorHits)) for (const hit of o.vectorHits) {
    if (hit && Number.isFinite(hit.similarity) && hit.similarity >= 0.55 && (!vectors.has(hit.id) || vectors.get(hit.id) < hit.similarity)) vectors.set(hit.id, hit.similarity)
  }
  const k1 = 1.4
  const b = 0.7
  const scored = []
  const eligible = new Map()
  const period = o.period === undefined ? pastWindow(query, now) : o.period
  for (const d of idx.docs) {
    // 群聊 / 非主人私聊里只允许想起「和这一段说话的人有关」的记忆；
    // requireAbout 再收紧：社交场景连没有 about 的旧日记/事件也不放进来。
    // 梗概始终只属于原聊天，主人私聊也不能跨群查看。
    if (d.it.kind === 'gist' && (!o.gistChatId || d.it.chatId !== o.gistChatId)) continue
    const group = d.it.scope === 'group'
    if (group && (!String(d.it.chatId || '').startsWith('group:') || d.it.about)) continue
    if (group && !o.includeGroup && (!o.groupChatId || d.it.chatId !== o.groupChatId)) continue
    // F1 来源可见性（统一在检索层判定）：社交视角只看当前聊天里产生的记忆。
    // 旧数据/面板导入没有 chatId 的条目只留在主人视角；群公共记忆走上面的 scope==='group' 分支。
    if (!group && o.ownerScope === false && String(d.it.chatId || '') !== String(o.scopeChatId || '')) continue
    if (!group && d.it.about && excludeSet.has(String(d.it.about))) continue
    if (!group && onlyAbout && d.it.about && !onlyAbout.has(String(d.it.about))) continue
    if (!group && o.requireAbout && !d.it.about) continue
    let s = 0
    const why = []
    for (const t of q) {
      const f = d.tf.get(t)
      if (!f) continue
      const n = idx.df.get(t) || 0
      const idf = Math.log(1 + (idx.N - n + 0.5) / (n + 0.5))
      const part = idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * d.len / idx.avgLen))
      s += part
      why.push([t, part])
    }
    const it = d.it
    if (!o.includeInvalid) {
      if (period) {
        const from = validFromOf(it) || 0
        const until = validUntilOf(it) ?? Infinity
        if (from >= period.end || until <= period.start || until <= from) continue
      } else if (!isCurrent(it, now)) continue
    }
    eligible.set(it.id, it)
    const aboutHit = Boolean(it.about) && aboutSet.has(String(it.about))
    const vector = vectors.get(it.id)
    if (s <= 0 && vector === undefined) continue // 零关键词只能由足够相似的向量召回
    const importance = 0.6 + 0.8 * (Number(it.importance) || 0.5)
    const score = ((s > 0 ? s : vector) + (aboutHit ? 0.8 : 0)) * importance * (0.55 + 0.45 * retention(it, now))
    scored.push({ it, score, keyword: s, vector, why: why.sort((x, y) => y[1] - x[1]).slice(0, 3).map((w) => w[0]).concat(aboutHit ? ['（关于这个人）'] : [], vector !== undefined ? ['（语义相关）'] : []) })
  }
  // 关键词名次始终从关键词候选里算，向量候选不能改变原本的关键词排序。
  const hits = scored.filter((x) => x.keyword > 0 && x.score >= (o.minScore ?? 0.35))
  if (o.rrf !== false) {
    const ranks = (value) => {
      const sorted = [...hits].sort((a, b) => value(b) - value(a))
      let rank = 0
      return new Map(sorted.map((h, i) => { if (!i || value(h) !== value(sorted[i - 1])) rank = i + 1; return [h, rank] }))
    }
    const keywordRank = ranks((h) => h.keyword)
    const timeRank = ranks((h) => saidTime(h.it) || 0)
    const fused = (h) => 1 / (60 + keywordRank.get(h)) + 1 / (60 + timeRank.get(h))
    hits.sort((a, b) => Math.floor(b.score * 10) - Math.floor(a.score * 10) || fused(b) - fused(a) || b.score - a.score)
  } else hits.sort((a, b) => b.score - a.score)
  if (vectors.size) {
    // 先保留原来的关键词排序，再与相似度排序按名次融合，分数不能直接相加。
    const keywordRank = new Map(hits.map((h, i) => [h, i + 1]))
    const have = new Set(hits)
    for (const hit of scored) if (hit.vector !== undefined && !have.has(hit)) hits.push(hit)
    const vectorRank = new Map(hits.filter((h) => h.vector !== undefined).sort((a, b) => b.vector - a.vector).map((h, i) => [h, i + 1]))
    const fused = (h) => (keywordRank.has(h) ? 1 / (60 + keywordRank.get(h)) : 0) + (vectorRank.has(h) ? 1 / (60 + vectorRank.get(h)) : 0)
    hits.sort((a, b) => fused(b) - fused(a) || b.score - a.score)
  }
  const limit = Math.max(0, o.limit ?? 6)
  const selected = hits.slice(0, limit)
  if (o.related !== false) {
    const have = new Set(selected.map((h) => h.it.id))
    for (const hit of [...selected]) for (const id of hit.it.related || []) {
      if (selected.length >= limit) break
      const it = eligible.get(id)
      if (!it || have.has(id)) continue
      selected.push({ it, score: hit.score * 0.6, why: ['（相关的事）'] })
      have.add(id)
    }
  }
  return selected.map(({ keyword, vector, ...hit }) => hit)
}

export const saidTime = (m) => m.saidAt || m.at

export function retention(m, now = Date.now()) {
  const last = Math.max(m.at || 0, m.lastHit || 0)
  const days = Math.max(0, (now - last) / 86400_000)
  const strength = 1 + Math.min(10, Number(m.hitDays) || 0)
  const tau = 20 * strength * (0.5 + (Number(m.importance) || 0.5))
  return Math.exp(-days / tau)
}
