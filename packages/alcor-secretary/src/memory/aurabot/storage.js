// Adapted from Aurabot 1.2.0, see AURABOT-SOURCE.json and AURABOT-LICENSE.txt.
import { DatabaseSync } from 'node:sqlite'
import { existsSync, mkdirSync, renameSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { isDeepStrictEqual } from 'node:util'
export const MEMORY_SCHEMA = 1
const tables = ['items', 'gists', 'kv', 'meta']

function rowsOf(state) {
  if (!state || typeof state !== 'object' || Array.isArray(state) || !Array.isArray(state.items)) throw new Error('记忆格式不正确')
  const rows = { items: new Map(), gists: new Map(), kv: new Map(), meta: new Map() }
  const collections = []
  for (const [key, value] of Object.entries(state)) {
    if (key === 'items' || key === 'gists') {
      if (!Array.isArray(value)) throw new Error(`${key} 不是数组`)
      value.forEach((item) => {
        if (!item || typeof item.id !== 'string' || !item.id || rows[key].has(item.id)) throw new Error(`${key} 有缺失或重复的 id，保留 JSON`)
        rows[key].set(item.id, { json: JSON.stringify(item) })
      })
      rows.meta.set(`${key}:order`, { json: JSON.stringify([...rows[key].keys()]) })
      collections.push(key)
    } else rows.kv.set(key, { json: JSON.stringify(value) })
  }
  // 元数据单独存，kv 只放其余顶层字段，也不占用用户可能已有的键。
  rows.meta.set('collections', { json: JSON.stringify(collections.sort()) })
  return rows
}

function readDatabase(db) {
  const meta = new Map(db.prepare('SELECT key, json FROM meta').all().map((r) => [r.key, JSON.parse(r.json)]))
  const collections = meta.get('collections') || []
  const state = Object.fromEntries(db.prepare('SELECT key, json FROM kv ORDER BY rowid').all().map((r) => [r.key, JSON.parse(r.json)]))
  for (const table of ['items', 'gists']) {
    const ranks = new Map((meta.get(`${table}:order`) || []).map((id, i) => [id, i]))
    const items = db.prepare(`SELECT id, json FROM ${table}`).all()
      .sort((a, b) => (ranks.get(a.id) ?? Infinity) - (ranks.get(b.id) ?? Infinity))
      .map((r) => JSON.parse(r.json))
    if (collections.includes(table) || items.length) state[table] = items
  }
  return state
}

function initialize(db) {
  db.exec('PRAGMA busy_timeout = 1000')
  const version = db.prepare('PRAGMA user_version').get().user_version
  if (version > MEMORY_SCHEMA) throw new Error(`记忆数据库版本 ${version} 比插件新，保留原库`)
  db.exec('PRAGMA journal_mode = WAL; PRAGMA synchronous = FULL')
  if (version === MEMORY_SCHEMA) return
  db.exec(`BEGIN IMMEDIATE;
    CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS gists (id TEXT PRIMARY KEY, json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, json TEXT NOT NULL);
    PRAGMA user_version = ${MEMORY_SCHEMA};
    COMMIT;`)
}


export function createMemoryStorage({ directory }) {
  const file = join(directory, 'memory.sqlite')
  function load() {
    if (!existsSync(file)) return { items: [] }
    const db = new DatabaseSync(file, {readOnly:true})
    try {
      if (db.prepare('PRAGMA user_version').get().user_version !== MEMORY_SCHEMA) throw Error('记忆库版本不兼容。')
      return readDatabase(db)
    } finally { db.close() }
  }
  function save(state) {
    mkdirSync(directory, { recursive: true, mode: 0o700 })
    const fresh = !existsSync(file)
    const target = fresh ? file + '.migrating' : file
    if (fresh) for (const suffix of ['', '-shm', '-wal']) rmSync(target + suffix, { force: true })
    const db = new DatabaseSync(target)
    try {
      initialize(db)
      const next = rowsOf(state)
      const existing = readDatabase(db)
      const previous = existing.items ? rowsOf(existing) : Object.fromEntries(tables.map(table => [table, new Map()]))
      db.exec('BEGIN IMMEDIATE')
      try {
        for (const table of tables) {
          const scalar = table === 'kv' || table === 'meta'
          const key = scalar ? 'key' : 'id'
          const put = db.prepare(`INSERT INTO ${table} (${key}, json) VALUES (?, ?) ON CONFLICT(${key}) DO UPDATE SET json=excluded.json`)
          const del = db.prepare(`DELETE FROM ${table} WHERE ${key} = ?`)
          for (const id of previous[table].keys()) if (!next[table].has(id)) del.run(id)
          for (const [id, row] of next[table]) {
            const old = previous[table].get(id)
            if (old?.json === row.json) continue
            put.run(id, row.json)
          }
        }
        if (!isDeepStrictEqual(readDatabase(db), state)) throw Error('记忆写入校验失败。')
        db.exec('COMMIT')
      } catch (error) {
        try { db.exec('ROLLBACK') } catch {}
        throw error
      }
      if (fresh && db.prepare('PRAGMA wal_checkpoint(TRUNCATE)').get().busy) throw Error('初次迁移仍有未写入的记忆。')
    } finally { db.close() }
    if (fresh) renameSync(target, file)
  }
  return { load, save }
}
