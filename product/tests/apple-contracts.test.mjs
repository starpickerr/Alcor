import test from 'node:test'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { fileURLToPath } from 'node:url'
const run = promisify(execFile)
test('Apple compatibility preserves data across permissions, accounts, conflicts, recurrence and incremental ranges', async () => {
  await run(process.env.ALCOR_BUSINESS_PYTHON || process.env.STELLA_BUSINESS_PYTHON || 'python3',[fileURLToPath(new URL('./apple-contracts.py',import.meta.url))])
})
test('native Apple fields and durable receipts retain public data without requesting authorization',{skip:process.platform!=='darwin'},async () => {
  await run(process.env.ALCOR_BUSINESS_PYTHON || process.env.STELLA_BUSINESS_PYTHON || 'python3',[fileURLToPath(new URL('./apple-native-fields.py',import.meta.url))],{timeout:60000})
})
test('Apple reminder alarm intent and completion bridge regressions remain isolated', async () => {
  await run(process.env.ALCOR_BUSINESS_PYTHON || process.env.STELLA_BUSINESS_PYTHON || 'python3',[fileURLToPath(new URL('./apple-reminder-regressions.py',import.meta.url))])
})

for (const suite of [
  'apple-link-replacement-lock', 'apple-approved-recurring-delete', 'apple-approved-delete-run', 'apple-owner-approved-delete',
  'apple-delete-crash-recovery', 'apple-confirmed-delete-continuation', 'apple-delete-state-messages',
  'apple-owner-finish-verification', 'apple-retained-conflict-successor', 'apple-import-unlink-recovery',
  'apple-reminder-identity-candidates', 'apple-calendar-identity-candidates', 'apple-category-source-reconnect',
]) {
  test(suite, async () => {
    await run(process.env.ALCOR_BUSINESS_PYTHON || process.env.STELLA_BUSINESS_PYTHON || 'python3',
      ['-B', fileURLToPath(new URL('./' + suite + '.py', import.meta.url))], { timeout: 60000 })
  })
}
