/** Shared names and completion-marker contract for the Alcor Profile migration. */
export const MIGRATION_PACKAGE_NAMES = Object.freeze(['secretary', 'organizer', 'apple-sync', 'elearning', 'mail', 'privacy', 'agent-hub'])
export const MIGRATION_BUNDLE_NAMES = Object.freeze(['secretary', 'apple-sync', 'elearning', 'mail', 'privacy', 'agent-hub'])
export const MIGRATION_BUNDLES = Object.freeze(MIGRATION_BUNDLE_NAMES.map(name => Object.freeze({ name, old: `@stella/${name}`, current: `@alcor/${name}` })))
export const MIGRATION_DIRECTORIES = Object.freeze({ business: 'business', appleSync: 'apple-sync', email: 'email', elearning: 'elearning' })
export const MIGRATION_DOMAINS = Object.freeze({ organizer: Object.freeze({ old: 'stella_organizer', current: 'alcor_organizer' }), secretary: Object.freeze({ old: 'stella_secretary', current: 'alcor_secretary' }) })
export const MIGRATION_CREDENTIALS = Object.freeze({ elearning: Object.freeze({ old: 'stella-elearning/account', current: 'alcor-elearning/account' }), email: Object.freeze({ old: 'stella-email/accounts', current: 'alcor-email/accounts' }) })
export const MIGRATION_PRESETS = Object.freeze({ old: 'stella', current: 'alcor', oldBriefing: 'stella-briefing', currentBriefing: 'alcor-briefing' })
export const MIGRATION_BRIEFING_TOOLS = Object.freeze({ old: 'stella_generate_briefing', current: 'alcor_generate_briefing' })
export const MIGRATION_CORDIS_IDS = Object.freeze(['secretary', 'organizer', 'apple-sync', 'elearning', 'mail', 'ui-layout', 'ui-workspace', 'ui-settings-general', 'ui-schedule', 'ui-settings-models'].map(name => Object.freeze({ old: `stella-${name}`, current: `alcor-${name}` })))
export const MIGRATION_MARKER = '.migration.json'

const exactKeys = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))

/** Validate the persisted marker, without treating it as proof of quiescence or backend routing. */
export function isMigrationMarker(marker) {
  if (!exactKeys(marker, ['schema', 'status', 'source', 'directories', 'domains', 'credentials', 'schedule']) || marker.schema !== 1 || marker.status !== 'complete' || !['stella', 'fresh'].includes(marker.source)) return false
  if (!exactKeys(marker.directories, Object.keys(MIGRATION_DIRECTORIES)) || !exactKeys(marker.domains, Object.keys(MIGRATION_DOMAINS)) || !exactKeys(marker.credentials, Object.keys(MIGRATION_CREDENTIALS)) || !exactKeys(marker.schedule, ['mapped']) || !Number.isSafeInteger(marker.schedule.mapped) || marker.schedule.mapped < 0) return false
  const values = [...Object.values(marker.directories), ...Object.values(marker.domains), ...Object.values(marker.credentials)]
  if (values.some(value => !['migrated', 'absent'].includes(value))) return false
  return marker.source !== 'fresh' || (values.every(value => value === 'absent') && marker.schedule.mapped === 0)
}

/** Construct a marker only when all provided completion fields satisfy the shared contract. */
export function createMigrationMarker({ source, directories, domains, credentials, schedule }) {
  const marker = { schema: 1, status: 'complete', source, directories, domains, credentials, schedule }
  if (!isMigrationMarker(marker)) throw new Error('Invalid Alcor migration completion marker')
  return marker
}
