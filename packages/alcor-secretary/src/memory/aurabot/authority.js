import { join } from 'node:path'
import { createMemoryStorage } from './storage.js'
import { parsePersona } from './card.js'

// The store and lock belong to one audience. Keep IDs, sources and delivery grants in one transaction.
export function authorityStore(dataDir) {
  const store = createMemoryStorage({ directory: join(dataDir, 'aurabot') })
  return {
    load() {
      const { items, personaCard, ...state } = store.load()
      if (state.version === undefined && !Object.keys(state).length && !items.length && !personaCard) return null
      if (personaCard !== undefined) {
        if (!state.identity || typeof personaCard !== 'string') throw Error('人格卡数据损坏。')
        state.identity.soul = parsePersona('alcor', personaCard).body
      }
      return { ...state, entries: items }
    },
    save(next) {
      const { entries: items, identity, ...state } = next
      if (identity) {
        const { soul, ...metadata } = identity
        state.identity = metadata
        state.personaCard = '---\nname: ' + identity.name.replace(/[\r\n]/g,' ') + '\ntags: []\n---\n\n' + soul
      }
      store.save(JSON.parse(JSON.stringify({ ...state, items })))
    },
  }
}
